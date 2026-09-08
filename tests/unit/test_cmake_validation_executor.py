from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.cmake_validation_executor import (
    CMAKE_VALIDATION_REPORT_GATES,
    execute_cmake_validation,
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
    content = b"cmake_minimum_required(VERSION 3.20)\n"
    (candidate_root / "CMakeLists.txt").write_bytes(content)
    record = CandidateFileRecord(
        path="CMakeLists.txt",
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


def successful_result(*, stdout: str = "") -> ControlledProcessResult:
    return ControlledProcessResult(0, False, stdout, "", False, 1)


class CMakeValidationExecutorTests(unittest.TestCase):
    def test_uses_fixed_commands_and_keeps_build_output_outside_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake.exe"
            ctest = root / "ctest.exe"
            cmake.write_bytes(b"")
            ctest.write_bytes(b"")
            candidate = make_candidate(root)
            specifications: list[ControlledProcessSpec] = []

            def record(specification: ControlledProcessSpec) -> ControlledProcessResult:
                specifications.append(specification)
                if specification.name == "executable-run":
                    return successful_result(stdout="scaffold compiler c example\n")
                return successful_result()

            report = execute_cmake_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                CMAKE_VALIDATION_REPORT_GATES,
                target_name="example_cli",
                cmake_executable=cmake,
                ctest_executable=ctest,
                validation_environment=root / "validation-env",
                process_runner=record,
            )

            self.assertEqual(
                [item.name for item in specifications],
                list(CMAKE_VALIDATION_REPORT_GATES),
            )
            build_root = (root / "validation-env" / "build").resolve()
            self.assertEqual(
                specifications[0].argv,
                (
                    str(cmake.resolve()),
                    "-S",
                    str(candidate.root.resolve()),
                    "-B",
                    str(build_root),
                    "-G",
                    "Ninja",
                    "-DCMAKE_BUILD_TYPE=Debug",
                    "-DBUILD_TESTING=ON",
                ),
            )
            self.assertEqual(
                specifications[1].argv,
                (str(cmake.resolve()), "--build", str(build_root), "--config", "Debug"),
            )
            self.assertEqual(
                specifications[2].argv,
                (
                    str(ctest.resolve()),
                    "--test-dir",
                    str(build_root),
                    "--output-on-failure",
                    "-C",
                    "Debug",
                ),
            )
            executable_name = "example_cli.exe" if os.name == "nt" else "example_cli"
            self.assertEqual(specifications[3].argv, (str(build_root / executable_name),))
            self.assertTrue(all(item.cwd == candidate.root for item in specifications))
            self.assertTrue(all(item.output_limit_bytes == 65_536 for item in specifications))
            self.assertTrue(all(item.timeout_seconds > 0 for item in specifications))
            self.assertFalse(build_root.is_relative_to(candidate.root))
            issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_missing_cmake_marks_every_required_gate_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = make_candidate(root)

            report = execute_cmake_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                CMAKE_VALIDATION_REPORT_GATES,
                target_name="example_cli",
                cmake_executable=None,
                ctest_executable=None,
                validation_environment=root / "validation-env",
                process_runner=lambda specification: self.fail("process must not run"),
            )

            self.assertTrue(all(check.required for check in report.checks))
            self.assertTrue(
                all(check.status is ValidationStatus.SKIPPED for check in report.checks)
            )
            self.assertTrue(all("CMake" in check.diagnostics for check in report.checks))
            with self.assertRaises(ValueError):
                issue_verification_credential(report, current_candidate_digest=candidate.digest)

    def test_each_failed_stage_skips_all_dependent_stages(self) -> None:
        cases = {
            "cmake-configure": [
                ValidationStatus.FAIL,
                ValidationStatus.SKIPPED,
                ValidationStatus.SKIPPED,
                ValidationStatus.SKIPPED,
            ],
            "cmake-build": [
                ValidationStatus.PASS,
                ValidationStatus.FAIL,
                ValidationStatus.SKIPPED,
                ValidationStatus.SKIPPED,
            ],
            "ctest": [
                ValidationStatus.PASS,
                ValidationStatus.PASS,
                ValidationStatus.FAIL,
                ValidationStatus.SKIPPED,
            ],
        }
        for failing_name, expected_statuses in cases.items():
            with self.subTest(failing_name=failing_name), TemporaryDirectory() as directory:
                root = Path(directory)
                cmake = root / "cmake.exe"
                ctest = root / "ctest.exe"
                cmake.write_bytes(b"")
                ctest.write_bytes(b"")
                candidate = make_candidate(root)
                names: list[str] = []

                def fail_stage(
                    specification: ControlledProcessSpec,
                    *,
                    recorded_names: list[str] = names,
                    selected_failure: str = failing_name,
                ) -> ControlledProcessResult:
                    recorded_names.append(specification.name)
                    if specification.name == selected_failure:
                        return ControlledProcessResult(1, False, "", "failure", False, 1)
                    return successful_result()

                report = execute_cmake_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    CMAKE_VALIDATION_REPORT_GATES,
                    target_name="example_cli",
                    cmake_executable=cmake,
                    ctest_executable=ctest,
                    validation_environment=root / "validation-env",
                    process_runner=fail_stage,
                )

                self.assertEqual(names[-1], failing_name)
                self.assertEqual([check.status for check in report.checks], expected_statuses)

    def test_timeout_and_output_overflow_are_failures(self) -> None:
        failure_results = (
            ControlledProcessResult(-1, True, "", "", False, 1),
            ControlledProcessResult(0, False, "large", "", True, 1),
        )
        for failure_result in failure_results:
            with self.subTest(result=failure_result), TemporaryDirectory() as directory:
                root = Path(directory)
                cmake = root / "cmake.exe"
                ctest = root / "ctest.exe"
                cmake.write_bytes(b"")
                ctest.write_bytes(b"")
                candidate = make_candidate(root)

                report = execute_cmake_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    CMAKE_VALIDATION_REPORT_GATES,
                    target_name="example_cli",
                    cmake_executable=cmake,
                    ctest_executable=ctest,
                    validation_environment=root / "validation-env",
                    process_runner=lambda specification, result=failure_result: result,
                )

                self.assertEqual(report.checks[0].status, ValidationStatus.FAIL)
                self.assertIn(
                    "timeout" if failure_result.timed_out else "output limit",
                    report.checks[0].diagnostics.lower(),
                )

    def test_executable_output_must_match_the_recipe_contract(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake.exe"
            ctest = root / "ctest.exe"
            cmake.write_bytes(b"")
            ctest.write_bytes(b"")
            candidate = make_candidate(root)

            def wrong_output(specification: ControlledProcessSpec) -> ControlledProcessResult:
                if specification.name == "executable-run":
                    return successful_result(stdout="unexpected\n")
                return successful_result()

            report = execute_cmake_validation(
                candidate,
                "0" * 64,
                "1" * 64,
                CMAKE_VALIDATION_REPORT_GATES,
                target_name="example_cli",
                cmake_executable=cmake,
                ctest_executable=ctest,
                validation_environment=root / "validation-env",
                process_runner=wrong_output,
            )

            self.assertEqual(report.checks[-1].status, ValidationStatus.FAIL)
            self.assertIn("output", report.checks[-1].diagnostics.lower())

    def test_external_process_that_changes_candidate_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake.exe"
            ctest = root / "ctest.exe"
            cmake.write_bytes(b"")
            ctest.write_bytes(b"")
            candidate = make_candidate(root)

            def mutate_candidate(specification: ControlledProcessSpec) -> ControlledProcessResult:
                (candidate.root / "unexpected.txt").write_text("changed\n", encoding="utf-8")
                return successful_result()

            with self.assertRaises(CandidateChangedError):
                execute_cmake_validation(
                    candidate,
                    "0" * 64,
                    "1" * 64,
                    ("cmake-configure",),
                    target_name="example_cli",
                    cmake_executable=cmake,
                    ctest_executable=ctest,
                    validation_environment=root / "validation-env",
                    process_runner=mutate_candidate,
                )


if __name__ == "__main__":
    unittest.main()
