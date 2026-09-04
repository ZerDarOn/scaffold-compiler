from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.v1_validation_executor import execute_v1_validation
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationStatus,
    issue_verification_credential,
)


def make_candidate(root: Path) -> CandidateAssemblyResult:
    candidate_root = root / "candidate"
    candidate_root.mkdir()
    content = b"value = 1\n"
    (candidate_root / "module.py").write_bytes(content)
    record = CandidateFileRecord(
        path="module.py",
        owner="test",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return CandidateAssemblyResult(
        root=candidate_root,
        files=(record,),
        digest=calculate_candidate_digest(candidate_root, (record,)),
        plan_digest="2" * 64,
    )


def successful_result() -> ControlledProcessResult:
    return ControlledProcessResult(0, False, "", "", False, 1)


class V1ValidationExecutorTests(unittest.TestCase):
    def test_validation_environment_inside_candidate_is_rejected_before_processes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)

            with self.assertRaisesRegex(ValueError, "outside the candidate"):
                execute_v1_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    ("pytest",),
                    package_name="example",
                    uv_executable=uv,
                    validation_environment=candidate.root / ".venv",
                    forbidden_absolute_paths=(root / "outside",),
                    process_runner=lambda specification: successful_result(),
                )

    def test_supported_code_gates_use_fixed_argument_arrays_and_can_issue_credential(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            specifications: list[ControlledProcessSpec] = []

            def record(specification: ControlledProcessSpec) -> ControlledProcessResult:
                specifications.append(specification)
                return successful_result()

            required = ("mypy", "pytest", "python-syntax", "ruff-format", "ruff-lint")
            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                required,
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=record,
            )

            self.assertEqual(len(specifications), 6)
            self.assertEqual(
                specifications[0].argv[1:],
                ("sync", "--frozen", "--no-install-project"),
            )
            self.assertTrue(
                all(specification.cwd == candidate.root for specification in specifications)
            )
            self.assertTrue(
                all(isinstance(specification.argv, tuple) for specification in specifications)
            )
            credential = issue_verification_credential(
                report,
                current_candidate_digest=candidate.digest,
            )
            self.assertEqual(credential.candidate_digest, candidate.digest)

    def test_unconnected_runtime_gate_is_required_and_cannot_issue_credential(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                ("postgres-connect",),
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=lambda specification: successful_result(),
            )

            check = next(item for item in report.checks if item.name == "postgres-connect")
            self.assertEqual(check.status, ValidationStatus.SKIPPED)
            self.assertTrue(check.required)
            with self.assertRaises(ValueError):
                issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_failed_locked_install_skips_code_gates_without_running_them(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            calls = 0

            def fail_install(specification: ControlledProcessSpec) -> ControlledProcessResult:
                nonlocal calls
                calls += 1
                return ControlledProcessResult(1, False, "", "", False, 1)

            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                ("mypy", "pytest"),
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=fail_install,
            )

            self.assertEqual(calls, 1)
            self.assertEqual(
                [check.status for check in report.checks if check.name in {"mypy", "pytest"}],
                [ValidationStatus.SKIPPED, ValidationStatus.SKIPPED],
            )

    def test_failed_static_safety_runs_no_external_process(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            changed = b"value = '${MISSING}'\n"
            (candidate.root / "module.py").write_bytes(changed)
            record = CandidateFileRecord(
                path="module.py",
                owner="test",
                size=len(changed),
                sha256=hashlib.sha256(changed).hexdigest(),
            )
            unsafe_candidate = CandidateAssemblyResult(
                root=candidate.root,
                files=(record,),
                digest=calculate_candidate_digest(candidate.root, (record,)),
                plan_digest=candidate.plan_digest,
            )
            calls = 0

            def unexpected_process(specification: ControlledProcessSpec) -> ControlledProcessResult:
                nonlocal calls
                calls += 1
                return successful_result()

            report = execute_v1_validation(
                unsafe_candidate,
                "0" * 64,
                "1" * 64,
                ("pytest",),
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=unexpected_process,
            )

            self.assertEqual(calls, 0)
            self.assertEqual(report.checks[0].status, ValidationStatus.FAIL)
            self.assertEqual(report.checks[1].status, ValidationStatus.SKIPPED)
            self.assertEqual(report.checks[2].status, ValidationStatus.SKIPPED)

    def test_http_gates_use_fixed_code_and_pass_package_and_endpoint_as_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            specifications: list[ControlledProcessSpec] = []

            def record(specification: ControlledProcessSpec) -> ControlledProcessResult:
                specifications.append(specification)
                return successful_result()

            required = ("application-start", "health-live", "health-ready", "openapi")
            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                required,
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=record,
            )

            runtime_specs = specifications[1:]
            self.assertEqual(len(runtime_specs), 4)
            self.assertTrue(
                all(
                    specification.argv[1:5] == ("run", "--no-sync", "python", "-c")
                    for specification in runtime_specs
                )
            )
            self.assertTrue(
                all(
                    ("UV_PROJECT_ENVIRONMENT", str(root / "validation-env" / "venv"))
                    in specification.environment
                    for specification in specifications
                )
            )
            self.assertTrue(
                all(
                    ("MYPY_CACHE_DIR", str(root / "validation-env" / "mypy-cache"))
                    in specification.environment
                    for specification in specifications
                )
            )
            self.assertTrue(
                all(specification.argv[-2] == "example.asgi" for specification in runtime_specs)
            )
            self.assertEqual(
                {specification.argv[-1] for specification in runtime_specs},
                {"/api/v1", "/health/live", "/health/ready", "/openapi.json"},
            )
            issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_postgres_gates_receive_database_url_only_through_redacted_environment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            specifications: list[ControlledProcessSpec] = []
            database_url = "postgresql+asyncpg://validator:secret@127.0.0.1:5432/test"

            def record(specification: ControlledProcessSpec) -> ControlledProcessResult:
                specifications.append(specification)
                return successful_result()

            required = (
                "alembic-upgrade",
                "database-readiness",
                "postgres-connect",
                "session-rollback",
            )
            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                required,
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                database_url=database_url,
                forbidden_absolute_paths=(root / "outside",),
                process_runner=record,
            )

            self.assertEqual(len(specifications), 5)
            self.assertTrue(
                all(database_url not in specification.argv for specification in specifications)
            )
            self.assertTrue(
                all(database_url in specification.secrets for specification in specifications)
            )
            self.assertTrue(
                all(
                    ("DATABASE_URL", database_url) in specification.environment
                    for specification in specifications
                )
            )
            issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_postgres_project_without_database_url_skips_http_and_database_gates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                ("application-start", "postgres-connect"),
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=lambda specification: successful_result(),
            )

            statuses = {check.name: check.status for check in report.checks}
            self.assertEqual(statuses["application-start"], ValidationStatus.SKIPPED)
            self.assertEqual(statuses["postgres-connect"], ValidationStatus.SKIPPED)

    def test_failed_postgres_connection_prevents_migration_side_effect(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)
            names: list[str] = []

            def fail_connection(specification: ControlledProcessSpec) -> ControlledProcessResult:
                names.append(specification.name)
                if specification.name == "postgres-connect":
                    return ControlledProcessResult(1, False, "", "", False, 1)
                return successful_result()

            report = execute_v1_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                ("alembic-upgrade", "postgres-connect"),
                package_name="example",
                uv_executable=uv,
                validation_environment=root / "validation-env",
                database_url="postgresql+asyncpg://validator@127.0.0.1:5432/test",
                forbidden_absolute_paths=(root / "outside",),
                process_runner=fail_connection,
            )

            self.assertEqual(names, ["locked-install", "postgres-connect"])
            statuses = {check.name: check.status for check in report.checks}
            self.assertEqual(statuses["postgres-connect"], ValidationStatus.FAIL)
            self.assertEqual(statuses["alembic-upgrade"], ValidationStatus.SKIPPED)

    def test_external_check_that_changes_candidate_cannot_produce_a_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv.exe"
            uv.write_bytes(b"")
            candidate = make_candidate(root)

            def mutate_candidate(specification: ControlledProcessSpec) -> ControlledProcessResult:
                (candidate.root / "unexpected.txt").write_text("changed\n", encoding="utf-8")
                return successful_result()

            with self.assertRaises(CandidateChangedError):
                execute_v1_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    ("pytest",),
                    package_name="example",
                    uv_executable=uv,
                    validation_environment=root / "validation-env",
                    forbidden_absolute_paths=(root / "outside",),
                    process_runner=mutate_candidate,
                )


if __name__ == "__main__":
    unittest.main()
