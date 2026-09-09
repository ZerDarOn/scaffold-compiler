from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.generation_workflow import (
    GenerationWorkflowError,
    execute_generation_transaction,
)
from scaffold_compiler.generation_workspace_identity import derive_generation_workspace
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyRequest,
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_generation_workflow import (
    ProjectGenerationWorkflowError,
    execute_project_generation,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
    build_project_validation_adapter_registry,
)
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.session_state_store import SessionStateStore
from scaffold_compiler.validation import ValidationCheck, ValidationReport, ValidationStatus
from scaffold_compiler.workspace_path_budget import WorkspacePathBudgetError


def _recipe() -> ProjectRecipe:
    return ProjectRecipe(
        recipe_id="c-like-test",
        version="1.0.0",
        answer_parser_key="c-like-answers",
        assembly_adapter_key="c-like-assembly",
        validation_adapter_key="c-like-validation",
        required_capabilities=("c-like-core",),
        capability_rules=(),
        allowed_blueprint_ids=("c-like-core",),
        allowed_validation_gates=("unit-tests",),
        prerequisites=("test-toolchain",),
    )


def _configuration(root: Path, recipe: ProjectRecipe) -> RecipeProjectConfiguration:
    return RecipeProjectConfiguration(
        schema_version=2,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        project_name="C Like Test",
        target_directory=root / "delivery",
        _answers_json='{"strict_warnings":true,"target_name":"c_like_test"}',
    )


