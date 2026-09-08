"""Composition root for trusted project recipes shipped with the compiler."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scaffold_compiler.cmake_project_assembly_adapter import (
    build_cmake_project_assembly_adapter_registry,
)
from scaffold_compiler.fastapi_project_validation_adapter import (
    FastApiValidationRuntime,
    build_fastapi_project_validation_adapter_registration,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_command_application import ProjectCommandApplication
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    ProjectRecipe,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import (
    build_project_validation_adapter_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    RecipeProjectConfiguration,
    build_builtin_answer_normalizers,
)
from scaffold_compiler.v1_project_compiler import (
    build_fastapi_project_assembly_adapter_registry,
)


def build_builtin_project_command_application(
    *,
    catalog_root: Path,
    working_directory: Path,
    home_directory: Path,
    uv_executable: Path | None,
    docker_executable: Path | None,
    environment: Mapping[str, str],
) -> ProjectCommandApplication:
    """Compose the generic application with every trusted built-in adapter."""
    resolved_catalog_root = catalog_root.resolve(strict=True)
    selected_environment = dict(environment)
    recipe_registry = build_builtin_project_recipe_registry()
    fastapi_assembly_registry = build_fastapi_project_assembly_adapter_registry()
    cmake_assembly_registry = build_cmake_project_assembly_adapter_registry()

    def validation_runtime_factory(
        configuration: RecipeProjectConfiguration,
        recipe: ProjectRecipe,
        run_id: str,
    ) -> object:
        if recipe.recipe_id != FASTAPI_RECIPE_ID or uv_executable is None:
            raise ValueError("Required recipe validation runtime is unavailable.")
        answers = configuration.answers
        package_name = answers.get("package_name")
        database = answers.get("database")
        if not isinstance(package_name, str) or database not in {"none", "postgres"}:
            raise ValueError("FastAPI runtime answers are invalid.")
        database_url = selected_environment.get("DATABASE_URL") if database == "postgres" else None
        workspace = (
            configuration.target_directory.parent
            / f".{configuration.target_directory.name}.scaffold-{run_id}"
        )
        return FastApiValidationRuntime(
            package_name=package_name,
            uv_executable=uv_executable,
            database_url=database_url,
            run_id=run_id,
            docker_executable=docker_executable,
            forbidden_absolute_paths=(resolved_catalog_root.parent, workspace),
        )

    return ProjectCommandApplication(
        catalog_root=resolved_catalog_root,
        working_directory=working_directory,
        home_directory=home_directory,
        recipe_registry=recipe_registry,
        answer_normalizers=build_builtin_answer_normalizers(),
        assembly_registry=build_project_assembly_adapter_registry(
            (*fastapi_assembly_registry.adapters, *cmake_assembly_registry.adapters)
        ),
        validation_registry=build_project_validation_adapter_registry(
            (build_fastapi_project_validation_adapter_registration(),)
        ),
        validation_runtime_factory=validation_runtime_factory,
    )
