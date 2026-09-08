"""Trusted validation adapter for the built-in C/CMake CLI recipe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scaffold_compiler.cmake_validation_executor import (
    CMAKE_VALIDATION_REPORT_GATES,
    ValidationProcessRunner,
    execute_cmake_validation,
)
from scaffold_compiler.project_recipe_registry import CMAKE_VALIDATION_ADAPTER_KEY
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
)
from scaffold_compiler.validation import ValidationReport, run_controlled_process


@dataclass(frozen=True, slots=True, repr=False)
class CMakeValidationRuntime:
    """Trusted CMake tool paths and process runner kept out of recipe answers."""

    target_name: str
    cmake_executable: Path | None
    ctest_executable: Path | None
    process_runner: ValidationProcessRunner = run_controlled_process


def build_cmake_project_validation_adapter_registration() -> ProjectValidationAdapterRegistration:
    """Register the fixed CMake validation gates with a trusted adapter."""
    return ProjectValidationAdapterRegistration(
        adapter_key=CMAKE_VALIDATION_ADAPTER_KEY,
        validation_gates=CMAKE_VALIDATION_REPORT_GATES,
        adapter=execute_cmake_project_validation,
    )


def execute_cmake_project_validation(request: ProjectValidationRequest) -> ValidationReport:
    """Translate trusted runtime inputs into the bounded CMake validator call."""
    runtime = request.runtime_context
    if not isinstance(runtime, CMakeValidationRuntime):
        raise TypeError("CMake validation runtime context is invalid.")
    return execute_cmake_validation(
        request.candidate,
        request.configuration_digest,
        request.blueprint_digest,
        request.required_validations,
        target_name=runtime.target_name,
        cmake_executable=runtime.cmake_executable,
        ctest_executable=runtime.ctest_executable,
        validation_environment=request.validation_environment,
        process_runner=runtime.process_runner,
    )
