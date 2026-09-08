"""Trusted assembly adapter for the built-in Go CLI recipe."""

from __future__ import annotations

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    assemble_candidate_project,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyAdapterRegistry,
    ProjectAssemblyRequest,
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_recipe_registry import GO_ASSEMBLY_ADAPTER_KEY


def build_go_project_assembly_adapter_registry() -> ProjectAssemblyAdapterRegistry:
    """Return the statically registered Go assembly adapter."""
    return build_project_assembly_adapter_registry(
        ((GO_ASSEMBLY_ADAPTER_KEY, assemble_go_project_candidate),)
    )


def assemble_go_project_candidate(
    request: ProjectAssemblyRequest,
) -> CandidateAssemblyResult:
    """Render a fixed standard-library Go CLI without executing blueprint behavior."""
    answers = request.configuration.answers
    if set(answers) != {"binary_name", "module_path"}:
        raise ValueError("Go assembly answers do not match the trusted schema.")
    binary_name = answers["binary_name"]
    module_path = answers["module_path"]
    if not isinstance(binary_name, str) or not isinstance(module_path, str):
        raise ValueError("Go assembly answers have invalid types.")
    return assemble_candidate_project(
        request.catalog,
        request.plan,
        request.workspace,
        values={
            "binary_name": binary_name,
            "module_path": module_path,
            "project_name": request.configuration.project_name,
        },
    )
