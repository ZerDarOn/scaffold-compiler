"""Trusted validation adapter for the built-in Go CLI recipe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scaffold_compiler.go_validation_executor import (
    GO_VALIDATION_REPORT_GATES,
    ValidationProcessRunner,
    execute_go_validation,
)
from scaffold_compiler.project_recipe_registry import GO_VALIDATION_ADAPTER_KEY
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
)
from scaffold_compiler.validation import ValidationReport, run_controlled_process


@dataclass(frozen=True, slots=True, repr=False)
class GoValidationRuntime:
    """Trusted Go tool paths and process runner kept out of recipe answers."""

    binary_name: str
    go_executable: Path | None
    gofmt_executable: Path | None
    process_runner: ValidationProcessRunner = run_controlled_process


def build_go_project_validation_adapter_registration() -> ProjectValidationAdapterRegistration:
    """Register the fixed Go validation gates with a trusted adapter."""
    return ProjectValidationAdapterRegistration(
        adapter_key=GO_VALIDATION_ADAPTER_KEY,
        validation_gates=GO_VALIDATION_REPORT_GATES,
        adapter=execute_go_project_validation,
    )


def execute_go_project_validation(request: ProjectValidationRequest) -> ValidationReport:
    """Translate trusted runtime inputs into the bounded Go validator call."""
    runtime = request.runtime_context
    if not isinstance(runtime, GoValidationRuntime):
        raise TypeError("Go validation runtime context is invalid.")
    return execute_go_validation(
        request.candidate,
        request.configuration_digest,
        request.blueprint_digest,
        request.required_validations,
        binary_name=runtime.binary_name,
        go_executable=runtime.go_executable,
        gofmt_executable=runtime.gofmt_executable,
        validation_environment=request.validation_environment,
        process_runner=runtime.process_runner,
    )
