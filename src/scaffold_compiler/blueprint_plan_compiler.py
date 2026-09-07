"""Deterministic capability closure and blueprint plan compilation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from scaffold_compiler.blueprint_catalog import BlueprintCatalog, BlueprintManifest
from scaffold_compiler.project_recipe_registry import ProjectRecipe


class PlanCompilationError(ValueError):
    """Raised when selected blueprint capabilities cannot form a safe plan."""


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    """An immutable and canonically serializable generation plan."""

    requested_capabilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    blueprint_ids: tuple[str, ...]
    file_owners: tuple[tuple[str, str], ...]
    recipe_id: str | None = None
    recipe_version: str | None = None

    def serialize(self) -> bytes:
        """Return the byte-stable canonical plan representation."""
        record: dict[str, object] = {
            "schema_version": 1 if self.recipe_id is None else 2,
            "requested_capabilities": self.requested_capabilities,
            "capabilities": self.capabilities,
            "blueprint_ids": self.blueprint_ids,
            "file_owners": self.file_owners,
        }
        if self.recipe_id is not None:
            record["recipe"] = self.recipe_id
            record["recipe_version"] = self.recipe_version
        return (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()

    @property
    def digest(self) -> str:
        """Bind later work to this exact deterministic plan."""
        return hashlib.sha256(self.serialize()).hexdigest()


def compile_blueprint_plan(
    catalog: BlueprintCatalog,
    requested_capabilities: tuple[str, ...],
) -> GenerationPlan:
    """Resolve capabilities before any candidate project files are written."""
    if not requested_capabilities:
        raise PlanCompilationError("At least one capability must be requested.")
    requested = tuple(sorted(set(requested_capabilities)))
    if len(requested) != len(requested_capabilities):
        raise PlanCompilationError("Requested capabilities contain duplicates.")

    providers = _index_unique_providers(catalog)
    _validate_unscoped_dependency_graph(catalog, providers)
    selected = _resolve_closure(requested, providers)
    _validate_conflicts(selected)
    file_owners = _collect_file_owners(selected)
    ordered = _stable_recipe_topological_order(selected, providers)
    capabilities = tuple(
        sorted({capability for manifest in selected for capability in manifest.provides})
    )
    return GenerationPlan(
        requested_capabilities=requested,
        capabilities=capabilities,
        blueprint_ids=tuple(manifest.blueprint_id for manifest in ordered),
        file_owners=file_owners,
    )


def compile_recipe_blueprint_plan(
    catalog: BlueprintCatalog,
    recipe: ProjectRecipe,
    requested_capabilities: tuple[str, ...],
) -> GenerationPlan:
    """Compile a stage-free plan constrained to one trusted recipe."""
    if len(requested_capabilities) != len(set(requested_capabilities)):
        raise PlanCompilationError("Requested capabilities contain duplicates.")
    allowed_ids = set(recipe.allowed_blueprint_ids)
    manifests_by_id = {manifest.blueprint_id: manifest for manifest in catalog.manifests}
    missing_allowed = allowed_ids.difference(manifests_by_id)
    if missing_allowed:
        raise PlanCompilationError("Recipe scope contains an unavailable blueprint.")
    scoped_catalog = BlueprintCatalog(
        tuple(manifests_by_id[blueprint_id] for blueprint_id in sorted(allowed_ids))
    )
    providers = _index_unique_providers(scoped_catalog)
    all_capabilities = {
        capability for manifest in catalog.manifests for capability in manifest.provides
    }
    _validate_recipe_dependency_graph(
        scoped_catalog,
        providers,
        all_capabilities,
        allowed_ids,
        set(recipe.allowed_validation_gates),
    )
    requested = tuple(sorted(set(recipe.required_capabilities) | set(requested_capabilities)))
    if not requested:
        raise PlanCompilationError("At least one capability must be requested.")
    selected = _resolve_recipe_closure(requested, providers, all_capabilities)
    _validate_conflicts(selected)
    file_owners = _collect_file_owners(selected)
    ordered = _stable_recipe_topological_order(selected, providers)
    capabilities = tuple(
        sorted({capability for manifest in selected for capability in manifest.provides})
    )
    return GenerationPlan(
        requested_capabilities=requested,
        capabilities=capabilities,
        blueprint_ids=tuple(manifest.blueprint_id for manifest in ordered),
        file_owners=file_owners,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
    )


def resolve_recipe_capabilities(
    recipe: ProjectRecipe,
    answers: Mapping[str, object],
) -> tuple[str, ...]:
    """Resolve required and conditional capabilities from normalized recipe answers."""
    capabilities = set(recipe.required_capabilities)
    for rule in recipe.capability_rules:
        if all(
            key in answers
            and type(answers[key]) is type(expected)
            and answers[key] == expected
            for key, expected in rule.conditions
        ):
            capabilities.update(rule.requested_capabilities)
    return tuple(sorted(capabilities))


def _index_unique_providers(
    catalog: BlueprintCatalog,
) -> dict[str, BlueprintManifest]:
    providers: dict[str, BlueprintManifest] = {}
    for manifest in catalog.manifests:
        for capability in manifest.provides:
            existing = providers.get(capability)
            if existing is not None:
                raise PlanCompilationError(
                    f"Capability {capability} has multiple providers: "
                    f"{existing.blueprint_id}, {manifest.blueprint_id}."
                )
            providers[capability] = manifest
    return providers


def _validate_unscoped_dependency_graph(
    catalog: BlueprintCatalog,
    providers: dict[str, BlueprintManifest],
) -> None:
    states: dict[str, str] = {}

    def visit(manifest: BlueprintManifest) -> None:
        state = states.get(manifest.blueprint_id)
        if state == "visiting":
            raise PlanCompilationError(
                f"Capability dependency cycle includes {manifest.blueprint_id}."
            )
        if state == "visited":
            return
        states[manifest.blueprint_id] = "visiting"
        for capability in manifest.requires:
            provider = providers.get(capability)
            if provider is None:
                raise PlanCompilationError(f"Required capability {capability} has no provider.")
            visit(provider)
        states[manifest.blueprint_id] = "visited"

    for manifest in catalog.manifests:
        visit(manifest)


def _validate_recipe_dependency_graph(
    catalog: BlueprintCatalog,
    providers: dict[str, BlueprintManifest],
    all_capabilities: set[str],
    allowed_ids: set[str],
    allowed_validation_gates: set[str],
) -> None:
    for manifest in catalog.manifests:
        if set(manifest.after).difference(allowed_ids):
            raise PlanCompilationError(
                f"Blueprint {manifest.blueprint_id} ordering crosses the recipe scope."
            )
        if set(manifest.validations).difference(allowed_validation_gates):
            raise PlanCompilationError(
                f"Blueprint {manifest.blueprint_id} uses a validation outside the recipe scope."
            )
        for capability in manifest.requires:
            if capability not in providers:
                if capability in all_capabilities:
                    raise PlanCompilationError(
                        f"Required capability {capability} crosses the recipe scope."
                    )
                raise PlanCompilationError(f"Required capability {capability} has no provider.")


def _resolve_closure(
    requested: tuple[str, ...],
    providers: dict[str, BlueprintManifest],
) -> tuple[BlueprintManifest, ...]:
    selected: dict[str, BlueprintManifest] = {}

    def select(capability: str) -> None:
        provider = providers.get(capability)
        if provider is None:
            raise PlanCompilationError(f"Requested capability {capability} has no provider.")
        if provider.blueprint_id in selected:
            return
        selected[provider.blueprint_id] = provider
        for requirement in provider.requires:
            select(requirement)

    for capability in requested:
        select(capability)
    return tuple(selected.values())


def _resolve_recipe_closure(
    requested: tuple[str, ...],
    providers: dict[str, BlueprintManifest],
    all_capabilities: set[str],
) -> tuple[BlueprintManifest, ...]:
    try:
        return _resolve_closure(requested, providers)
    except PlanCompilationError as error:
        missing = [capability for capability in requested if capability not in providers]
        if any(capability in all_capabilities for capability in missing):
            raise PlanCompilationError(
                "Requested capability is provided only outside the recipe scope."
            ) from error
        raise


def _validate_conflicts(selected: tuple[BlueprintManifest, ...]) -> None:
    capabilities = {capability for manifest in selected for capability in manifest.provides}
    for manifest in selected:
        overlap = sorted(set(manifest.conflicts) & capabilities)
        if overlap:
            raise PlanCompilationError(
                f"Blueprint {manifest.blueprint_id} has selected capability conflict: "
                f"{', '.join(overlap)}."
            )


def _collect_file_owners(
    selected: tuple[BlueprintManifest, ...],
) -> tuple[tuple[str, str], ...]:
    owners: dict[str, str] = {}
    for manifest in selected:
        for file in manifest.files:
            existing = owners.get(file.target)
            if existing is not None:
                raise PlanCompilationError(
                    f"Output file {file.target} is owned by both {existing} and "
                    f"{manifest.blueprint_id}."
                )
            owners[file.target] = manifest.blueprint_id
    return tuple(sorted(owners.items()))


def _stable_recipe_topological_order(
    selected: tuple[BlueprintManifest, ...],
    providers: dict[str, BlueprintManifest],
) -> tuple[BlueprintManifest, ...]:
    selected_by_id = {manifest.blueprint_id: manifest for manifest in selected}
    dependencies = {
        manifest.blueprint_id: {
            providers[capability].blueprint_id
            for capability in manifest.requires
            if providers[capability].blueprint_id in selected_by_id
        }
        | set(manifest.after).intersection(selected_by_id)
        for manifest in selected
    }
    ordered: list[BlueprintManifest] = []
    while dependencies:
        ready_ids = sorted(item for item, needs in dependencies.items() if not needs)
        if not ready_ids:
            raise PlanCompilationError("Capability dependency cycle prevents planning.")
        blueprint_id = ready_ids[0]
        ordered.append(selected_by_id[blueprint_id])
        dependencies.pop(blueprint_id)
        for needs in dependencies.values():
            needs.discard(blueprint_id)
    return tuple(ordered)
