from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.command_line_interface import CommandOutcome
from scaffold_compiler.failed_workspace_recovery import (
    FailedWorkspaceDiscardResult,
    FailedWorkspaceInspection,
)
from scaffold_compiler.generation_workflow import CandidateValidator, CompletedGeneration
from scaffold_compiler.project_configuration import DatabaseChoice, ProjectConfiguration
from scaffold_compiler.session_state_store import FailureStage, SessionState
from scaffold_compiler.v1_command_application import GenerationRunner, V1CommandApplication
from scaffold_compiler.validation import ValidationCheck, ValidationReport, ValidationStatus


class V1CommandApplicationTests(unittest.TestCase):
    def test_non_interactive_run_binds_config_runtime_and_catalog(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "capsule" / "blueprints"
            catalog_root.mkdir(parents=True)
            uv_executable = root / "uv.exe"
            uv_executable.write_bytes(b"uv")
            config_path = root / "project.json"
            config_path.write_text(
                json.dumps(
                    {
                        "project_name": "Example API",
                        "package_name": "example",
                        "target_directory": "delivery",
                        "database": "postgres",
                        "container": "none",
                    }
                ),
                encoding="utf-8",
            )
            captured: dict[str, object] = {}

            def generation_runner(configuration: object, **options: object) -> object:
                captured["configuration"] = configuration
                captured.update(options)
                validator = cast(CandidateValidator, options["validator"])
                validation_environment = root / "validation-env"
                validation_environment.mkdir()
                candidate_root = root / "candidate"
                candidate_root.mkdir()
                candidate = CandidateAssemblyResult(
                    root=candidate_root,
                    files=(),
                    digest="d" * 64,
                    plan_digest="c" * 64,
                )
                with patch(
                    "scaffold_compiler.v1_command_application.execute_v1_validation"
                ) as execute_validation:
                    execute_validation.return_value = ValidationReport(
                        configuration_digest="a" * 64,
                        blueprint_digest="b" * 64,
                        plan_digest="c" * 64,
                        candidate_digest="d" * 64,
                        checks=(ValidationCheck("pytest", ValidationStatus.PASS, True),),
                    )
                    validator(
                        candidate,
                        "a" * 64,
                        "b" * 64,
                        ("pytest",),
                        validation_environment,
                    )
                    called = execute_validation.call_args.kwargs
                self.assertEqual(called["package_name"], "example")
                self.assertEqual(called["uv_executable"], uv_executable.resolve())
                self.assertEqual(called["validation_environment"], validation_environment)
                self.assertEqual(called["run_id"], "release-run")
                self.assertIsNone(called["docker_executable"])
                self.assertEqual(
                    called["database_url"],
                    "postgresql+asyncpg://user:secret@localhost/example",
                )
                return cast(
                    CompletedGeneration,
                    type("Completed", (), {"target": root / "delivery"})(),
                )

            application = V1CommandApplication(
                catalog_root=catalog_root,
                working_directory=root,
                home_directory=root / "home",
                uv_executable=uv_executable,
                environment={"DATABASE_URL": "postgresql+asyncpg://user:secret@localhost/example"},
                generation_runner=cast(GenerationRunner, generation_runner),
                run_id_factory=lambda: "release-run",
            )

            outcome = application.run_non_interactive(config_path)

            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.message, "Project finalized successfully: delivery")
            configuration = cast(ProjectConfiguration, captured["configuration"])
            self.assertEqual(configuration.database, DatabaseChoice.POSTGRES)
            self.assertEqual(captured["catalog_root"], catalog_root)
            self.assertEqual(captured["run_id"], "release-run")

    def test_invalid_json_or_domain_failure_returns_safe_stable_failure(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            catalog_root.mkdir()
            uv_executable = root / "uv"
            uv_executable.write_bytes(b"uv")
            invalid = root / "invalid.json"
            invalid.write_text("[]", encoding="utf-8")
            application = V1CommandApplication(
                catalog_root=catalog_root,
                working_directory=root,
                home_directory=root / "home",
                uv_executable=uv_executable,
                environment={},
            )

            malformed = application.run_non_interactive(invalid)
            missing = application.run_non_interactive(root / "missing.json")

            self.assertEqual(malformed, CommandOutcome(1, "Configuration could not be loaded."))
            self.assertEqual(missing, CommandOutcome(1, "Configuration could not be loaded."))

    def test_preview_is_deterministic_and_does_not_create_a_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "project.json"
            config.write_text(
                json.dumps(
                    {
                        "project_name": "Preview API",
                        "package_name": "preview_api",
                        "target_directory": "delivery",
                        "database": "none",
                        "container": "none",
                    }
                ),
                encoding="utf-8",
            )
            application = V1CommandApplication(
                catalog_root=Path(__file__).parents[2] / "blueprints",
                working_directory=root,
                home_directory=root / "home",
                uv_executable=None,
                environment={},
            )

            first = application.preview(config)
            second = application.preview(config)

            self.assertEqual(first, second)
            self.assertEqual(first.exit_code, 0)
            summary = json.loads(first.message)
            self.assertEqual(summary["target_name"], "delivery")
            self.assertIn("fastapi-http-api", summary["blueprints"])
            self.assertFalse((root / "delivery").exists())
            self.assertFalse(any(".scaffold-" in path.name for path in root.iterdir()))

    def test_inspect_and_discard_route_recovery_results_without_uv(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            catalog_root.mkdir()
            application = V1CommandApplication(
                catalog_root=catalog_root,
                working_directory=root,
                home_directory=root / "home",
                uv_executable=None,
                environment={},
            )
            inspection = FailedWorkspaceInspection(
                run_id="run-1",
                state=SessionState.FAILED_RETRYABLE,
                failed_stage=FailureStage.VERIFY,
                candidate_digest="d" * 64,
                failed_gates=("pytest",),
                evidence_valid=True,
            )
            with (
                patch(
                    "scaffold_compiler.v1_command_application.inspect_failed_workspace",
                    return_value=inspection,
                ) as inspect_workspace,
                patch(
                    "scaffold_compiler.v1_command_application.discard_failed_workspace",
                    return_value=FailedWorkspaceDiscardResult(True),
                ) as discard_workspace,
            ):
                inspected = application.inspect(Path(".delivery.scaffold-run-1"))
                discarded = application.discard(Path(".delivery.scaffold-run-1"))

            self.assertEqual(json.loads(inspected.message)["failed_gates"], ["pytest"])
            self.assertEqual(discarded.exit_code, 0)
            expected = root.resolve() / ".delivery.scaffold-run-1"
            inspect_workspace.assert_called_once_with(expected)
            discard_workspace.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
