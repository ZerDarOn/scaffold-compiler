from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import compile_blueprint_plan
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    GeneratedCandidateFile,
    assemble_candidate_project,
    verify_candidate_digest,
)
from scaffold_compiler.validation import (
    ControlledProcessSpec,
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
    check_from_process_result,
    issue_verification_credential,
    run_controlled_process,
    scan_candidate_static_safety,
    skipped_validation_check,
)

DIGESTS = tuple(character * 64 for character in "abcd")


class ControlledProcessTests(unittest.TestCase):
    def test_reports_non_zero_exit_without_using_a_shell(self) -> None:
        with TemporaryDirectory() as directory:
            result = run_controlled_process(
                ControlledProcessSpec(
                    name="non-zero",
                    argv=(sys.executable, "-c", "import sys; print('failed'); sys.exit(7)"),
                    cwd=Path(directory),
                    timeout_seconds=5,
                )
            )

        self.assertEqual(result.return_code, 7)
        self.assertFalse(result.timed_out)
        self.assertIn("failed", result.stdout)

    def test_timeout_terminates_the_spawned_process_tree(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "child-survived"
            child = (
                "import pathlib,time; time.sleep(2); "
                f"pathlib.Path({str(marker)!r}).write_text('alive')"
            )
            parent = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
            )

            result = run_controlled_process(
                ControlledProcessSpec(
                    name="timeout-tree",
                    argv=(sys.executable, "-c", parent),
                    cwd=root,
                    timeout_seconds=0.3,
                )
            )
            time.sleep(2.2)

            self.assertTrue(result.timed_out)
            self.assertFalse(marker.exists())

    def test_caps_output_and_redacts_secrets_from_results_and_logs(self) -> None:
        secret = "credential-value"
        code = f"print({secret!r}); print('x' * 10000)"
        with (
            TemporaryDirectory() as directory,
            self.assertLogs("scaffold_compiler.validation", level="INFO") as logs,
        ):
            result = run_controlled_process(
                ControlledProcessSpec(
                    name="bounded-output",
                    argv=(sys.executable, "-c", code),
                    cwd=Path(directory),
                    timeout_seconds=5,
                    output_limit_bytes=128,
                    secrets=(secret,),
                )
            )

        visible = result.stdout + result.stderr + "\n".join(logs.output)
        self.assertNotIn(secret, visible)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertTrue(result.output_truncated)
        self.assertIn("[output truncated]", result.stdout)

    def test_redacts_a_secret_crossing_the_visible_output_boundary(self) -> None:
        secret = "boundary-secret"
        code = f"print('x' * 120 + {secret!r})"
        with TemporaryDirectory() as directory:
            result = run_controlled_process(
                ControlledProcessSpec(
                    name="boundary-redaction",
                    argv=(sys.executable, "-c", code),
                    cwd=Path(directory),
                    timeout_seconds=5,
                    output_limit_bytes=128,
                    secrets=(secret,),
                )
            )

        self.assertNotIn(secret, result.stdout)
        self.assertNotIn("boundary", result.stdout)
        self.assertTrue(result.output_truncated)

    def test_passes_validated_environment_overrides_without_logging_secret_values(self) -> None:
        secret = "environment-secret"
        with (
            TemporaryDirectory() as directory,
            self.assertLogs("scaffold_compiler.validation", level="INFO") as logs,
        ):
            result = run_controlled_process(
                ControlledProcessSpec(
                    name="environment-check",
                    argv=(
                        sys.executable,
                        "-c",
                        "import os; print(os.environ['VALIDATION_SECRET'])",
                    ),
                    cwd=Path(directory),
                    timeout_seconds=5,
                    secrets=(secret,),
                    environment=(("VALIDATION_SECRET", secret),),
                )
            )

        self.assertEqual(result.stdout.strip(), "[REDACTED]")
        self.assertNotIn(secret, "\n".join(logs.output))


