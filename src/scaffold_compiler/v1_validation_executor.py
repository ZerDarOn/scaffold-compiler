"""Fixed V1 validation execution with explicit unsupported-gate semantics."""

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
_HTTP_VALIDATION_PATHS: Final = {
    "application-start": "/api/v1",
    "health-live": "/health/live",
    "health-ready": "/health/ready",
    "openapi": "/openapi.json",
}
_HTTP_CHECK_CODE: Final = """import importlib
import sys
from fastapi.testclient import TestClient

module = importlib.import_module(sys.argv[1])
application = module.application
with TestClient(application) as client:
    response = client.get(sys.argv[2])
    if response.status_code != 200:
        raise SystemExit(1)
    if sys.argv[2] == "/openapi.json" and not response.json().get("openapi"):
        raise SystemExit(1)
"""
_PACKAGE_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")

ValidationProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_v1_validation(
    candidate: CandidateAssemblyResult,
    configuration_digest: str,
    blueprint_digest: str,
    required_validations: tuple[str, ...],
    *,
    package_name: str,
    uv_executable: Path,
    validation_environment: Path,
    forbidden_absolute_paths: tuple[Path, ...],
    secrets: tuple[str, ...] = (),
    process_runner: ValidationProcessRunner = run_controlled_process,
) -> ValidationReport:
    """Run supported fixed checks and mark every unavailable required gate as skipped."""
    if not _PACKAGE_NAME_PATTERN.fullmatch(package_name):
        raise ValueError("Package name is invalid for runtime validation.")
    if not validation_environment.is_absolute():
        raise ValueError("Validation environment path must be absolute.")
    resolved_validation_environment = validation_environment.resolve(strict=False)
    if validation_environment.is_symlink():
        raise ValueError("Validation environment cannot be a symbolic link.")
    if _path_is_within(resolved_validation_environment, candidate.root.resolve(strict=True)):
        raise ValueError("Validation environment must be outside the candidate project.")
    if resolved_validation_environment.exists() and not resolved_validation_environment.is_dir():
        raise ValueError("Validation environment path must be a directory when it exists.")
    if not resolved_validation_environment.parent.is_dir():
        raise ValueError("Validation environment parent must already exist.")
    process_environment = (("UV_PROJECT_ENVIRONMENT", str(resolved_validation_environment)),)
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
                process_environment,
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
        validation = _validation_command(name, package_name)
        if validation is None:
            checks.append(
                skipped_validation_check(
                    name,
                    _phase_for_unavailable_check(name),
                    required=True,
                    reason="This required runtime or delivery gate is not connected yet.",
                )
            )
            continue
        arguments, phase = validation
        if install_check.status is not ValidationStatus.PASS:
            checks.append(
                skipped_validation_check(
                    name,
                    phase,
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
                process_environment,
            )
        )
        checks.append(
            check_from_process_result(
                name,
                phase,
                required=True,
                result=result,
            )
        )

    verify_candidate_digest(candidate)
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
    environment: tuple[tuple[str, str], ...],
) -> ControlledProcessSpec:
    return ControlledProcessSpec(
        name=name,
        argv=(str(uv_executable), *arguments),
        cwd=candidate_root,
        timeout_seconds=timeout_seconds,
        output_limit_bytes=_OUTPUT_LIMIT_BYTES,
        secrets=secrets,
        environment=environment,
    )


def _phase_for_unavailable_check(name: str) -> ValidationPhase:
    if name.startswith("docker") or name.startswith("container") or name.startswith("compose"):
        return ValidationPhase.DELIVERY
    return ValidationPhase.RUNTIME


def _validation_command(
    name: str,
    package_name: str,
) -> tuple[tuple[str, ...], ValidationPhase] | None:
    code_arguments = _CODE_VALIDATION_ARGUMENTS.get(name)
    if code_arguments is not None:
        return code_arguments, ValidationPhase.QUALITY
    endpoint = _HTTP_VALIDATION_PATHS.get(name)
    if endpoint is not None:
        return (
            ("run", "python", "-c", _HTTP_CHECK_CODE, f"{package_name}.asgi", endpoint),
            ValidationPhase.RUNTIME,
        )
    return None


def _path_is_within(path: Path, parent: Path) -> bool:
    normalized_path = os.path.normcase(str(path.absolute()))
    normalized_parent = os.path.normcase(str(parent.absolute()))
    try:
        return os.path.commonpath((normalized_path, normalized_parent)) == normalized_parent
    except ValueError:
        return False
