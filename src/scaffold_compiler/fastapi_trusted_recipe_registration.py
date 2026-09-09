"""Complete trusted registration for the built-in FastAPI service recipe."""

from __future__ import annotations

from pathlib import Path

from scaffold_compiler.fastapi_project_validation_adapter import (
    FastApiValidationRuntime,
    build_fastapi_project_validation_adapter_registration,
)
from scaffold_compiler.generation_workspace_identity import derive_generation_workspace
from scaffold_compiler.interactive_configuration import (
    build_fastapi_recipe_questionnaire_registration,
)
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_ANSWER_PARSER_KEY,
    FASTAPI_ASSEMBLY_ADAPTER_KEY,
    FASTAPI_RECIPE_ID,
    ProjectRecipe,
    build_fastapi_project_recipe_declaration,
)
from scaffold_compiler.recipe_project_configuration import (
    RecipeProjectConfiguration,
    normalize_fastapi_recipe_answers,
)
from scaffold_compiler.trusted_recipe_registration import (
    RecipeRuntimeFactoryRegistration,
    TrustedRecipeRegistration,
)
from scaffold_compiler.v1_project_compiler import (
    build_fastapi_project_assembly_adapter_registry,
)


def build_fastapi_trusted_recipe_registration(
    *,
    catalog_root: Path,
    uv_executable: Path | None,
    docker_executable: Path | None,
    database_url: str | None,
) -> TrustedRecipeRegistration:
    """Bind every trusted FastAPI recipe component in one registration unit."""
    resolved_catalog_root = catalog_root.resolve(strict=True)

    def runtime_factory(
        configuration: RecipeProjectConfiguration,
        recipe: ProjectRecipe,
        run_id: str,
    ) -> object:
        del recipe
        if uv_executable is None:
            raise ValueError("Required recipe validation runtime is unavailable.")
        answers = configuration.answers
        package_name = answers.get("package_name")
        database = answers.get("database")
        if not isinstance(package_name, str) or database not in {"none", "postgres"}:
            raise ValueError("FastAPI runtime answers are invalid.")
        selected_database_url = database_url if database == "postgres" else None
        workspace = derive_generation_workspace(
            configuration.target_directory,
            run_id=run_id,
            configuration_digest=configuration.configuration_digest,
        )
        return FastApiValidationRuntime(
            package_name=package_name,
            uv_executable=uv_executable,
            database_url=selected_database_url,
            run_id=run_id,
            docker_executable=docker_executable,
            forbidden_absolute_paths=(resolved_catalog_root.parent, workspace),
        )

    return TrustedRecipeRegistration(
        declaration=build_fastapi_project_recipe_declaration(),
        answer_parser_key=FASTAPI_ANSWER_PARSER_KEY,
        answer_normalizer=normalize_fastapi_recipe_answers,
        questionnaire=build_fastapi_recipe_questionnaire_registration(),
        assembly_adapter_key=FASTAPI_ASSEMBLY_ADAPTER_KEY,
        assembly_adapter=build_fastapi_project_assembly_adapter_registry().get(
            FASTAPI_ASSEMBLY_ADAPTER_KEY
        ),
        validation_adapter=build_fastapi_project_validation_adapter_registration(),
        runtime_factory=RecipeRuntimeFactoryRegistration(
            FASTAPI_RECIPE_ID,
            runtime_factory,
        ),
    )
