"""Bounded validation for generated Go CLI projects."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    verify_candidate_digest,
)
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
    check_from_process_result,
    run_controlled_process,
    skipped_validation_check,
)

LOGGER = logging.getLogger(__name__)

GO_VALIDATION_REPORT_GATES: Final = (
    "go-format",
    "go-test",
    "go-build",
    "go-executable-run",
)
_GO_SOURCE_FILES: Final = ("calculator.go", "calculator_test.go", "main.go")
_EXPECTED_OUTPUT: Final = "scaffold compiler go example"
_BINARY_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_OUTPUT_LIMIT_BYTES: Final = 65_536
_TIMEOUTS: Final = {
    "go-format": 30.0,
    "go-test": 120.0,
    "go-build": 120.0,
    "go-executable-run": 30.0,
}

ValidationProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_go_validation(
    candidate: CandidateAssemblyResult,
    configuration_digest: str,
    blueprint_digest: str,
    required_validations: tuple[str, ...],
    *,
    binary_name: str,
    go_executable: Path | None,
    gofmt_executable: Path | None,
    validation_environment: Path,
    process_runner: ValidationProcessRunner = run_controlled_process,
) -> ValidationReport:
    """Format-check, test, build, and run Go without writing into the candidate."""
    _validate_inputs(candidate, required_validations, binary_name, validation_environment)
    resolved_go = _resolve_tool(go_executable, "Go")
    resolved_gofmt = _resolve_tool(gofmt_executable, "gofmt")
    candidate_root = candidate.root.resolve(strict=True)
    environment_root = validation_environment.resolve(strict=False)
    ordered = tuple(name for name in GO_VALIDATION_REPORT_GATES if name in required_validations)
    checks: list[ValidationCheck] = []
    blocked = False
    LOGGER.info(
        "go_validation_started required=%d candidate_digest=%s", len(ordered), candidate.digest
    )
    for name in ordered:
        unavailable = _unavailable_reason(name, resolved_go, resolved_gofmt)
        if blocked:
            check = skipped_validation_check(
                name,
                _phase_for(name),
                required=True,
                reason="A prerequisite Go validation gate did not pass.",
            )
        elif unavailable:
            check = skipped_validation_check(
                name, _phase_for(name), required=True, reason=unavailable
            )
        else:
            specification = _process_specification(
                name,
                candidate_root,
                environment_root,
                binary_name,
                resolved_go,
                resolved_gofmt,
            )
            result = process_runner(specification)
            verify_candidate_digest(candidate)
            check = _check_from_result(name, result)
        checks.append(check)
        blocked = check.status is not ValidationStatus.PASS
    verify_candidate_digest(candidate)
    report = ValidationReport(
        configuration_digest=configuration_digest,
        blueprint_digest=blueprint_digest,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        checks=tuple(checks),
    )
    LOGGER.info(
        "go_validation_completed checks=%d incomplete=%d candidate_digest=%s",
        len(checks),
        sum(check.status is not ValidationStatus.PASS for check in checks),
        candidate.digest,
    )
    return report


def _validate_inputs(
    candidate: CandidateAssemblyResult,
    required: tuple[str, ...],
    binary_name: str,
    validation_environment: Path,
) -> None:
    if not _BINARY_NAME_PATTERN.fullmatch(binary_name):
        raise ValueError("Go binary name is invalid for runtime validation.")
    if (
        len(required) != len(set(required))
        or not required
        or any(name not in GO_VALIDATION_REPORT_GATES for name in required)
    ):
        raise ValueError("Go required validation gates are invalid.")
    if not validation_environment.is_absolute() or validation_environment.is_symlink():
        raise ValueError("Go validation environment path is invalid.")
    resolved = validation_environment.resolve(strict=False)
    if resolved.is_relative_to(candidate.root.resolve(strict=True)):
        raise ValueError("Go validation environment must be outside the candidate.")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("Go validation environment must be a directory when it exists.")
    if not resolved.parent.is_dir():
        raise ValueError("Go validation environment parent must exist.")
    verify_candidate_digest(candidate)


def _resolve_tool(executable: Path | None, name: str) -> Path | None:
    if executable is None:
        return None
    try:
        resolved = executable.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{name} executable must be an existing ordinary file.") from error
    if not resolved.is_file():
        raise ValueError(f"{name} executable must be an existing ordinary file.")
    return resolved


def _unavailable_reason(name: str, go: Path | None, gofmt: Path | None) -> str | None:
    if name == "go-format" and gofmt is None:
        return "gofmt is unavailable in this validation runtime."
    if name != "go-format" and go is None:
        return "Go is unavailable in this validation runtime."
    return None


def _process_specification(
    name: str,
    candidate_root: Path,
    environment_root: Path,
    binary_name: str,
    go: Path | None,
    gofmt: Path | None,
) -> ControlledProcessSpec:
    executable = environment_root / "build" / f"{binary_name}{'.exe' if os.name == 'nt' else ''}"
    temporary_root = environment_root / "go-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    if name == "go-format" and gofmt is not None:
        argv = (str(gofmt), "-l", *(str(candidate_root / item) for item in _GO_SOURCE_FILES))
    elif name == "go-test" and go is not None:
        argv = (str(go), "test", "./...")
    elif name == "go-build" and go is not None:
        executable.parent.mkdir(parents=True, exist_ok=True)
        argv = (str(go), "build", "-o", str(executable), ".")
    elif name == "go-executable-run":
        argv = (str(executable),)
    else:
        raise ValueError("Go validation command is unavailable.")
    environment = (
        ("CGO_ENABLED", "0"),
        ("GOCACHE", str(environment_root / "go-cache")),
        ("GOMODCACHE", str(environment_root / "module-cache")),
        ("GOTMPDIR", str(temporary_root)),
        ("GOTOOLCHAIN", "local"),
        ("GOWORK", "off"),
    )
    return ControlledProcessSpec(
        name=name,
        argv=argv,
        cwd=candidate_root,
        timeout_seconds=_TIMEOUTS[name],
        output_limit_bytes=_OUTPUT_LIMIT_BYTES,
        environment=environment,
    )


def _check_from_result(name: str, result: ControlledProcessResult) -> ValidationCheck:
    check = check_from_process_result(name, _phase_for(name), required=True, result=result)
    if result.output_truncated:
        return ValidationCheck(
            name,
            ValidationStatus.FAIL,
            required=True,
            phase=_phase_for(name),
            diagnostics="Validation process exceeded its output limit.",
        )
    if name == "go-format" and check.status is ValidationStatus.PASS and result.stdout.strip():
        return ValidationCheck(
            name,
            ValidationStatus.FAIL,
            required=True,
            phase=ValidationPhase.QUALITY,
            diagnostics="Generated Go source is not formatted.",
        )
    if (
        name == "go-executable-run"
        and check.status is ValidationStatus.PASS
        and result.stdout.splitlines() != [_EXPECTED_OUTPUT]
    ):
        return ValidationCheck(
            name,
            ValidationStatus.FAIL,
            required=True,
            phase=ValidationPhase.RUNTIME,
            diagnostics="Generated Go executable output did not match the recipe contract.",
        )
    return check


def _phase_for(name: str) -> ValidationPhase:
    return ValidationPhase.RUNTIME if name == "go-executable-run" else ValidationPhase.QUALITY