class ValidationReportTests(unittest.TestCase):
    def test_process_and_skip_results_have_explicit_phase_and_status(self) -> None:
        with TemporaryDirectory() as directory:
            process = run_controlled_process(
                ControlledProcessSpec(
                    name="successful-check",
                    argv=(sys.executable, "-c", "pass"),
                    cwd=Path(directory),
                    timeout_seconds=5,
                )
            )

        passed = check_from_process_result(
            "quality",
            ValidationPhase.QUALITY,
            required=True,
            result=process,
        )
        skipped = skipped_validation_check(
            "docker-runtime",
            ValidationPhase.DELIVERY,
            required=True,
            reason="Docker daemon is unavailable.",
        )

        self.assertIs(passed.status, ValidationStatus.PASS)
        self.assertIs(passed.phase, ValidationPhase.QUALITY)
        self.assertIs(skipped.status, ValidationStatus.SKIPPED)
        self.assertTrue(skipped.required)

    def test_report_rejects_an_empty_check_set(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            ValidationReport(
                configuration_digest=DIGESTS[0],
                blueprint_digest=DIGESTS[1],
                plan_digest=DIGESTS[2],
                candidate_digest=DIGESTS[3],
                checks=(),
            )

    def test_required_fail_or_skip_cannot_issue_a_verification_credential(self) -> None:
        for status in (ValidationStatus.FAIL, ValidationStatus.SKIPPED):
            with self.subTest(status=status):
                report = ValidationReport(
                    configuration_digest=DIGESTS[0],
                    blueprint_digest=DIGESTS[1],
                    plan_digest=DIGESTS[2],
                    candidate_digest=DIGESTS[3],
                    checks=(ValidationCheck("required", status, required=True),),
                )
                with self.assertRaisesRegex(ValueError, "required"):
                    issue_verification_credential(
                        report,
                        current_candidate_digest=DIGESTS[3],
                    )

    def test_credential_binds_all_digests_and_rejects_a_changed_candidate(self) -> None:
        report = ValidationReport(
            configuration_digest=DIGESTS[0],
            blueprint_digest=DIGESTS[1],
            plan_digest=DIGESTS[2],
            candidate_digest=DIGESTS[3],
            checks=(
                ValidationCheck("required", ValidationStatus.PASS, required=True),
                ValidationCheck("optional", ValidationStatus.SKIPPED, required=False),
            ),
        )

        credential = issue_verification_credential(
            report,
            current_candidate_digest=DIGESTS[3],
        )

        self.assertEqual(credential.configuration_digest, DIGESTS[0])
        self.assertEqual(credential.blueprint_digest, DIGESTS[1])
        self.assertEqual(credential.plan_digest, DIGESTS[2])
        self.assertEqual(credential.candidate_digest, DIGESTS[3])
        self.assertEqual(len(credential.verification_digest), 64)
        with self.assertRaisesRegex(ValueError, "changed"):
            issue_verification_credential(
                report,
                current_candidate_digest="e" * 64,
            )


class StaticSafetyScanTests(unittest.TestCase):
    def assemble_candidate(self, root: Path, content: str) -> CandidateAssemblyResult:
        catalog = load_blueprint_catalog(Path(__file__).parents[2] / "blueprints")
        plan = compile_blueprint_plan(catalog, ("final-project-assembly",))
        workspace = root / "workspace"
        workspace.mkdir()
        return assemble_candidate_project(
            catalog,
            plan,
            workspace,
            values={"project_name": "Example", "package_name": "example"},
            generated_files=(
                GeneratedCandidateFile(
                    path="diagnostic.txt",
                    owner="final-project-assembly",
                    content=content.encode(),
                ),
            ),
        )

    def test_rejects_placeholders_generation_paths_and_secrets_without_echoing_them(self) -> None:
        secret = "private-token"
        for unsafe_kind in ("placeholder", "path", "secret"):
            with self.subTest(unsafe_kind=unsafe_kind), TemporaryDirectory() as directory:
                forbidden = Path(directory).resolve() / "generator-workspace"
                unsafe_value = {
                    "placeholder": "${package_name}",
                    "path": forbidden.as_posix(),
                    "secret": secret,
                }[unsafe_kind]
                result = self.assemble_candidate(Path(directory), unsafe_value)
                check = scan_candidate_static_safety(
                    result,
                    forbidden_absolute_paths=(forbidden,),
                    secrets=(secret,),
                )

                self.assertIs(check.status, ValidationStatus.FAIL)
                self.assertNotIn(unsafe_value, check.diagnostics)

    def test_scan_is_read_only_and_passes_a_safe_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            result = self.assemble_candidate(Path(directory), "safe generated content")

            check = scan_candidate_static_safety(
                result,
                forbidden_absolute_paths=(Path(directory).resolve() / "generator-workspace",),
                secrets=("private-token",),
            )

            self.assertIs(check.status, ValidationStatus.PASS)
            verify_candidate_digest(result)

    def test_only_the_fixed_compose_runtime_placeholder_is_allowed(self) -> None:
        cases = (
            ('ports: ["${APPLICATION_PORT:-8000}:8000"]', ValidationStatus.PASS),
            ('ports: ["${APPLICATION_PORT:-9000}:8000"]', ValidationStatus.FAIL),
            ('ports: ["${APPLICATION_PORT}:8000"]', ValidationStatus.FAIL),
        )

        for content, expected_status in cases:
            with self.subTest(content=content), TemporaryDirectory() as directory:
                result = self.assemble_candidate(Path(directory), content)

                check = scan_candidate_static_safety(
                    result,
                    forbidden_absolute_paths=(Path(directory).resolve() / "generator-workspace",),
                    secrets=("private-token",),
                )

                self.assertIs(check.status, expected_status)


if __name__ == "__main__":
    unittest.main()
