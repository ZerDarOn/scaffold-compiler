"""Recipe-driven entry point for the shared generation transaction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from scaffold_compiler.blueprint_catalog import BlueprintCatalog, load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    GenerationPlan,
    compile_recipe_blueprint_plan,
    resolve_recipe_capabilities,
)
from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.generation_workflow import (
    CompletedGeneration,
    execute_generation_transaction,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyAdapterRegistry,
    ProjectAssemblyRequest,
    assemble_project_candidate,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistry,
    ProjectValidationRequest,
    execute_project_validation,
)
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.validation import ValidationReport


class ProjectGenerationWorkflowError(RuntimeError):
    """Raised when public recipe identities do not describe one generation run."""


def execute_project_generation(
    configuration: RecipeProjectConfiguration,
    recipe: ProjectRecipe,
    *,
    run_id: str,
    catalog_root: Path,
    assembly_registry: ProjectAssemblyAdapterRegistry,
    validation_registry: ProjectValidationAdapterRegistry,
    validation_runtime_context: object,
    mover: Callable[[Path, Path], None] | None = None,
) -> CompletedGeneration:
    """Run one recipe without interpreting any recipe-specific answer field."""
    if (configuration.recipe_id, configuration.recipe_version) != (
        recipe.recipe_id,
        recipe.version,
    ):
        raise ProjectGenerationWorkflowError(
            "Project configuration and recipe identities do not match."
        )
    resolved_catalog_root = catalog_root.resolve(strict=True)

    def plan_compiler() -> tuple[BlueprintCatalog, GenerationPlan]:
        catalog = load_blueprint_catalog(resolved_catalog_root)
        requested = resolve_recipe_capabilities(recipe, configuration.answers)
        return catalog, compile_recipe_blueprint_plan(catalog, recipe, requested)

    def candidate_materializer(
        workspace: Path,
        catalog: BlueprintCatalog,
        plan: GenerationPlan,
    ) -> CandidateAssemblyResult:
        return assemble_project_candidate(
            ProjectAssemblyRequest(
                configuration=configuration,
                recipe=recipe,
                catalog=catalog,
                plan=plan,
                workspace=workspace,
                catalog_root=resolved_catalog_root,
            ),
            assembly_registry,
        )

    def validator(
        candidate: CandidateAssemblyResult,
        configuration_digest: str,
        blueprint_digest: str,
        required_validations: tuple[str, ...],
        validation_environment: Path,
    ) -> ValidationReport:
        return execute_project_validation(
            ProjectValidationRequest(
                configuration=configuration,
                recipe=recipe,
                candidate=candidate,
                configuration_digest=configuration_digest,
                blueprint_digest=blueprint_digest,
                required_validations=required_validations,
                validation_environment=validation_environment,
                runtime_context=validation_runtime_context,
            ),
            validation_registry,
        )

    return execute_generation_transaction(
        target=configuration.target_directory,
        configuration_digest=configuration.configuration_digest,
        run_id=run_id,
        plan_compiler=plan_compiler,
        candidate_materializer=candidate_materializer,
        validator=validator,
        mover=mover,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
    )
