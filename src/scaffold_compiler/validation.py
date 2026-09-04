"""Controlled validation processes, static safety scans, and bound credentials."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Final

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    verify_candidate_digest,
)

LOGGER = logging.getLogger(__name__)

_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_CHECK_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]*$")
_ENVIRONMENT_NAME_PATTERN: Final = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_TRUNCATION_MARKER: Final = "\n[output truncated]"


class ValidationStatus(StrEnum):
    """The only states exposed by an individual validation check."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class ValidationPhase(StrEnum):
    """Stable phases used to order and explain validation work."""

    STATIC = "static"
    INSTALL = "install"
    QUALITY = "quality"
    RUNTIME = "runtime"
    DELIVERY = "delivery"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class ControlledProcessSpec:
    """A shell-free process with mandatory resource bounds."""

    name: str
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    output_limit_bytes: int = 65_536
    secrets: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _CHECK_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("Process name must be a stable lowercase identifier.")
        if not self.argv or any(
            not isinstance(argument, str) or not argument or "\0" in argument
            for argument in self.argv
        ):
            raise ValueError("Process arguments must be non-empty strings without NUL bytes.")
        if not self.cwd.is_absolute() or not self.cwd.is_dir():
            raise ValueError("Process working directory must be an existing absolute directory.")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Process timeout must be positive.")
        if isinstance(self.output_limit_bytes, bool) or self.output_limit_bytes <= 0:
            raise ValueError("Process output limit must be positive.")
        if any(not secret for secret in self.secrets):
            raise ValueError("Redaction secrets cannot be empty.")
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            for item in self.environment
        ):
            raise ValueError("Process environment overrides must be string pairs.")
        environment_names = tuple(name for name, _ in self.environment)
        if len(set(environment_names)) != len(environment_names):
            raise ValueError("Process environment overrides contain duplicate names.")
        if any(
            not _ENVIRONMENT_NAME_PATTERN.fullmatch(name) or "\0" in value
            for name, value in self.environment
        ):
            raise ValueError("Process environment override is invalid.")


