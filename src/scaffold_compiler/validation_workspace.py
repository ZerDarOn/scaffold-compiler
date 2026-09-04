"""Run-owned validation environment creation and bounded retryable cleanup."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

LOGGER = logging.getLogger(__name__)

_MARKER_NAME: Final = ".validation-workspace.json"
_SCHEMA_VERSION: Final = 1
_RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ValidationWorkspaceError(RuntimeError):
    """Raised when validation workspace ownership cannot be established safely."""


@dataclass(frozen=True, slots=True)
class ValidationWorkspace:
    """Identity of the fixed validation child owned by one generation run."""

    workspace: Path
    root: Path
    run_id: str


@dataclass(frozen=True, slots=True)
class ValidationWorkspaceCleanupResult:
    """Result of a bounded cleanup attempt that can be retried."""

    completed: bool
    failed_entries: int = 0


def prepare_validation_workspace(workspace: Path, *, run_id: str) -> ValidationWorkspace:
    """Create the fixed validation child and persist its ownership marker."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Run ID is invalid for a validation workspace.")
    trusted_workspace = workspace.resolve(strict=True)
    if not trusted_workspace.is_dir() or _is_link_or_reparse(trusted_workspace):
        raise ValidationWorkspaceError("Generation workspace must be a real directory.")
    root = trusted_workspace / "validation-env"
    try:
        root.mkdir()
        marker = root / _MARKER_NAME
        payload = json.dumps(
            {"run_id": run_id, "schema_version": _SCHEMA_VERSION},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
        with marker.open("x", encoding="utf-8", newline="\n") as marker_file:
            marker_file.write(payload)
            marker_file.flush()
            os.fsync(marker_file.fileno())
    except Exception:
        try:
            (root / _MARKER_NAME).unlink(missing_ok=True)
            root.rmdir()
        except OSError:
            LOGGER.error("validation_workspace_prepare_cleanup_failed run_id=%s", run_id)
        raise
    LOGGER.info("validation_workspace_prepared run_id=%s", run_id)
    return ValidationWorkspace(workspace=trusted_workspace, root=root, run_id=run_id)


def cleanup_validation_workspace(
    owned: ValidationWorkspace,
    *,
    unlinker: Callable[[Path], None] | None = None,
) -> ValidationWorkspaceCleanupResult:
    """Delete only a marked fixed child after a complete no-link preflight."""
    trusted_workspace = owned.workspace.resolve(strict=True)
    expected_root = trusted_workspace / "validation-env"
    if owned.root != expected_root:
        raise ValidationWorkspaceError("Validation root is not the fixed workspace child.")
    if not expected_root.exists():
        return ValidationWorkspaceCleanupResult(completed=True)
    entries = _preflight_owned_entries(owned, expected_root)
    if entries is None:
        LOGGER.warning("validation_workspace_ownership_refused run_id=%s", owned.run_id)
        return ValidationWorkspaceCleanupResult(completed=False, failed_entries=1)

    delete_file = unlinker or Path.unlink
    marker = expected_root / _MARKER_NAME
    files = [path for path in entries if path.is_file() and path != marker]
    directories = [path for path in entries if path.is_dir()]
    failures = 0
    for path in sorted(files, key=lambda item: len(item.parts), reverse=True):
        try:
            delete_file(path)
        except OSError:
            failures += 1
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            failures += 1
    if failures == 0:
        try:
            delete_file(marker)
            expected_root.rmdir()
        except OSError:
            failures += 1

    completed = not expected_root.exists()
    if completed:
        LOGGER.info("validation_workspace_cleanup_completed run_id=%s", owned.run_id)
    else:
        LOGGER.warning(
            "validation_workspace_cleanup_incomplete run_id=%s failures=%d",
            owned.run_id,
            failures,
        )
    return ValidationWorkspaceCleanupResult(completed=completed, failed_entries=failures)


def _preflight_owned_entries(
    owned: ValidationWorkspace,
    root: Path,
) -> list[Path] | None:
    if _is_link_or_reparse(root):
        return None
    marker = root / _MARKER_NAME
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record != {"run_id": owned.run_id, "schema_version": _SCHEMA_VERSION}:
            return None
        entries = list(root.rglob("*"))
        if any(_is_link_or_reparse(path) for path in entries):
            return None
        if any(not path.is_file() and not path.is_dir() for path in entries):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    return entries


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)
