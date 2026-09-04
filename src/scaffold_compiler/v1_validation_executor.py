"""Fixed V1 validation execution with explicit unsupported-gate semantics."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
    check_from_process_result,
    run_controlled_process,
    scan_candidate_static_safety,
    skipped_validation_check,
)

LOGGER = logging.getLogger(__name__)

_INSTALL_TIMEOUT_SECONDS: Final = 300.0
_CHECK_TIMEOUT_SECONDS: Final = 180.0
_OUTPUT_LIMIT_BYTES: Final = 65_536
_CODE_VALIDATION_ARGUMENTS: Final = {
    "python-syntax": ("run", "python", "-m", "compileall", "-q", "src", "tests"),
    "ruff-format": ("run", "ruff", "format", "--check", "."),
    "ruff-lint": ("run", "ruff", "check", "."),
    "mypy": ("run", "mypy"),
    "pytest": ("run", "pytest"),
}

ValidationProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_v1_validation(
    candidate: CandidateAssemblyResult,
    configuration_digest: str,
    blueprint_digest: str,
    required_validations: tuple[str, ...],
    *,
    uv_executable: Path,
    forbidden_absolute_paths: tuple[Path, ...],
    secrets: tuple[str, ...] = (),
    process_runner: ValidationProcessRunner = run_controlled_process,
) -> ValidationReport:
    """Run supported fixed checks and mark every unavailable required gate as skipped."""
    resolved_uv = uv_executable.resolve(strict=True)
    if not resolved_uv.is_file():
        raise ValueError("uv executable must be an existing ordinary file.")
    static_check = scan_candidate_static_safety(
        candidate,
        forbidden_absolute_paths=forbidden_absolute_paths,
        secrets=secrets,
    )
    checks: list[ValidationCheck] = [static_check]
    LOGGER.info(
        "v1_validation_started required=%d candidate_digest=%s",
        len(required_validations),
        candidate.digest,
    )
    if static_check.status is ValidationStatus.PASS:
        install_result = process_runner(
            _process_specification(
                "locked-install",
                resolved_uv,
                ("sync", "--frozen"),
                candidate.root,
                _INSTALL_TIMEOUT_SECONDS,
                secrets,
            )
        )
        install_check = check_from_process_result(
            "locked-install",
            ValidationPhase.INSTALL,
            required=True,
            result=install_result,
        )
    else:
        install_check = skipped_validation_check(
            "locked-install",
            ValidationPhase.INSTALL,
            required=True,
            reason="Static safety validation did not pass.",
        )
    checks.append(install_check)

    for name in required_validations:
        arguments = _CODE_VALIDATION_ARGUMENTS.get(name)
        if arguments is None:
            checks.append(
                skipped_validation_check(
                    name,
                    _phase_for_unavailable_check(name),
                    required=True,
                    reason="This required runtime or delivery gate is not connected yet.",
                )
            )
            continue
        if install_check.status is not ValidationStatus.PASS:
            checks.append(
                skipped_validation_check(
                    name,
                    ValidationPhase.QUALITY,
                    required=True,
                    reason="Locked dependency installation did not pass.",
                )
            )
            continue
        result = process_runner(
            _process_specification(
                name,
                resolved_uv,
                arguments,
                candidate.root,
                _CHECK_TIMEOUT_SECONDS,
                secrets,
            )
        )
        checks.append(
            check_from_process_result(
                name,
                ValidationPhase.QUALITY,
                required=True,
                result=result,
            )
        )

    report = ValidationReport(
        configuration_digest=configuration_digest,
        blueprint_digest=blueprint_digest,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        checks=tuple(checks),
    )
    incomplete = sum(
        check.required and check.status is not ValidationStatus.PASS for check in report.checks
    )
    LOGGER.info(
        "v1_validation_completed checks=%d incomplete=%d candidate_digest=%s",
        len(report.checks),
        incomplete,
        candidate.digest,
    )
    return report


def _process_specification(
    name: str,
    uv_executable: Path,
    arguments: tuple[str, ...],
    candidate_root: Path,
    timeout_seconds: float,
    secrets: tuple[str, ...],
) -> ControlledProcessSpec:
    return ControlledProcessSpec(
        name=name,
        argv=(str(uv_executable), *arguments),
        cwd=candidate_root,
        timeout_seconds=timeout_seconds,
        output_limit_bytes=_OUTPUT_LIMIT_BYTES,
        secrets=secrets,
    )


def _phase_for_unavailable_check(name: str) -> ValidationPhase:
    if name.startswith("docker") or name.startswith("container") or name.startswith("compose"):
        return ValidationPhase.DELIVERY
    return ValidationPhase.RUNTIME
