"""Trusted FastAPI validation adapter over the existing bounded V1 executor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe

from scaffold_compiler.project_recipe_registry import FASTAPI_VALIDATION_ADAPTER_KEY
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
)
from scaffold_compiler.v1_validation_executor import (
    V1_VALIDATION_REPORT_GATES,
    ValidationProcessRunner,
    execute_v1_validation,
)
from scaffold_compiler.validation import ValidationReport, run_controlled_process


@dataclass(frozen=True, slots=True, repr=False)
class FastApiValidationRuntime:
    """Trusted runtime-only inputs that never enter public recipe configuration."""

    package_name: str
    uv_executable: Path
    database_url: str | None
    run_id: str
    docker_executable: Path | None
    forbidden_absolute_paths: tuple[Path, ...]
    secrets: tuple[str, ...] = ()
    process_runner: ValidationProcessRunner = run_controlled_process
    compose_password_factory: Callable[[], str] = lambda: token_urlsafe(24)


def build_fastapi_project_validation_adapter_registration() -> ProjectValidationAdapterRegistration:
    """Register the fixed FastAPI gates with one statically imported adapter."""
    return ProjectValidationAdapterRegistration(
        adapter_key=FASTAPI_VALIDATION_ADAPTER_KEY,
        validation_gates=V1_VALIDATION_REPORT_GATES,
        adapter=execute_fastapi_project_validation,
    )


def execute_fastapi_project_validation(request: ProjectValidationRequest) -> ValidationReport:
    """Translate opaque trusted runtime inputs into the bounded V1 validator call."""
    runtime = request.runtime_context
    if not isinstance(runtime, FastApiValidationRuntime):
        raise TypeError("FastAPI validation runtime context is invalid.")
    return execute_v1_validation(
        request.candidate,
        request.configuration_digest,
        request.blueprint_digest,
        request.required_validations,
        package_name=runtime.package_name,
        uv_executable=runtime.uv_executable,
        validation_environment=request.validation_environment,
        database_url=runtime.database_url,
        run_id=runtime.run_id,
        docker_executable=runtime.docker_executable,
        forbidden_absolute_paths=runtime.forbidden_absolute_paths,
        secrets=runtime.secrets,
        process_runner=runtime.process_runner,
        compose_password_factory=runtime.compose_password_factory,
    )
