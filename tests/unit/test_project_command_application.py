from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO, cast
from unittest.mock import patch

from scaffold_compiler.cleanup_recovery import CleanupRecoveryResult
from scaffold_compiler.command_line_interface import CommandOutcome
from scaffold_compiler.failed_workspace_recovery import (
    FailedWorkspaceDiscardResult,
    FailedWorkspaceInspection,
)
from scaffold_compiler.generation_workflow import CompletedGeneration, GenerationPreflightError
from scaffold_compiler.generation_workspace_identity import derive_generation_workspace
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
from scaffold_compiler.trusted_recipe_registration import (
    RecipeQuestionnaireRegistration,
    RecipeRuntimeFactoryRegistration,
    build_recipe_questionnaire_registry,
    build_recipe_runtime_factory_registry,
)


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


def _collect_answers(source: TextIO, sink: TextIO) -> dict[str, JSONValue]:
    del source, sink
    return {"target_name": "generic_cli"}


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
        questionnaire_registry=build_recipe_questionnaire_registry(
            (
                RecipeQuestionnaireRegistration(
                    recipe.recipe_id,
                    "Generic command-line project",
                    _collect_answers,
                ),
            )
        ),
        answer_normalizers={recipe.answer_parser_key: _normalizer},
        assembly_registry=build_project_assembly_adapter_registry(()),
        validation_registry=build_project_validation_adapter_registry(()),
        runtime_factory_registry=build_recipe_runtime_factory_registry(
            (
                RecipeRuntimeFactoryRegistration(
                    recipe.recipe_id,
                    runtime_factory,  # type: ignore[arg-type]
                ),
            )
        ),
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
            self.assertEqual(tuple(root.glob(".scw-*")), ())

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

    def test_run_reports_preflight_failure_without_claiming_a_workspace_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root, config_path = _write_fixture(root)
            application = _application(
                root,
                catalog_root,
                generation_runner=cast(
                    ProjectGenerationRunner,
                    lambda *args, **kwargs: (_ for _ in ()).throw(
                        GenerationPreflightError(
                            "Windows generation path is too long; "
                            "choose a shorter target parent directory."
                        )
                    ),
                ),
                runtime_factory=lambda *args: object(),
            )

            outcome = application.run_non_interactive(config_path)

            self.assertEqual(outcome.exit_code, 1)
            self.assertIn("choose a shorter target parent directory", outcome.message)
            self.assertNotIn("failed workspace", outcome.message)

    def test_run_failure_reports_the_exact_short_workspace_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            catalog_root, config_path = _write_fixture(root)
            captured: dict[str, RecipeProjectConfiguration] = {}

            def fail_generation(
                configuration: RecipeProjectConfiguration,
                *_args: object,
                **_kwargs: object,
            ) -> CompletedGeneration:
                captured["configuration"] = configuration
                raise RuntimeError("simulated generation failure")

            application = _application(
                root,
                catalog_root,
                generation_runner=fail_generation,
                runtime_factory=lambda *args: object(),
            )

            outcome = application.run_non_interactive(config_path)
            configuration = captured["configuration"]
            expected = derive_generation_workspace(
                configuration.target_directory,
                run_id="generic-run",
                configuration_digest=configuration.configuration_digest,
            )

            self.assertEqual(outcome.exit_code, 1)
            self.assertIn(f"failed workspace: {expected.name}", outcome.message)

    def test_run_reports_runtime_initialization_failure_without_claiming_a_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root, config_path = _write_fixture(root)
            generation_runner_called = False

            def generation_runner(*args: object, **kwargs: object) -> CompletedGeneration:
                nonlocal generation_runner_called
                generation_runner_called = True
                raise AssertionError("generation runner must not run")

            def runtime_factory(*args: object) -> object:
                raise ValueError("private runtime detail")

            application = _application(
                root,
                catalog_root,
                generation_runner=generation_runner,
                runtime_factory=runtime_factory,
            )

            outcome = application.run_non_interactive(config_path)

            self.assertEqual(
                outcome,
                CommandOutcome(1, "Recipe validation runtime could not be initialized."),
            )
            self.assertFalse(generation_runner_called)
            self.assertNotIn("private runtime detail", outcome.message)
            self.assertNotIn("workspace", outcome.message)

    def test_recovery_commands_reuse_the_exact_workspace_services(self) -> None:
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
                patch(
                    "scaffold_compiler.project_command_application.retry_cleanup_pending_workspace",
                    return_value=CleanupRecoveryResult(True),
                ) as cleanup_workspace,
            ):
                inspected = application.inspect(Path(".delivery.scaffold-failed-run"))
                discarded = application.discard(Path(".delivery.scaffold-failed-run"))
                cleaned = application.cleanup(Path(".delivery.scaffold-failed-run"))

            expected = root.resolve() / ".delivery.scaffold-failed-run"
            inspect_workspace.assert_called_once_with(expected)
            discard_workspace.assert_called_once_with(expected)
            cleanup_workspace.assert_called_once_with(expected)
            self.assertEqual(inspected.exit_code, 0)
            self.assertEqual(json.loads(inspected.message)["failed_gates"], ["unit-tests"])
            self.assertEqual(discarded.exit_code, 0)
            self.assertEqual(cleaned.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
