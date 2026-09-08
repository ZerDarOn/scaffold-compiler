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
from scaffold_compiler.go_validation_executor import (
    GO_VALIDATION_REPORT_GATES,
    execute_go_validation,
)
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationStatus,
    issue_verification_credential,
)


def make_candidate(root: Path) -> CandidateAssemblyResult:
    candidate_root = root / "candidate"
    candidate_root.mkdir()
    records = []
    for name, content in {
        "calculator.go": b"package main\n",
        "calculator_test.go": b"package main\n",
        "go.mod": b"module example.com/tool\n\ngo 1.22\n",
        "main.go": b"package main\n",
    }.items():
        (candidate_root / name).write_bytes(content)
        records.append(
            CandidateFileRecord(
                path=name,
                owner="test",
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    frozen = tuple(records)
    return CandidateAssemblyResult(
        root=candidate_root,
        files=frozen,
        digest=calculate_candidate_digest(candidate_root, frozen),
        plan_digest="2" * 64,
    )


def successful_result(*, stdout: str = "") -> ControlledProcessResult:
    return ControlledProcessResult(0, False, stdout, "", False, 1)


class GoValidationExecutorTests(unittest.TestCase):
    def test_uses_fixed_commands_and_keeps_all_writes_outside_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            go = root / "go.exe"
            gofmt = root / "gofmt.exe"
            go.write_bytes(b"")
            gofmt.write_bytes(b"")
            candidate = make_candidate(root)
            specifications: list[ControlledProcessSpec] = []

            def record(specification: ControlledProcessSpec) -> ControlledProcessResult:
                specifications.append(specification)
                output = (
                    "scaffold compiler go example\n"
                    if specification.name == "go-executable-run"
                    else ""
                )
                return successful_result(stdout=output)

            report = execute_go_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                GO_VALIDATION_REPORT_GATES,
                binary_name="example-tool",
                go_executable=go,
                gofmt_executable=gofmt,
                validation_environment=root / "validation-env",
                process_runner=record,
            )

            self.assertEqual(
                [item.name for item in specifications], list(GO_VALIDATION_REPORT_GATES)
            )
            self.assertEqual(specifications[0].argv[1], "-l")
            self.assertEqual(specifications[1].argv[1:], ("test", "./..."))
            self.assertEqual(specifications[2].argv[1], "build")
            build_root = (root / "validation-env" / "build").resolve()
            self.assertEqual(Path(specifications[2].argv[3]).parent, build_root)
            for specification in specifications:
                environment = dict(specification.environment)
                self.assertEqual(environment["GOTOOLCHAIN"], "local")
                self.assertEqual(environment["GOWORK"], "off")
                self.assertFalse(Path(environment["GOCACHE"]).is_relative_to(candidate.root))
            issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_missing_tools_marks_required_gates_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = make_candidate(root)
            report = execute_go_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                GO_VALIDATION_REPORT_GATES,
                binary_name="tool",
                go_executable=None,
                gofmt_executable=None,
                validation_environment=root / "validation-env",
                process_runner=lambda specification: self.fail("process must not run"),
            )

            self.assertTrue(
                all(check.status is ValidationStatus.SKIPPED for check in report.checks)
            )
            with self.assertRaises(ValueError):
                issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_unformatted_source_and_wrong_runtime_output_are_failures(self) -> None:
        cases = (("go-format", "main.go\n"), ("go-executable-run", "wrong\n"))
        for failing_gate, output in cases:
            with self.subTest(failing_gate=failing_gate), TemporaryDirectory() as directory:
                root = Path(directory)
                go = root / "go.exe"
                gofmt = root / "gofmt.exe"
                go.write_bytes(b"")
                gofmt.write_bytes(b"")
                candidate = make_candidate(root)

                def result(
                    specification: ControlledProcessSpec,
                    *,
                    selected_gate: str = failing_gate,
                    selected_output: str = output,
                ) -> ControlledProcessResult:
                    if specification.name == selected_gate:
                        return successful_result(stdout=selected_output)
                    if specification.name == "go-executable-run":
                        return successful_result(stdout="scaffold compiler go example\n")
                    return successful_result()

                report = execute_go_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    GO_VALIDATION_REPORT_GATES,
                    binary_name="tool",
                    go_executable=go,
                    gofmt_executable=gofmt,
                    validation_environment=root / "validation-env",
                    process_runner=result,
                )
                failed = next(check for check in report.checks if check.name == failing_gate)
                self.assertIs(failed.status, ValidationStatus.FAIL)

    def test_external_process_that_changes_candidate_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            gofmt = root / "gofmt.exe"
            gofmt.write_bytes(b"")
            candidate = make_candidate(root)

            def mutate(specification: ControlledProcessSpec) -> ControlledProcessResult:
                (candidate.root / "main.go").write_text("changed\n", encoding="utf-8")
                return successful_result()

            with self.assertRaises(CandidateChangedError):
                execute_go_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    ("go-format",),
                    binary_name="tool",
                    go_executable=None,
                    gofmt_executable=gofmt,
                    validation_environment=root / "validation-env",
                    process_runner=mutate,
                )


if __name__ == "__main__":
    unittest.main()
