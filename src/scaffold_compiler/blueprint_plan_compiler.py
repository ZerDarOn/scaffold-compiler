"""Deterministic capability closure and blueprint plan compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scaffold_compiler.blueprint_catalog import BlueprintCatalog, BlueprintManifest


class PlanCompilationError(ValueError):
    """Raised when selected blueprint capabilities cannot form a safe plan."""


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    """An immutable and canonically serializable generation plan."""

    requested_capabilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    blueprint_ids: tuple[str, ...]
    file_owners: tuple[tuple[str, str], ...]

    def serialize(self) -> bytes:
        """Return the byte-stable canonical plan representation."""
        record = {
            "schema_version": 1,
            "requested_capabilities": self.requested_capabilities,
            "capabilities": self.capabilities,
            "blueprint_ids": self.blueprint_ids,
            "file_owners": self.file_owners,
        }
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
    _validate_dependency_graph(catalog, providers)
    selected = _resolve_closure(requested, providers)
    _validate_conflicts(selected)
    file_owners = _collect_file_owners(selected)
    ordered = _stable_topological_order(selected, providers)
    capabilities = tuple(
        sorted({capability for manifest in selected for capability in manifest.provides})
    )
    return GenerationPlan(
        requested_capabilities=requested,
        capabilities=capabilities,
        blueprint_ids=tuple(manifest.blueprint_id for manifest in ordered),
        file_owners=file_owners,
    )


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


def _validate_dependency_graph(
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
            if provider.stage.order > manifest.stage.order:
                raise PlanCompilationError(
                    f"Blueprint {manifest.blueprint_id} depends on capability {capability} "
                    "from a later stage."
                )
            visit(provider)
        states[manifest.blueprint_id] = "visited"

    for manifest in catalog.manifests:
        visit(manifest)


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


def _stable_topological_order(
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
        for manifest in selected
    }
    ordered: list[BlueprintManifest] = []
    while dependencies:
        ready = sorted(
            (selected_by_id[item] for item, needs in dependencies.items() if not needs),
            key=lambda manifest: (manifest.stage.order, manifest.blueprint_id),
        )
        if not ready:
            raise PlanCompilationError("Capability dependency cycle prevents planning.")
        manifest = ready[0]
        ordered.append(manifest)
        dependencies.pop(manifest.blueprint_id)
        for needs in dependencies.values():
            needs.discard(manifest.blueprint_id)
    return tuple(ordered)