def _write_catalog(root: Path) -> Path:
    catalog_root = root / "blueprints"
    blueprint_root = catalog_root / "c_like_core"
    blueprint_root.mkdir(parents=True)
    (blueprint_root / "blueprint.json").write_text(
        json.dumps(
            {
                "after": [],
                "conflicts": [],
                "contributions": {},
                "files": [],
                "id": "c-like-core",
                "provides": ["c-like-core"],
                "requires": [],
                "schema_version": 2,
                "validations": ["unit-tests"],
                "variables": {},
                "version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    return catalog_root


class ProjectGenerationWorkflowTests(unittest.TestCase):
    def test_runs_a_non_python_recipe_through_the_shared_transaction(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = _recipe()
            configuration = _configuration(root, recipe)
            catalog_root = _write_catalog(root)
            runtime_context = object()
            observed: dict[str, object] = {}

            def assemble(request: ProjectAssemblyRequest) -> CandidateAssemblyResult:
                observed["assembly_answers"] = request.configuration.answers
                observed["plan_digest"] = request.plan.digest
                candidate_root = request.workspace / "candidate"
                candidate_root.mkdir()
                content = b"generic lifecycle\n"
                (candidate_root / "result.txt").write_bytes(content)
                record = CandidateFileRecord(
                    path="result.txt",
                    owner="c-like-core",
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
                return CandidateAssemblyResult(
                    root=candidate_root,
                    files=(record,),
                    digest=calculate_candidate_digest(candidate_root, (record,)),
                    plan_digest=request.plan.digest,
                )

            def validate(request: ProjectValidationRequest) -> ValidationReport:
                observed["runtime_context"] = request.runtime_context
                observed["required_validations"] = request.required_validations
                observed["candidate_digest"] = request.candidate.digest
                report = ValidationReport(
                    request.configuration_digest,
                    request.blueprint_digest,
                    request.candidate.plan_digest,
                    request.candidate.digest,
                    (ValidationCheck("unit-tests", ValidationStatus.PASS, required=True),),
                )
                observed["verification_digest"] = report.digest
                return report

            assembly_registry = build_project_assembly_adapter_registry(
                ((recipe.assembly_adapter_key, assemble),)
            )
            validation_registry = build_project_validation_adapter_registry(
                (
                    ProjectValidationAdapterRegistration(
                        recipe.validation_adapter_key,
                        ("unit-tests",),
                        validate,
                    ),
                )
            )

            with self.assertLogs("scaffold_compiler.generation_workflow", level="INFO") as captured:
                completed = execute_project_generation(
                    configuration,
                    recipe,
                    run_id="generic-run",
                    catalog_root=catalog_root,
                    assembly_registry=assembly_registry,
                    validation_registry=validation_registry,
                    validation_runtime_context=runtime_context,
                )

            self.assertEqual((completed.target / "result.txt").read_bytes(), b"generic lifecycle\n")
            self.assertEqual(
                observed["assembly_answers"],
                {"strict_warnings": True, "target_name": "c_like_test"},
            )
            self.assertIs(observed["runtime_context"], runtime_context)
            self.assertEqual(observed["required_validations"], ("unit-tests",))
            self.assertEqual(
                completed.session.configuration_digest,
                configuration.configuration_digest,
            )
            self.assertEqual(completed.session.plan_digest, observed["plan_digest"])
            self.assertEqual(completed.session.candidate_digest, observed["candidate_digest"])
            self.assertEqual(
                completed.session.verification_digest,
                observed["verification_digest"],
            )
            self.assertIn("recipe_id=c-like-test", "\n".join(captured.output))
            workspace = derive_generation_workspace(
                configuration.target_directory,
                run_id="generic-run",
                configuration_digest=configuration.configuration_digest,
            )
            self.assertFalse(workspace.exists())

    def test_rejects_recipe_identity_mismatch_before_creating_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = _recipe()
            configuration = RecipeProjectConfiguration(
                schema_version=2,
                recipe_id="other-recipe",
                recipe_version=recipe.version,
                project_name="Mismatch",
                target_directory=root / "delivery",
                _answers_json="{}",
            )

            with self.assertRaises(ProjectGenerationWorkflowError):
                execute_project_generation(
                    configuration,
                    recipe,
                    run_id="mismatch-run",
                    catalog_root=_write_catalog(root),
                    assembly_registry=build_project_assembly_adapter_registry(()),
                    validation_registry=build_project_validation_adapter_registry(()),
                    validation_runtime_context=object(),
                )

            self.assertFalse(configuration.target_directory.exists())
            self.assertEqual(tuple(root.glob(".scw-*")), ())

    def test_transaction_rejects_unsafe_run_id_before_creating_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = _recipe()
            configuration = _configuration(root, recipe)

            with self.assertRaises(GenerationWorkflowError):
                execute_project_generation(
                    configuration,
                    recipe,
                    run_id="../escape",
                    catalog_root=_write_catalog(root),
                    assembly_registry=build_project_assembly_adapter_registry(()),
                    validation_registry=build_project_validation_adapter_registry(()),
                    validation_runtime_context=object(),
                )

            self.assertFalse(configuration.target_directory.exists())
            self.assertEqual(tuple(root.glob(".scw-*")), ())

    def test_transaction_rejects_unsafe_windows_path_before_any_side_effect(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = (root / "delivery").resolve()
            expected_workspace = derive_generation_workspace(
                target,
                run_id="path-run",
                configuration_digest="0" * 64,
            )
            plan_compiler_called = False

            def plan_compiler() -> tuple[object, object]:
                nonlocal plan_compiler_called
                plan_compiler_called = True
                raise AssertionError("plan compiler must not run")

            with (
                patch(
                    "scaffold_compiler.generation_workflow.validate_workspace_path_budget",
                    side_effect=WorkspacePathBudgetError(
                        "Windows generation path is too long; "
                        "choose a shorter target parent directory."
                    ),
                ) as path_validator,
                patch("scaffold_compiler.generation_workflow.TargetLockStore.acquire") as acquire,
                self.assertRaises(GenerationWorkflowError),
            ):
                execute_generation_transaction(
                    target=target,
                    configuration_digest="0" * 64,
                    run_id="path-run",
                    plan_compiler=plan_compiler,  # type: ignore[arg-type]
                    candidate_materializer=lambda *_args: None,  # type: ignore[arg-type]
                    validator=lambda *_args: None,  # type: ignore[arg-type]
                )

            path_validator.assert_called_once_with(expected_workspace, run_id="path-run")
            acquire.assert_not_called()
            self.assertFalse(plan_compiler_called)
            self.assertFalse(target.exists())
            self.assertFalse(expected_workspace.exists())
            self.assertEqual(tuple(root.iterdir()), ())

    def test_transaction_persists_target_binding_in_the_short_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / ("delivery-" + "x" * 80)
            configuration_digest = "a" * 64
            workspace = derive_generation_workspace(
                target,
                run_id="short-run",
                configuration_digest=configuration_digest,
            )

            def fail_plan() -> tuple[object, object]:
                raise RuntimeError("stop after workspace creation")

            with (
                self.assertLogs("scaffold_compiler.generation_workflow", level="INFO") as logs,
                self.assertRaisesRegex(RuntimeError, "stop after workspace creation"),
            ):
                execute_generation_transaction(
                    target=target,
                    configuration_digest=configuration_digest,
                    run_id="short-run",
                    plan_compiler=fail_plan,  # type: ignore[arg-type]
                    candidate_materializer=lambda *_args: None,  # type: ignore[arg-type]
                    validator=lambda *_args: None,  # type: ignore[arg-type]
                )

            session = SessionStateStore(workspace / "session.json").load()
            self.assertEqual(session.target_name, target.name)
            self.assertTrue(workspace.exists())
            self.assertFalse(target.exists())
            output = "\n".join(logs.output)
            self.assertIn("generation_workspace_selected", output)
            self.assertIn("scheme=short-v1", output)
            self.assertNotIn(target.name, output)


if __name__ == "__main__":
    unittest.main()