@dataclass(frozen=True, slots=True)
class ControlledProcessResult:
    """Bounded and redacted facts from one external command."""

    return_code: int
    timed_out: bool
    stdout: str
    stderr: str
    output_truncated: bool
    elapsed_milliseconds: int


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One deterministic validation outcome."""

    name: str
    status: ValidationStatus
    required: bool
    phase: ValidationPhase = ValidationPhase.STATIC
    diagnostics: str = ""

    def __post_init__(self) -> None:
        if not _CHECK_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("Validation check name is invalid.")
        if any(character in self.diagnostics for character in "\0"):
            raise ValueError("Validation diagnostics contain a NUL byte.")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Ordered validation facts bound to every upstream immutable digest."""

    configuration_digest: str
    blueprint_digest: str
    plan_digest: str
    candidate_digest: str
    checks: tuple[ValidationCheck, ...]

    def __post_init__(self) -> None:
        for name, digest in (
            ("configuration", self.configuration_digest),
            ("blueprint", self.blueprint_digest),
            ("plan", self.plan_digest),
            ("candidate", self.candidate_digest),
        ):
            _validate_digest(digest, name=name)
        check_names = tuple(check.name for check in self.checks)
        if not self.checks:
            raise ValueError("Validation report requires at least one check.")
        if len(set(check_names)) != len(check_names):
            raise ValueError("Validation check names must be unique.")

    @property
    def digest(self) -> str:
        """Hash a canonical report without host paths or timing noise."""
        payload = {
            "blueprint_digest": self.blueprint_digest,
            "candidate_digest": self.candidate_digest,
            "checks": [
                {
                    "diagnostics": check.diagnostics,
                    "name": check.name,
                    "phase": check.phase.value,
                    "required": check.required,
                    "status": check.status.value,
                }
                for check in self.checks
            ],
            "configuration_digest": self.configuration_digest,
            "plan_digest": self.plan_digest,
            "schema_version": 1,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(serialized).hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationCredential:
    """Proof that all required checks passed for the exact candidate."""

    configuration_digest: str
    blueprint_digest: str
    plan_digest: str
    candidate_digest: str
    verification_digest: str


def run_controlled_process(specification: ControlledProcessSpec) -> ControlledProcessResult:
    """Run an argument array, draining bounded output and killing its tree on timeout."""
    LOGGER.info(
        "validation_process_started name=%s cwd_name=%s timeout_seconds=%s",
        specification.name,
        _redact_text(specification.cwd.name, specification.secrets),
        specification.timeout_seconds,
    )
    started = time.monotonic()
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    environment = os.environ.copy()
    environment.update(specification.environment)
    process = subprocess.Popen(
        specification.argv,
        cwd=specification.cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    stdout_truncated = [False]
    stderr_truncated = [False]
    redaction_margin = max(
        (len(secret.encode("utf-8")) for secret in specification.secrets),
        default=0,
    )
    capture_limit = specification.output_limit_bytes + redaction_margin
    readers = (
        threading.Thread(
            target=_drain_bounded_stream,
            args=(
                process.stdout,
                stdout,
                capture_limit,
                specification.output_limit_bytes,
                stdout_truncated,
            ),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_bounded_stream,
            args=(
                process.stderr,
                stderr,
                capture_limit,
                specification.output_limit_bytes,
                stderr_truncated,
            ),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        process.wait(timeout=specification.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        LOGGER.warning("validation_process_timeout name=%s", specification.name)
        _terminate_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    finally:
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()

    output_truncated = stdout_truncated[0] or stderr_truncated[0]
    stdout_text = _decode_redacted_output(
        stdout,
        secrets=specification.secrets,
        output_limit_bytes=specification.output_limit_bytes,
        truncated=stdout_truncated[0],
    )
    stderr_text = _decode_redacted_output(
        stderr,
        secrets=specification.secrets,
        output_limit_bytes=specification.output_limit_bytes,
        truncated=stderr_truncated[0],
    )
    elapsed_milliseconds = max(0, round((time.monotonic() - started) * 1000))
    LOGGER.info(
        "validation_process_completed name=%s return_code=%d timed_out=%s "
        "output_truncated=%s elapsed_milliseconds=%d",
        specification.name,
        process.returncode,
        timed_out,
        output_truncated,
        elapsed_milliseconds,
    )
    return ControlledProcessResult(
        return_code=process.returncode,
        timed_out=timed_out,
        stdout=stdout_text,
        stderr=stderr_text,
        output_truncated=output_truncated,
        elapsed_milliseconds=elapsed_milliseconds,
    )


def scan_candidate_static_safety(
    candidate: CandidateAssemblyResult,
    *,
    forbidden_absolute_paths: tuple[Path, ...],
    secrets: tuple[str, ...],
) -> ValidationCheck:
    """Reject unresolved placeholders, generation paths, secrets, or changed bytes."""
    if any(not path.is_absolute() for path in forbidden_absolute_paths):
        raise ValueError("Forbidden generation paths must be absolute.")
    if any(not secret for secret in secrets):
        raise ValueError("Static scan secrets cannot be empty.")
    try:
        verify_candidate_digest(candidate)
        for record in candidate.files:
            content = (candidate.root / record.path).read_bytes()
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if "${" in text:
                return ValidationCheck(
                    "static-safety",
                    ValidationStatus.FAIL,
                    required=True,
                    diagnostics="Candidate contains an unresolved template placeholder.",
                )
            if _contains_forbidden_path(text, forbidden_absolute_paths):
                return ValidationCheck(
                    "static-safety",
                    ValidationStatus.FAIL,
                    required=True,
                    diagnostics="Candidate references a generation-time absolute path.",
                )
            if any(secret in text for secret in secrets):
                return ValidationCheck(
                    "static-safety",
                    ValidationStatus.FAIL,
                    required=True,
                    diagnostics="Candidate contains a configured secret value.",
                )
        verify_candidate_digest(candidate)
    except (CandidateChangedError, OSError):
        return ValidationCheck(
            "static-safety",
            ValidationStatus.FAIL,
            required=True,
            diagnostics="Candidate bytes changed during static validation.",
        )
    return ValidationCheck("static-safety", ValidationStatus.PASS, required=True)


def issue_verification_credential(
    report: ValidationReport,
    *,
    current_candidate_digest: str,
) -> VerificationCredential:
    """Issue a credential only for unchanged candidates with all required checks passing."""
    _validate_digest(current_candidate_digest, name="current candidate")
    if current_candidate_digest != report.candidate_digest:
        raise ValueError("Candidate changed after validation.")
    incomplete = tuple(
        check.name
        for check in report.checks
        if check.required and check.status is not ValidationStatus.PASS
    )
    if incomplete:
        raise ValueError("All required validation checks must pass.")
    return VerificationCredential(
        configuration_digest=report.configuration_digest,
        blueprint_digest=report.blueprint_digest,
        plan_digest=report.plan_digest,
        candidate_digest=report.candidate_digest,
        verification_digest=report.digest,
    )


def check_from_process_result(
    name: str,
    phase: ValidationPhase,
    *,
    required: bool,
    result: ControlledProcessResult,
) -> ValidationCheck:
    """Convert controlled process facts into an explicit validation status."""
    diagnostics = ""
    if result.timed_out:
        status = ValidationStatus.FAIL
        diagnostics = "Validation process exceeded its timeout."
    elif result.return_code != 0:
        status = ValidationStatus.FAIL
        diagnostics = f"Validation process exited with code {result.return_code}."
    else:
        status = ValidationStatus.PASS
    if result.output_truncated:
        diagnostics = f"{diagnostics} Process output was truncated.".strip()
    return ValidationCheck(
        name=name,
        status=status,
        required=required,
        phase=phase,
        diagnostics=diagnostics,
    )


def skipped_validation_check(
    name: str,
    phase: ValidationPhase,
    *,
    required: bool,
    reason: str,
) -> ValidationCheck:
    """Represent an unavailable check without falsely treating it as successful."""
    if not reason.strip():
        raise ValueError("A skipped validation check requires a reason.")
    return ValidationCheck(
        name=name,
        status=ValidationStatus.SKIPPED,
        required=required,
        phase=phase,
        diagnostics=reason,
    )


def _drain_bounded_stream(
    stream: BinaryIO,
    destination: bytearray,
    capture_limit: int,
    visible_limit: int,
    truncated: list[bool],
) -> None:
    bytes_seen = 0
    while chunk := stream.read(8192):
        bytes_seen += len(chunk)
        if bytes_seen > visible_limit:
            truncated[0] = True
        remaining = capture_limit - len(destination)
        if remaining > 0:
            destination.extend(chunk[:remaining])


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        completed = subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            return
    else:
        try:
            kill_process_group = vars(os)["killpg"]
            kill_signal = vars(signal)["SIGKILL"]
            kill_process_group(process.pid, kill_signal)
            return
        except ProcessLookupError:
            return
    process.kill()


def _decode_redacted_output(
    output: bytearray,
    *,
    secrets: tuple[str, ...],
    output_limit_bytes: int,
    truncated: bool,
) -> str:
    sanitized = bytes(output)
    for secret in sorted(set(secrets), key=len, reverse=True):
        sanitized = sanitized.replace(secret.encode("utf-8"), b"[REDACTED]")
    text = sanitized[:output_limit_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += _TRUNCATION_MARKER
    return text


def _redact_text(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def _contains_forbidden_path(text: str, paths: tuple[Path, ...]) -> bool:
    normalized_text = text.casefold()
    for path in paths:
        variants = {str(path), path.as_posix(), str(path).replace("\\", "/")}
        if any(variant.casefold() in normalized_text for variant in variants):
            return True
    return False


def _validate_digest(digest: str, *, name: str) -> None:
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ValueError(f"{name} digest must be a lowercase SHA-256 value.")
