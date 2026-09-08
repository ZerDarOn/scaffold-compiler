from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from scaffold_compiler.failed_workspace_recovery import (
    FailedWorkspaceDiscardResult,
    FailedWorkspaceInspection,
)
from scaffold_compiler.generation_workflow import CompletedGeneration
from scaffold_compiler.project_assembly_adapter_registry import (
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_command_application import (
    ProjectCommandApplication,
    ProjectGenerationRunner,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe, ProjectRecipeRegistry
from scaffold_compiler.project_validation_adapter_registry import (
    build_project_validation_adapter_registry,
)
from scaffold_compiler.recipe_project_configuration import JSONValue, RecipeProjectConfiguration
from scaffold_compiler.session_state_store import FailureStage, SessionState


def _recipe() -> ProjectRecipe:
    return ProjectRecipe(
        recipe_id="generic-cli",
        version="1.2.0",
        answer_parser_key="generic-answers",
        assembly_adapter_key="generic-assembly",
        validation_adapter_key="generic-validation",
        required_capabilities=("generic-core",),
        capability_rules=(),
        allowed_blueprint_ids=("generic-core",),
        allowed_validation_gates=("unit-tests",),
        prerequisites=(),
    )


def _normalizer(raw: object, project_name: str) -> dict[str, JSONValue]:
    del project_name
    if not isinstance(raw, dict) or set(raw) != {"target_name"}:
        raise ValueError("Invalid test answers.")
    target_name = raw["target_name"]
    if not isinstance(target_name, str):
        raise ValueError("Invalid test target name.")
    return {"target_name": target_name}


def _write_fixture(root: Path) -> tuple[Path, Path]:
    catalog_root = root / "blueprints"
    blueprint_root = catalog_root / "generic_core"
    blueprint_root.mkdir(parents=True)
    (blueprint_root / "blueprint.json").write_text(
        json.dumps(
            {
                "after": [],
                "conflicts": [],
                "contributions": {},
                "files": [],
                "id": "generic-core",
                "provides": ["generic-core"],
                "requires": [],
                "schema_version": 2,
                "validations": ["unit-tests"],
                "variables": {},
                "version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    config_path = root / "project.json"
    config_path.write_text(
        json.dumps(
            {
                "answers": {"target_name": "generic_cli"},
                "project_name": "Generic CLI",
                "recipe": "generic-cli",
                "schema_version": 2,
                "target_directory": "delivery",
            }
        ),
        encoding="utf-8",
    )
    return catalog_root, config_path


def _application(
    root: Path,
    catalog_root: Path,
    *,
    generation_runner: ProjectGenerationRunner,
    runtime_factory: object,
) -> ProjectCommandApplication:
    recipe = _recipe()
    return ProjectCommandApplication(
        catalog_root=catalog_root,
        working_directory=root,
        home_directory=root / "home",
        recipe_registry=ProjectRecipeRegistry((recipe,)),
        answer_normalizers={recipe.answer_parser_key: _normalizer},
        assembly_registry=build_project_assembly_adapter_registry(()),
        validation_registry=build_project_validation_adapter_registry(()),
        validation_runtime_factory=runtime_factory,  # type: ignore[arg-type]
        generation_runner=generation_runner,
        run_id_factory=lambda: "generic-run",
    )


class ProjectCommandApplicationTests(unittest.TestCase):
    def test_preview_reports_recipe_identity_and_language_neutral_plan(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root, config_path = _write_fixture(root)
            application = _application(
                root,
                catalog_root,
                generation_runner=cast(ProjectGenerationRunner, lambda *args, **kwargs: None),
                runtime_factory=lambda *args: object(),
            )

            outcome = application.preview(config_path)

            self.assertEqual(outcome.exit_code, 0)
            summary = json.loads(outcome.message)
            self.assertEqual(summary["recipe"], "generic-cli")
            self.assertEqual(summary["recipe_version"], "1.2.0")
            self.assertEqual(summary["blueprints"], ["generic-core"])
            self.assertEqual(summary["validations"], ["unit-tests"])
            self.assertFalse((root / ".delivery.scaffold-generic-run").exists())

    def test_run_passes_only_generic_configuration_recipe_and_runtime_context(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root, config_path = _write_fixture(root)
            captured: dict[str, object] = {}
            runtime_context = object()

            def runtime_factory(
                configuration: RecipeProjectConfiguration,
                recipe: ProjectRecipe,
                run_id: str,
            ) -> object:
                captured["runtime_configuration"] = configuration
                captured["runtime_recipe"] = recipe
                captured["runtime_run_id"] = run_id
                return runtime_context

            def generation_runner(
                configuration: RecipeProjectConfiguration,
                recipe: ProjectRecipe,
                **options: object,
            ) -> CompletedGeneration:
                captured["configuration"] = configuration
                captured["recipe"] = recipe
                captured.update(options)
                return cast(
                    CompletedGeneration,
                    type("Completed", (), {"target": root / "delivery"})(),
                )

            application = _application(
                root,
                catalog_root,
                generation_runner=generation_runner,
                runtime_factory=runtime_factory,
            )

            outcome = application.run_non_interactive(config_path)

            self.assertEqual(outcome.exit_code, 0)
            configuration = cast(RecipeProjectConfiguration, captured["configuration"])
            self.assertEqual(configuration.answers, {"target_name": "generic_cli"})
            self.assertEqual(cast(ProjectRecipe, captured["recipe"]).recipe_id, "generic-cli")
            self.assertEqual(captured["run_id"], "generic-run")
            self.assertIs(captured["validation_runtime_context"], runtime_context)
            self.assertIs(captured["runtime_configuration"], configuration)

    def test_inspect_and_discard_reuse_the_exact_failed_workspace_services(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root, _config_path = _write_fixture(root)
            application = _application(
                root,
                catalog_root,
                generation_runner=cast(ProjectGenerationRunner, lambda *args, **kwargs: None),
                runtime_factory=lambda *args: object(),
            )
            inspection = FailedWorkspaceInspection(
                run_id="failed-run",
                state=SessionState.FAILED_RETRYABLE,
                failed_stage=FailureStage.VERIFY,
                candidate_digest="d" * 64,
                failed_gates=("unit-tests",),
                evidence_valid=True,
            )

            with (
                patch(
                    "scaffold_compiler.project_command_application.inspect_failed_workspace",
                    return_value=inspection,
                ) as inspect_workspace,
                patch(
                    "scaffold_compiler.project_command_application.discard_failed_workspace",
                    return_value=FailedWorkspaceDiscardResult(True),
                ) as discard_workspace,
            ):
                inspected = application.inspect(Path(".delivery.scaffold-failed-run"))
                discarded = application.discard(Path(".delivery.scaffold-failed-run"))

            expected = (root / ".delivery.scaffold-failed-run").absolute()
            inspect_workspace.assert_called_once_with(expected)
            discard_workspace.assert_called_once_with(expected)
            self.assertEqual(inspected.exit_code, 0)
            self.assertEqual(json.loads(inspected.message)["failed_gates"], ["unit-tests"])
            self.assertEqual(discarded.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
