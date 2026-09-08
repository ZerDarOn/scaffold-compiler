"""Trusted validation adapter registration and immutable-result verification."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeAlias

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    verify_candidate_digest,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.validation import ValidationReport, ValidationStatus

LOGGER = logging.getLogger(__name__)
_GATE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class ProjectValidationAdapterRegistryError(RuntimeError):
    """A safe error raised at the trusted project-validation boundary."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProjectValidationRequest:
    """Frozen public inputs plus an opaque trusted adapter runtime context."""

    configuration: RecipeProjectConfiguration
    recipe: ProjectRecipe
    candidate: CandidateAssemblyResult
    configuration_digest: str
    blueprint_digest: str
    required_validations: tuple[str, ...]
    validation_environment: Path
    runtime_context: object


ProjectValidationAdapter: TypeAlias = Callable[[ProjectValidationRequest], ValidationReport]


@dataclass(frozen=True, slots=True)
class ProjectValidationAdapterRegistration:
    """One trusted adapter and the complete fixed gate set it can report."""

    adapter_key: str
    validation_gates: tuple[str, ...]
    adapter: ProjectValidationAdapter


@dataclass(frozen=True, slots=True)
class ProjectValidationAdapterRegistry:
    """Immutable validation adapter registrations with unique gate ownership."""

    registrations: tuple[ProjectValidationAdapterRegistration, ...]

    def get(self, adapter_key: str) -> ProjectValidationAdapterRegistration:
        """Return a trusted registration without dynamic imports."""
        for registration in self.registrations:
            if registration.adapter_key == adapter_key:
                return registration
        _raise_registry_error("unknown_adapter", "Project validation adapter is not registered.")


def build_project_validation_adapter_registry(
    registrations: Iterable[ProjectValidationAdapterRegistration],
) -> ProjectValidationAdapterRegistry:
    """Reject ambiguous adapter keys and validation-gate providers."""
    frozen_registrations: list[ProjectValidationAdapterRegistration] = []
    adapter_keys: set[str] = set()
    gate_providers: set[str] = set()
    for registration in registrations:
        if (
            not _GATE_NAME_PATTERN.fullmatch(registration.adapter_key)
            or len(registration.validation_gates) != len(set(registration.validation_gates))
            or any(not _GATE_NAME_PATTERN.fullmatch(gate) for gate in registration.validation_gates)
        ):
            _raise_registry_error(
                "invalid_validation_adapter",
                "Project validation adapter registration is invalid.",
            )
        if registration.adapter_key in adapter_keys:
            _raise_registry_error(
                "duplicate_validation_adapter",
                "Project validation adapter keys must be unique.",
            )
        if gate_providers.intersection(registration.validation_gates):
            _raise_registry_error(
                "duplicate_validation_gate_provider",
                "Validation gates must have one trusted provider.",
            )
        adapter_keys.add(registration.adapter_key)
        gate_providers.update(registration.validation_gates)
        frozen_registrations.append(registration)
    return ProjectValidationAdapterRegistry(tuple(frozen_registrations))


def execute_project_validation(
    request: ProjectValidationRequest,
    registry: ProjectValidationAdapterRegistry,
) -> ValidationReport:
    """Execute one trusted validator and verify its bindings and candidate immutability."""
    registration = registry.get(request.recipe.validation_adapter_key)
    _validate_request(request, registration)
    LOGGER.info(
        "project_validation_adapter_started recipe_id=%s recipe_version=%s "
        "adapter=%s gates=%s plan_digest=%s candidate_digest=%s",
        request.recipe.recipe_id,
        request.recipe.version,
        registration.adapter_key,
        ",".join(request.required_validations),
        request.candidate.plan_digest,
        request.candidate.digest,
    )
    try:
        verify_candidate_digest(request.candidate)
        report = registration.adapter(request)
        verify_candidate_digest(request.candidate)
        _validate_report(request, registration, report)
    except Exception as error:
        candidate_changed = False
        try:
            verify_candidate_digest(request.candidate)
        except (CandidateChangedError, OSError):
            candidate_changed = True
        LOGGER.error(
            "project_validation_adapter_failed recipe_id=%s adapter=%s "
            "plan_digest=%s candidate_changed=%s",
            request.recipe.recipe_id,
            registration.adapter_key,
            request.candidate.plan_digest,
            candidate_changed,
        )
        if candidate_changed:
            raise ProjectValidationAdapterRegistryError(
                code="validation_candidate_changed",
                message="Project validation adapter changed the candidate.",
            ) from error
        if isinstance(error, ProjectValidationAdapterRegistryError):
            raise
        raise ProjectValidationAdapterRegistryError(
            code="validation_adapter_failed",
            message="Project validation adapter failed.",
        ) from error
    incomplete = sum(
        check.required and check.status is not ValidationStatus.PASS for check in report.checks
    )
    LOGGER.info(
        "project_validation_adapter_completed recipe_id=%s adapter=%s "
        "checks=%d incomplete=%d plan_digest=%s candidate_digest=%s",
        request.recipe.recipe_id,
        registration.adapter_key,
        len(report.checks),
        incomplete,
        request.candidate.plan_digest,
        request.candidate.digest,
    )
    return report


def _validate_request(
    request: ProjectValidationRequest,
    registration: ProjectValidationAdapterRegistration,
) -> None:
    if (
        request.configuration.recipe_id,
        request.configuration.recipe_version,
    ) != (request.recipe.recipe_id, request.recipe.version):
        _raise_registry_error(
            "validation_request_recipe_mismatch",
            "Validation configuration and recipe identities do not match.",
        )
    if request.candidate.plan_digest == "":
        _raise_registry_error(
            "validation_request_plan_mismatch",
            "Validation candidate does not bind a plan.",
        )
    if len(request.required_validations) != len(set(request.required_validations)):
        _raise_registry_error(
            "duplicate_required_gate",
            "Required validation gates contain duplicates.",
        )
    required = set(request.required_validations)
    if not required.issubset(request.recipe.allowed_validation_gates):
        _raise_registry_error(
            "gate_outside_recipe_scope",
            "Required validation gate is outside the recipe scope.",
        )
    if not required.issubset(registration.validation_gates):
        _raise_registry_error(
            "unsupported_required_gate",
            "Required validation gate has no trusted provider.",
        )
    if not request.validation_environment.is_absolute():
        _raise_registry_error(
            "invalid_validation_environment",
            "Validation environment path must be absolute.",
        )


def _validate_report(
    request: ProjectValidationRequest,
    registration: ProjectValidationAdapterRegistration,
    report: ValidationReport,
) -> None:
    if (
        report.configuration_digest != request.configuration_digest
        or report.blueprint_digest != request.blueprint_digest
        or report.plan_digest != request.candidate.plan_digest
        or report.candidate_digest != request.candidate.digest
    ):
        _raise_registry_error(
            "validation_report_binding_mismatch",
            "Validation report does not bind the requested candidate.",
        )
    if any(check.name not in registration.validation_gates for check in report.checks):
        _raise_registry_error(
            "unknown_report_gate",
            "Validation report contains a gate outside the trusted adapter registration.",
        )
    checks = {check.name: check for check in report.checks}
    if any(
        name not in checks or not checks[name].required for name in request.required_validations
    ):
        _raise_registry_error(
            "required_gate_missing",
            "Validation report omits a required validation gate.",
        )


def _raise_registry_error(code: str, message: str) -> NoReturn:
    raise ProjectValidationAdapterRegistryError(code=code, message=message)
