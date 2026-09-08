"""Trusted recipe assembly adapters with plan and candidate boundary checks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeAlias

from scaffold_compiler.blueprint_catalog import BlueprintCatalog
from scaffold_compiler.blueprint_plan_compiler import GenerationPlan
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    verify_candidate_digest,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration

LOGGER = logging.getLogger(__name__)


class ProjectAssemblyAdapterRegistryError(RuntimeError):
    """A safe error raised at the trusted project-assembly boundary."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProjectAssemblyRequest:
    """All frozen inputs needed by one trusted recipe assembly adapter."""

    configuration: RecipeProjectConfiguration
    recipe: ProjectRecipe
    catalog: BlueprintCatalog
    plan: GenerationPlan
    workspace: Path
    catalog_root: Path


ProjectAssemblyAdapter: TypeAlias = Callable[
    [ProjectAssemblyRequest],
    CandidateAssemblyResult,
]


@dataclass(frozen=True, slots=True)
class ProjectAssemblyAdapterRegistry:
    """Immutable key-to-adapter bindings compiled into this application."""

    adapters: tuple[tuple[str, ProjectAssemblyAdapter], ...]

    def get(self, adapter_key: str) -> ProjectAssemblyAdapter:
        """Return one trusted adapter without dynamic imports or manifest execution."""
        for registered_key, adapter in self.adapters:
            if registered_key == adapter_key:
                return adapter
        _raise_registry_error(
            "unknown_assembly_adapter",
            "Project assembly adapter is not registered.",
        )


def build_project_assembly_adapter_registry(
    adapters: Iterable[tuple[str, ProjectAssemblyAdapter]],
) -> ProjectAssemblyAdapterRegistry:
    """Build a registry while rejecting ambiguous adapter keys."""
    registered: list[tuple[str, ProjectAssemblyAdapter]] = []
    seen_keys: set[str] = set()
    for adapter_key, adapter in adapters:
        if not adapter_key:
            _raise_registry_error(
                "invalid_assembly_adapter",
                "Project assembly adapter key is invalid.",
            )
        if adapter_key in seen_keys:
            _raise_registry_error(
                "duplicate_assembly_adapter",
                "Project assembly adapter keys must be unique.",
            )
        seen_keys.add(adapter_key)
        registered.append((adapter_key, adapter))
    return ProjectAssemblyAdapterRegistry(tuple(registered))


def assemble_project_candidate(
    request: ProjectAssemblyRequest,
    registry: ProjectAssemblyAdapterRegistry,
) -> CandidateAssemblyResult:
    """Invoke one trusted adapter and verify its complete candidate result."""
    _validate_request_binding(request)
    adapter = registry.get(request.recipe.assembly_adapter_key)
    LOGGER.info(
        "project_assembly_adapter_started recipe_id=%s recipe_version=%s adapter=%s plan_digest=%s",
        request.recipe.recipe_id,
        request.recipe.version,
        request.recipe.assembly_adapter_key,
        request.plan.digest,
    )
    try:
        result = adapter(request)
        _validate_result_binding(request, result)
    except Exception as error:
        LOGGER.error(
            "project_assembly_adapter_failed recipe_id=%s adapter=%s plan_digest=%s",
            request.recipe.recipe_id,
            request.recipe.assembly_adapter_key,
            request.plan.digest,
        )
        if isinstance(error, ProjectAssemblyAdapterRegistryError):
            raise
        raise ProjectAssemblyAdapterRegistryError(
            code="assembly_adapter_failed",
            message="Project assembly adapter failed.",
        ) from error
    LOGGER.info(
        "project_assembly_adapter_completed recipe_id=%s adapter=%s "
        "plan_digest=%s candidate_digest=%s files=%d",
        request.recipe.recipe_id,
        request.recipe.assembly_adapter_key,
        request.plan.digest,
        result.digest,
        len(result.files),
    )
    return result


def _validate_request_binding(request: ProjectAssemblyRequest) -> None:
    expected_identity = (request.recipe.recipe_id, request.recipe.version)
    if (
        request.configuration.recipe_id,
        request.configuration.recipe_version,
    ) != expected_identity or (
        request.plan.recipe_id,
        request.plan.recipe_version,
    ) != expected_identity:
        _raise_registry_error(
            "assembly_request_recipe_mismatch",
            "Assembly configuration, recipe, and plan identities do not match.",
        )
    catalog_ids = {manifest.blueprint_id for manifest in request.catalog.manifests}
    if not set(request.plan.blueprint_ids).issubset(catalog_ids):
        _raise_registry_error(
            "assembly_request_catalog_mismatch",
            "Assembly plan references a blueprint outside the supplied catalog.",
        )
    if not request.workspace.is_dir():
        _raise_registry_error(
            "assembly_workspace_unavailable",
            "Assembly workspace must already exist.",
        )


def _validate_result_binding(
    request: ProjectAssemblyRequest,
    result: CandidateAssemblyResult,
) -> None:
    if result.root != request.workspace / "candidate":
        _raise_registry_error(
            "assembly_result_root_mismatch",
            "Assembly result root does not match the isolated candidate path.",
        )
    if result.plan_digest != request.plan.digest:
        _raise_registry_error(
            "assembly_result_plan_mismatch",
            "Assembly result does not bind the requested plan.",
        )
    selected_blueprints = set(request.plan.blueprint_ids)
    if any(record.owner not in selected_blueprints for record in result.files):
        _raise_registry_error(
            "assembly_result_owner_mismatch",
            "Assembly result contains a file owner outside the selected plan.",
        )
    paths = tuple(record.path for record in result.files)
    if len(paths) != len(set(paths)):
        _raise_registry_error(
            "assembly_result_duplicate_path",
            "Assembly result contains duplicate file paths.",
        )
    try:
        verify_candidate_digest(result)
    except CandidateChangedError as error:
        raise ProjectAssemblyAdapterRegistryError(
            code="assembly_result_changed",
            message="Assembly result does not match the candidate on disk.",
        ) from error


def _raise_registry_error(code: str, message: str) -> NoReturn:
    raise ProjectAssemblyAdapterRegistryError(code=code, message=message)
