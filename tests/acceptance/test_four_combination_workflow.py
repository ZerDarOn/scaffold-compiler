from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.generation_workflow import (
    GenerationWorkflowError,
    execute_non_interactive_generation,
)
from scaffold_compiler.project_configuration import parse_project_configuration
from scaffold_compiler.session_state_store import (
    FailureStage,
    SessionState,
    SessionStateStore,
)
from scaffold_compiler.validation import (
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
    scan_candidate_static_safety,
)
from tests.golden_projects.project_specifications import GOLDEN_PROJECT_SPECIFICATIONS


class FourCombinationWorkflowTests(unittest.TestCase):
    def test_all_four_combinations_reach_a_clean_finalized_project(self) -> None:
        for index, specification in enumerate(GOLDEN_PROJECT_SPECIFICATIONS, start=1):
            with self.subTest(matrix_id=specification.matrix_id), TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / f"delivery-{specification.matrix_id.lower()}"
                configuration = parse_project_configuration(
                    {
                        "project_name": f"Example {specification.matrix_id}",
                        "package_name": "example",
                        "target_directory": str(target),
                        "database": specification.database,
                        "container": specification.container,
                    },
                    working_directory=root,
                    home_directory=root / "home",
                )

                completed = execute_non_interactive_generation(
                    configuration,
                    run_id=f"matrix-{index}",
                    validator=self.validate_acceptance_candidate,
                )

                expected_paths = {
                    path.format(package_name="example") for path in specification.required_paths
                }
                actual_paths = {record.path for record in completed.files}
                self.assertEqual(actual_paths, expected_paths)
                self.assertEqual(completed.session.state, SessionState.DONE)
                self.assertTrue(target.is_dir())
                self.assertFalse((root / f".{target.name}.scaffold-matrix-{index}").exists())
                self.assertFalse((root / f".{target.name}.scaffold.lock").exists())
                self.assertFalse(
                    any(
                        marker in path.relative_to(target).as_posix()
                        for path in target.rglob("*")
                        for marker in ("generator", "blueprint", "journal", "run_id")
                    )
                )

    def test_mismatched_validator_report_never_publishes_or_discards_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "delivery"
            configuration = parse_project_configuration(
                {
                    "project_name": "Example API",
                    "package_name": "example",
                    "target_directory": str(target),
                    "database": "none",
                    "container": "none",
                },
                working_directory=root,
                home_directory=root / "home",
            )

            def mismatched_validator(
                candidate: CandidateAssemblyResult,
                configuration_digest: str,
                blueprint_digest: str,
                required_validations: tuple[str, ...],
                validation_environment: Path,
            ) -> ValidationReport:
                report = self.validate_acceptance_candidate(
                    candidate,
                    configuration_digest,
                    blueprint_digest,
                    required_validations,
                    validation_environment,
                )
                return ValidationReport(
                    configuration_digest="0" * 64,
                    blueprint_digest=report.blueprint_digest,
                    plan_digest=report.plan_digest,
                    candidate_digest=report.candidate_digest,
                    checks=report.checks,
                )

            with self.assertRaises(GenerationWorkflowError):
                execute_non_interactive_generation(
                    configuration,
                    run_id="mismatch",
                    validator=mismatched_validator,
                )

            workspace = root / ".delivery.scaffold-mismatch"
            self.assertFalse(target.exists())
            self.assertFalse((root / ".delivery.scaffold.lock").exists())
            self.assertTrue((workspace / "candidate").is_dir())
            self.assertTrue((workspace / "candidate_ownership.json").is_file())
            failed_session = SessionStateStore(workspace / "session.json").load()
            self.assertEqual(failed_session.state, SessionState.FAILED_RETRYABLE)
            self.assertEqual(failed_session.failed_stage, FailureStage.VERIFY)

    @staticmethod
    def validate_acceptance_candidate(
        candidate: CandidateAssemblyResult,
        configuration_digest: str,
        blueprint_digest: str,
        required_validations: tuple[str, ...],
        validation_environment: Path,
    ) -> ValidationReport:
        if not validation_environment.is_dir():
            raise AssertionError("Workflow must provide an isolated validation environment.")
        return ValidationReport(
            configuration_digest=configuration_digest,
            blueprint_digest=blueprint_digest,
            plan_digest=candidate.plan_digest,
            candidate_digest=candidate.digest,
            checks=(
                scan_candidate_static_safety(
                    candidate,
                    forbidden_absolute_paths=(candidate.root.parent,),
                    secrets=(),
                ),
                *(
                    ValidationCheck(name, ValidationStatus.PASS, required=True)
                    for name in required_validations
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
