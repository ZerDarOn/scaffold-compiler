"""Language-neutral command application composed from trusted recipe services."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    compile_recipe_blueprint_plan,
    resolve_recipe_capabilities,
)
from scaffold_compiler.command_line_interface import CommandOutcome
from scaffold_compiler.failed_workspace_recovery import (
    discard_failed_workspace,
    inspect_failed_workspace,
)
from scaffold_compiler.generation_workflow import CompletedGeneration
from scaffold_compiler.project_assembly_adapter_registry import ProjectAssemblyAdapterRegistry
from scaffold_compiler.project_generation_workflow import execute_project_generation
from scaffold_compiler.project_recipe_registry import ProjectRecipe, ProjectRecipeRegistry
from scaffold_compiler.project_validation_adapter_registry import ProjectValidationAdapterRegistry
from scaffold_compiler.recipe_project_configuration import (
    AnswerNormalizer,
    RecipeProjectConfiguration,
    parse_recipe_project_configuration,
)

LOGGER = logging.getLogger(__name__)

ProjectGenerationRunner = Callable[..., CompletedGeneration]
ValidationRuntimeFactory = Callable[[RecipeProjectConfiguration, ProjectRecipe, str], object]


class ProjectCommandApplication:
    """Provide CLI operations without interpreting recipe-specific answer fields."""

    def __init__(
        self,
        *,
        catalog_root: Path,
        working_directory: Path,
        home_directory: Path,
        recipe_registry: ProjectRecipeRegistry,
        answer_normalizers: Mapping[str, AnswerNormalizer],
        assembly_registry: ProjectAssemblyAdapterRegistry,
        validation_registry: ProjectValidationAdapterRegistry,
        validation_runtime_factory: ValidationRuntimeFactory,
        generation_runner: ProjectGenerationRunner = execute_project_generation,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._catalog_root = catalog_root.resolve(strict=True)
        self._working_directory = working_directory.resolve(strict=True)
        self._home_directory = home_directory.resolve(strict=False)
        self._recipe_registry = recipe_registry
        self._answer_normalizers = dict(answer_normalizers)
        self._assembly_registry = assembly_registry
        self._validation_registry = validation_registry
        self._validation_runtime_factory = validation_runtime_factory
        self._generation_runner = generation_runner
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)

    def preview(self, config_path: Path) -> CommandOutcome:
        """Return a deterministic recipe plan without creating a workspace."""
        try:
            configuration, recipe = self._load_configuration(config_path)
            catalog = load_blueprint_catalog(self._catalog_root)
            requested = resolve_recipe_capabilities(recipe, configuration.answers)
            plan = compile_recipe_blueprint_plan(catalog, recipe, requested)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            LOGGER.error("project_preview_failed")
            return CommandOutcome(1, "Project plan could not be created.")
        selected = {manifest.blueprint_id: manifest for manifest in catalog.manifests}
        validations = sorted(
            {
                validation
                for blueprint_id in plan.blueprint_ids
                for validation in selected[blueprint_id].validations
            }
        )
        summary = {
            "blueprints": list(plan.blueprint_ids),
            "configuration_digest": configuration.configuration_digest,
            "recipe": recipe.recipe_id,
            "recipe_version": recipe.version,
            "target_name": configuration.target_directory.name,
            "validations": validations,
        }
        LOGGER.info(
            "project_preview_completed recipe_id=%s recipe_version=%s blueprints=%d validations=%d",
            recipe.recipe_id,
            recipe.version,
            len(plan.blueprint_ids),
            len(validations),
        )
        return CommandOutcome(
            0,
            json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        """Resolve one recipe and execute the shared confirmed transaction."""
        try:
            configuration, recipe = self._load_configuration(config_path)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            LOGGER.error("project_configuration_load_failed")
            return CommandOutcome(1, "Configuration could not be loaded.")
        run_id = self._run_id_factory()
        try:
            runtime_context = self._validation_runtime_factory(configuration, recipe, run_id)
            completed = self._generation_runner(
                configuration,
                recipe,
                run_id=run_id,
                catalog_root=self._catalog_root,
                assembly_registry=self._assembly_registry,
                validation_registry=self._validation_registry,
                validation_runtime_context=runtime_context,
            )
        except Exception as error:
            LOGGER.error(
                "project_non_interactive_run_failed run_id=%s recipe_id=%s error_type=%s",
                run_id,
                recipe.recipe_id,
                type(error).__name__,
            )
            workspace_name = f".{configuration.target_directory.name}.scaffold-{run_id}"
            return CommandOutcome(
                1,
                f"Project generation did not complete; failed workspace: {workspace_name}",
            )
        return CommandOutcome(0, f"Project finalized successfully: {completed.target.name}")

    def inspect(self, workspace: Path) -> CommandOutcome:
        """Return safe persisted facts for one failed generation workspace."""
        try:
            inspection = inspect_failed_workspace(self._absolute_path(workspace))
        except (OSError, ValueError):
            LOGGER.error("project_workspace_inspection_failed")
            return CommandOutcome(1, "Failed workspace could not be inspected.")
        summary = {
            "candidate_digest": inspection.candidate_digest,
            "evidence_valid": inspection.evidence_valid,
            "failed_gates": list(inspection.failed_gates),
            "failed_stage": (
                inspection.failed_stage.value if inspection.failed_stage is not None else None
            ),
            "run_id": inspection.run_id,
            "state": inspection.state.value,
        }
        return CommandOutcome(
            0,
            json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

    def discard(self, workspace: Path) -> CommandOutcome:
        """Discard only an exactly owned failed workspace."""
        result = discard_failed_workspace(self._absolute_path(workspace))
        if not result.completed:
            return CommandOutcome(1, result.reason or "Failed workspace was not discarded.")
        return CommandOutcome(0, "Failed workspace discarded successfully.")

    def _load_configuration(
        self,
        config_path: Path,
    ) -> tuple[RecipeProjectConfiguration, ProjectRecipe]:
        path = config_path if config_path.is_absolute() else self._working_directory / config_path
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise TypeError("Configuration must be a JSON object with string keys.")
        configuration = parse_recipe_project_configuration(
            raw,
            registry=self._recipe_registry,
            answer_normalizers=self._answer_normalizers,
            working_directory=self._working_directory,
            home_directory=self._home_directory,
        )
        return configuration, self._recipe_registry.get(configuration.recipe_id)

    def _absolute_path(self, path: Path) -> Path:
        return path if path.is_absolute() else (self._working_directory / path).absolute()
