"""Trusted assembly adapter for the built-in C/CMake CLI recipe."""

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
from scaffold_compiler.project_recipe_registry import CMAKE_ASSEMBLY_ADAPTER_KEY

_STRICT_WARNINGS_BLUEPRINT_ID = "cmake-strict-warnings"


def build_cmake_project_assembly_adapter_registry() -> ProjectAssemblyAdapterRegistry:
    """Return the statically registered CMake assembly adapter."""
    return build_project_assembly_adapter_registry(
        ((CMAKE_ASSEMBLY_ADAPTER_KEY, assemble_cmake_project_candidate),)
    )


def assemble_cmake_project_candidate(
    request: ProjectAssemblyRequest,
) -> CandidateAssemblyResult:
    """Render a fixed C11 project without executing manifest-provided behavior."""
    answers = request.configuration.answers
    if set(answers) != {"strict_warnings", "target_name"}:
        raise ValueError("CMake assembly answers do not match the trusted schema.")
    target_name = answers["target_name"]
    strict_warnings = answers["strict_warnings"]
    if not isinstance(target_name, str) or type(strict_warnings) is not bool:
        raise ValueError("CMake assembly answers have invalid types.")
    strict_selected = _STRICT_WARNINGS_BLUEPRINT_ID in request.plan.blueprint_ids
    if strict_selected is not strict_warnings:
        raise ValueError("CMake strict-warning plan does not match the normalized answer.")
    return assemble_candidate_project(
        request.catalog,
        request.plan,
        request.workspace,
        values={
            "project_name": request.configuration.project_name,
            "strict_warnings_setup": (
                "include(cmake/StrictWarnings.cmake)" if strict_warnings else ""
            ),
            "target_name": target_name,
            "target_name_upper": target_name.upper(),
        },
    )
