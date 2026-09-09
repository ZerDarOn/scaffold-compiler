"""Composition root for trusted project recipes shipped with the compiler."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scaffold_compiler.builtin_trusted_recipe_registrations import (
    build_builtin_trusted_recipe_registrations,
)
from scaffold_compiler.project_command_application import ProjectCommandApplication
from scaffold_compiler.trusted_recipe_registration import (
    compile_trusted_recipe_registrations,
)


def build_builtin_project_command_application(
    *,
    catalog_root: Path,
    working_directory: Path,
    home_directory: Path,
    uv_executable: Path | None,
    docker_executable: Path | None,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
    go_executable: Path | None = None,
    gofmt_executable: Path | None = None,
    environment: Mapping[str, str],
) -> ProjectCommandApplication:
    """Compose the generic application with every trusted built-in adapter."""
    compiled = compile_trusted_recipe_registrations(
        build_builtin_trusted_recipe_registrations(
            catalog_root=catalog_root,
            uv_executable=uv_executable,
            docker_executable=docker_executable,
            cmake_executable=cmake_executable,
            ctest_executable=ctest_executable,
            go_executable=go_executable,
            gofmt_executable=gofmt_executable,
            environment=environment,
        )
    )

    return ProjectCommandApplication(
        catalog_root=catalog_root,
        working_directory=working_directory,
        home_directory=home_directory,
        recipe_registry=compiled.recipe_registry,
        questionnaire_registry=compiled.questionnaire_registry,
        answer_normalizers=compiled.answer_normalizers,
        assembly_registry=compiled.assembly_registry,
        validation_registry=compiled.validation_registry,
        runtime_factory_registry=compiled.runtime_factory_registry,
    )
