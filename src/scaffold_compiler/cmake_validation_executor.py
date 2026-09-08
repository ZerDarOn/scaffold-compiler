"""Bounded validation for generated CMake projects."""

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

CMAKE_VALIDATION_REPORT_GATES: Final = (
    "cmake-configure",
    "cmake-build",
    "ctest",
    "executable-run",
)

_TARGET_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_OUTPUT_LIMIT_BYTES: Final = 65_536
_EXPECTED_EXECUTABLE_OUTPUT: Final = "scaffold compiler c example"
_TIMEOUTS: Final = {
    "cmake-configure": 120.0,
    "cmake-build": 180.0,
    "ctest": 120.0,
    "executable-run": 30.0,
}

ValidationProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_cmake_validation(
    candidate: CandidateAssemblyResult,
    configuration_digest: str,
    blueprint_digest: str,
    required_validations: tuple[str, ...],
    *,
    target_name: str,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
    validation_environment: Path,
    process_runner: ValidationProcessRunner = run_controlled_process,
) -> ValidationReport:
    """Configure, build, test, and run a CMake project without touching its source tree."""
    _validate_inputs(
        candidate,
        required_validations,
        target_name=target_name,
        validation_environment=validation_environment,
    )
    resolved_cmake = _resolve_tool(cmake_executable, name="CMake")
    resolved_ctest = _resolve_tool(ctest_executable, name="CTest")
    candidate_root = candidate.root.resolve(strict=True)
    build_root = validation_environment.resolve(strict=False) / "build"
    ordered_validations = tuple(
        name for name in CMAKE_VALIDATION_REPORT_GATES if name in required_validations
    )
    checks: list[ValidationCheck] = []
    blocked = False

    LOGGER.info(
        "cmake_validation_started required=%d candidate_digest=%s",
        len(ordered_validations),
        candidate.digest,
    )
    for name in ordered_validations:
        phase = _phase_for(name)
        unavailable_reason = _unavailable_reason(
            name,
            cmake_executable=resolved_cmake,
            ctest_executable=resolved_ctest,
        )
        if blocked:
            check = skipped_validation_check(
                name,
                phase,
                required=True,
                reason="A prerequisite CMake validation gate did not pass.",
            )
        elif unavailable_reason is not None:
            check = skipped_validation_check(
                name,
                phase,
                required=True,
                reason=unavailable_reason,
            )
        else:
            specification = _process_specification(
                name,
                candidate_root=candidate_root,
                build_root=build_root,
                target_name=target_name,
                cmake_executable=resolved_cmake,
                ctest_executable=resolved_ctest,
            )
            result = process_runner(specification)
            verify_candidate_digest(candidate)
            check = _check_from_result(name, phase, result)
        checks.append(check)
        if check.status is not ValidationStatus.PASS:
            blocked = True

    verify_candidate_digest(candidate)
    report = ValidationReport(
        configuration_digest=configuration_digest,
        blueprint_digest=blueprint_digest,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        checks=tuple(checks),
    )
    incomplete = sum(check.status is not ValidationStatus.PASS for check in report.checks)
    LOGGER.info(
        "cmake_validation_completed checks=%d incomplete=%d candidate_digest=%s",
        len(report.checks),
        incomplete,
        candidate.digest,
    )
    return report


def _validate_inputs(
    candidate: CandidateAssemblyResult,
    required_validations: tuple[str, ...],
    *,
    target_name: str,
    validation_environment: Path,
) -> None:
    if not _TARGET_NAME_PATTERN.fullmatch(target_name):
        raise ValueError("CMake target name is invalid for runtime validation.")
    if (
        len(required_validations) != len(set(required_validations))
        or not required_validations
        or any(name not in CMAKE_VALIDATION_REPORT_GATES for name in required_validations)
    ):
        raise ValueError("CMake required validation gates are invalid.")
    if not validation_environment.is_absolute():
        raise ValueError("Validation environment path must be absolute.")
    resolved_environment = validation_environment.resolve(strict=False)
    if validation_environment.is_symlink():
        raise ValueError("Validation environment cannot be a symbolic link.")
    if resolved_environment.is_relative_to(candidate.root.resolve(strict=True)):
        raise ValueError("Validation environment must be outside the candidate project.")
    if resolved_environment.exists() and not resolved_environment.is_dir():
        raise ValueError("Validation environment path must be a directory when it exists.")
    if not resolved_environment.parent.is_dir():
        raise ValueError("Validation environment parent must already exist.")
    verify_candidate_digest(candidate)


def _resolve_tool(executable: Path | None, *, name: str) -> Path | None:
    if executable is None:
        return None
    try:
        resolved = executable.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{name} executable must be an existing ordinary file.") from error
    if not resolved.is_file():
        raise ValueError(f"{name} executable must be an existing ordinary file.")
    return resolved


def _unavailable_reason(
    name: str,
    *,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
) -> str | None:
    if cmake_executable is None:
        return "CMake is unavailable in this validation runtime."
    if name == "ctest" and ctest_executable is None:
        return "CMake CTest is unavailable in this validation runtime."
    return None


def _process_specification(
    name: str,
    *,
    candidate_root: Path,
    build_root: Path,
    target_name: str,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
) -> ControlledProcessSpec:
    argv: tuple[str, ...]
    if name == "cmake-configure" and cmake_executable is not None:
        argv = (
            str(cmake_executable),
            "-S",
            str(candidate_root),
            "-B",
            str(build_root),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DBUILD_TESTING=ON",
        )
    elif name == "cmake-build" and cmake_executable is not None:
        argv = (str(cmake_executable), "--build", str(build_root), "--config", "Debug")
    elif name == "ctest" and ctest_executable is not None:
        argv = (
            str(ctest_executable),
            "--test-dir",
            str(build_root),
            "--output-on-failure",
            "-C",
            "Debug",
        )
    elif name == "executable-run":
        suffix = ".exe" if os.name == "nt" else ""
        argv = (str(build_root / f"{target_name}{suffix}"),)
    else:
        raise ValueError("CMake validation command is unavailable.")
    return ControlledProcessSpec(
        name=name,
        argv=argv,
        cwd=candidate_root,
        timeout_seconds=_TIMEOUTS[name],
        output_limit_bytes=_OUTPUT_LIMIT_BYTES,
    )


def _check_from_result(
    name: str,
    phase: ValidationPhase,
    result: ControlledProcessResult,
) -> ValidationCheck:
    check = check_from_process_result(name, phase, required=True, result=result)
    if result.output_truncated:
        return ValidationCheck(
            name,
            ValidationStatus.FAIL,
            required=True,
            phase=phase,
            diagnostics="Validation process exceeded its output limit.",
        )
    if (
        name == "executable-run"
        and check.status is ValidationStatus.PASS
        and result.stdout.splitlines() != [_EXPECTED_EXECUTABLE_OUTPUT]
    ):
        return ValidationCheck(
            name,
            ValidationStatus.FAIL,
            required=True,
            phase=phase,
            diagnostics="Generated executable output did not match the recipe contract.",
        )
    return check


def _phase_for(name: str) -> ValidationPhase:
    if name == "executable-run":
        return ValidationPhase.RUNTIME
    return ValidationPhase.QUALITY
