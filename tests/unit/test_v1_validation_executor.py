from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
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
                uv_executable=uv,
                forbidden_absolute_paths=(root / "outside",),
                process_runner=record,
            )

            self.assertEqual(len(specifications), 6)
            self.assertEqual(specifications[0].argv[1:], ("sync", "--frozen"))
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
                ("application-start",),
                uv_executable=uv,
                forbidden_absolute_paths=(root / "outside",),
                process_runner=lambda specification: successful_result(),
            )

            check = next(item for item in report.checks if item.name == "application-start")
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
                uv_executable=uv,
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
                uv_executable=uv,
                forbidden_absolute_paths=(root / "outside",),
                process_runner=unexpected_process,
            )

            self.assertEqual(calls, 0)
            self.assertEqual(report.checks[0].status, ValidationStatus.FAIL)
            self.assertEqual(report.checks[1].status, ValidationStatus.SKIPPED)
            self.assertEqual(report.checks[2].status, ValidationStatus.SKIPPED)


if __name__ == "__main__":
    unittest.main()
