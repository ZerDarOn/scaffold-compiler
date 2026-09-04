"""Target-scoped exclusive locks with conservative explicit stale recovery."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from scaffold_compiler.path_safety import resolve_safe_output_path

LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION: Final = 1
_RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class TargetLockAlreadyHeldError(RuntimeError):
    """Raised when another session already owns the target lock path."""


class TargetLockRecoveryState(StrEnum):
    """Conservative outcomes for an explicit stale-lock audit."""

    ACTIVE = "active"
    RECOVERED = "recovered"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TargetLock:
    """The exact lock bytes and identity held by one generation process."""

    lock_path: Path
    target_path: Path
    run_id: str
    pid: int
    process_start_token: str
    nonce: str
    serialized: str


class TargetLockStore:
    """Acquire and release one lock path derived only from a trusted target."""

    def __init__(self, target_path: Path) -> None:
        if not target_path.is_absolute():
            raise ValueError("Target path must be absolute.")
        parent = target_path.parent.resolve(strict=True)
        canonical_target = resolve_safe_output_path(parent, target_path.name)
        if canonical_target != target_path:
            raise ValueError("Target path spelling is not canonical.")
        self._target_path = target_path
        self._lock_path = resolve_safe_output_path(
            parent,
            f".{target_path.name}.scaffold.lock",
        )

    def acquire(self, *, run_id: str, process_start_token: str) -> TargetLock:
        """Atomically create the target lock and durably write its owner identity."""
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Run ID is invalid for a target lock.")
        if not process_start_token or "\0" in process_start_token:
            raise ValueError("Process start token is invalid.")
        if self._target_path.exists() or self._target_path.is_symlink():
            raise ValueError("Target already exists and cannot be locked for generation.")
        pid = os.getpid()
        nonce = secrets.token_hex(16)
        record = {
            "nonce": nonce,
            "pid": pid,
            "process_start_token": process_start_token,
            "run_id": run_id,
            "schema_version": _SCHEMA_VERSION,
            "target_name": self._target_path.name,
        }
        serialized = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        created = False
        try:
            with self._lock_path.open("x", encoding="utf-8", newline="\n") as lock_file:
                created = True
                lock_file.write(serialized)
                lock_file.flush()
                os.fsync(lock_file.fileno())
        except FileExistsError as error:
            raise TargetLockAlreadyHeldError("Target lock is already held.") from error
        except OSError:
            if created:
                self._lock_path.unlink(missing_ok=True)
            raise
        lock = TargetLock(
            lock_path=self._lock_path,
            target_path=self._target_path,
            run_id=run_id,
            pid=pid,
            process_start_token=process_start_token,
            nonce=nonce,
            serialized=serialized,
        )
        LOGGER.info(
            "target_lock_acquired run_id=%s pid=%d target_name=%s",
            lock.run_id,
            lock.pid,
            lock.target_path.name,
        )
        return lock

    def release(self, lock: TargetLock) -> None:
        """Delete only an unchanged lock created by this exact store and owner."""
        if lock.lock_path != self._lock_path or lock.target_path != self._target_path:
            raise ValueError("Target lock does not belong to this store.")
        current = self._read_lock_text()
        if not hmac.compare_digest(current, lock.serialized):
            raise ValueError("Target lock ownership bytes no longer match.")
        self._lock_path.unlink()
        LOGGER.info(
            "target_lock_released run_id=%s pid=%d target_name=%s",
            lock.run_id,
            lock.pid,
            lock.target_path.name,
        )

    def recover_stale(
        self,
        *,
        explicit: bool,
        process_is_alive: Callable[[int, str], bool],
    ) -> TargetLockRecoveryState:
        """Recover only a well-formed dead owner's unchanged lock with no target."""
        if not explicit:
            raise ValueError("Stale lock recovery requires an explicit request.")
        try:
            serialized = self._read_lock_text()
            record = _parse_record(serialized, expected_target_name=self._target_path.name)
        except (OSError, ValueError):
            return TargetLockRecoveryState.AMBIGUOUS
        if self._target_path.exists() or self._target_path.is_symlink():
            return TargetLockRecoveryState.AMBIGUOUS
        try:
            if process_is_alive(record.pid, record.process_start_token):
                return TargetLockRecoveryState.ACTIVE
        except OSError:
            return TargetLockRecoveryState.AMBIGUOUS
        try:
            current = self._read_lock_text()
            if not hmac.compare_digest(current, serialized):
                return TargetLockRecoveryState.AMBIGUOUS
            if self._target_path.exists() or self._target_path.is_symlink():
                return TargetLockRecoveryState.AMBIGUOUS
            self._lock_path.unlink()
        except OSError:
            return TargetLockRecoveryState.AMBIGUOUS
        LOGGER.info(
            "stale_target_lock_recovered run_id=%s pid=%d target_name=%s",
            record.run_id,
            record.pid,
            self._target_path.name,
        )
        return TargetLockRecoveryState.RECOVERED

    def _read_lock_text(self) -> str:
        safe_lock_path = resolve_safe_output_path(self._lock_path.parent, self._lock_path.name)
        return safe_lock_path.read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _ParsedTargetLock:
    run_id: str
    pid: int
    process_start_token: str


def _parse_record(serialized: str, *, expected_target_name: str) -> _ParsedTargetLock:
    try:
        record = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ValueError("Target lock JSON is invalid.") from error
    expected_fields = {
        "nonce",
        "pid",
        "process_start_token",
        "run_id",
        "schema_version",
        "target_name",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise ValueError("Target lock fields are invalid.")
    if record["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("Target lock schema is unsupported.")
    run_id = record["run_id"]
    pid = record["pid"]
    process_start_token = record["process_start_token"]
    nonce = record["nonce"]
    target_name = record["target_name"]
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Target lock run ID is invalid.")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("Target lock PID is invalid.")
    if not isinstance(process_start_token, str) or not process_start_token:
        raise ValueError("Target lock process token is invalid.")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("Target lock nonce is invalid.")
    if target_name != expected_target_name:
        raise ValueError("Target lock names a different target.")
    return _ParsedTargetLock(
        run_id=run_id,
        pid=pid,
        process_start_token=process_start_token,
    )
