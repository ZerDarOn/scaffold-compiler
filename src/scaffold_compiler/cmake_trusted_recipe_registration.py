"""Complete trusted registration for the built-in CMake CLI recipe."""

from __future__ import annotations

from pathlib import Path

from scaffold_compiler.cmake_project_assembly_adapter import (
    build_cmake_project_assembly_adapter_registry,
)
from scaffold_compiler.cmake_project_validation_adapter import (
    CMakeValidationRuntime,
    build_cmake_project_validation_adapter_registration,
)
from scaffold_compiler.interactive_configuration import (
    build_cmake_recipe_questionnaire_registration,
)
from scaffold_compiler.project_recipe_registry import (
    CMAKE_ANSWER_PARSER_KEY,
    CMAKE_ASSEMBLY_ADAPTER_KEY,
    CMAKE_RECIPE_ID,
    ProjectRecipe,
    build_cmake_project_recipe_declaration,
)
from scaffold_compiler.recipe_project_configuration import (
    RecipeProjectConfiguration,
    normalize_cmake_recipe_answers,
)
from scaffold_compiler.trusted_recipe_registration import (
    RecipeRuntimeFactoryRegistration,
    TrustedRecipeRegistration,
)


def build_cmake_trusted_recipe_registration(
    *,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
) -> TrustedRecipeRegistration:
    """Bind every trusted CMake recipe component in one registration unit."""

    def runtime_factory(
        configuration: RecipeProjectConfiguration,
        recipe: ProjectRecipe,
        run_id: str,
    ) -> object:
        del recipe, run_id
        target_name = configuration.answers.get("target_name")
        if not isinstance(target_name, str):
            raise ValueError("CMake runtime answers are invalid.")
        return CMakeValidationRuntime(
            target_name=target_name,
            cmake_executable=cmake_executable,
            ctest_executable=ctest_executable,
        )

    return TrustedRecipeRegistration(
        declaration=build_cmake_project_recipe_declaration(),
        answer_parser_key=CMAKE_ANSWER_PARSER_KEY,
        answer_normalizer=normalize_cmake_recipe_answers,
        questionnaire=build_cmake_recipe_questionnaire_registration(),
        assembly_adapter_key=CMAKE_ASSEMBLY_ADAPTER_KEY,
        assembly_adapter=build_cmake_project_assembly_adapter_registry().get(
            CMAKE_ASSEMBLY_ADAPTER_KEY
        ),
        validation_adapter=build_cmake_project_validation_adapter_registration(),
        runtime_factory=RecipeRuntimeFactoryRegistration(
            CMAKE_RECIPE_ID,
            runtime_factory,
        ),
    )
