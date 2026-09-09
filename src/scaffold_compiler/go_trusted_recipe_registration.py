"""Complete trusted registration for the built-in Go CLI recipe."""

from __future__ import annotations

from pathlib import Path

from scaffold_compiler.go_project_assembly_adapter import (
    build_go_project_assembly_adapter_registry,
)
from scaffold_compiler.go_project_validation_adapter import (
    GoValidationRuntime,
    build_go_project_validation_adapter_registration,
)
from scaffold_compiler.interactive_configuration import (
    build_go_recipe_questionnaire_registration,
)
from scaffold_compiler.project_recipe_registry import (
    GO_ANSWER_PARSER_KEY,
    GO_ASSEMBLY_ADAPTER_KEY,
    GO_RECIPE_ID,
    ProjectRecipe,
    build_go_project_recipe_declaration,
)
from scaffold_compiler.recipe_project_configuration import (
    RecipeProjectConfiguration,
    normalize_go_recipe_answers,
)
from scaffold_compiler.trusted_recipe_registration import (
    RecipeRuntimeFactoryRegistration,
    TrustedRecipeRegistration,
)


def build_go_trusted_recipe_registration(
    *,
    go_executable: Path | None,
    gofmt_executable: Path | None,
) -> TrustedRecipeRegistration:
    """Bind every trusted Go recipe component in one registration unit."""

    def runtime_factory(
        configuration: RecipeProjectConfiguration,
        recipe: ProjectRecipe,
        run_id: str,
    ) -> object:
        del recipe, run_id
        binary_name = configuration.answers.get("binary_name")
        if not isinstance(binary_name, str):
            raise ValueError("Go runtime answers are invalid.")
        return GoValidationRuntime(
            binary_name=binary_name,
            go_executable=go_executable,
            gofmt_executable=gofmt_executable,
        )

    return TrustedRecipeRegistration(
        declaration=build_go_project_recipe_declaration(),
        answer_parser_key=GO_ANSWER_PARSER_KEY,
        answer_normalizer=normalize_go_recipe_answers,
        questionnaire=build_go_recipe_questionnaire_registration(),
        assembly_adapter_key=GO_ASSEMBLY_ADAPTER_KEY,
        assembly_adapter=build_go_project_assembly_adapter_registry().get(GO_ASSEMBLY_ADAPTER_KEY),
        validation_adapter=build_go_project_validation_adapter_registration(),
        runtime_factory=RecipeRuntimeFactoryRegistration(
            GO_RECIPE_ID,
            runtime_factory,
        ),
    )
