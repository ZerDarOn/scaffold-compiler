from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.cmake_project_validation_adapter import (
    CMakeValidationRuntime,
    build_cmake_project_validation_adapter_registration,
    execute_cmake_project_validation,
)
from scaffold_compiler.cmake_validation_executor import CMAKE_VALIDATION_REPORT_GATES
from scaffold_compiler.project_recipe_registry import (
    CMAKE_RECIPE_ID,
    CMAKE_VALIDATION_ADAPTER_KEY,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import ProjectValidationRequest
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)


def _request(root: Path, runtime_context: object) -> ProjectValidationRequest:
    recipe = build_builtin_project_recipe_registry().get(CMAKE_RECIPE_ID)
    candidate = CandidateAssemblyResult(
        root=root / "candidate",
        files=(),
        digest="3" * 64,
        plan_digest="2" * 64,
    )
    return ProjectValidationRequest(
        configuration=RecipeProjectConfiguration(
            schema_version=2,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            project_name="Example",
            target_directory=root / "delivery",
            _answers_json='{"strict_warnings":true,"target_name":"example_cli"}',
        ),
        recipe=recipe,
        candidate=candidate,
        configuration_digest="0" * 64,
        blueprint_digest="1" * 64,
        required_validations=CMAKE_VALIDATION_REPORT_GATES,
        validation_environment=root / "validation",
        runtime_context=runtime_context,
    )


class CMakeProjectValidationAdapterTests(unittest.TestCase):
    def test_registration_exposes_only_the_fixed_cmake_gate_set(self) -> None:
        registration = build_cmake_project_validation_adapter_registration()

        self.assertEqual(registration.adapter_key, CMAKE_VALIDATION_ADAPTER_KEY)
        self.assertEqual(registration.validation_gates, CMAKE_VALIDATION_REPORT_GATES)
        self.assertNotIn("shell-hook", registration.validation_gates)

    def test_rejects_an_untrusted_runtime_context(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(Path(directory), {"cmake": "from-manifest"})

            with self.assertRaises(TypeError):
                execute_cmake_project_validation(request)

    def test_delegates_only_trusted_runtime_values_to_the_bounded_executor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake"
            ctest = root / "ctest"

            def process_runner(specification: object) -> ControlledProcessResult:
                del specification
                return ControlledProcessResult(0, False, "", "", False, 1)

            runtime = CMakeValidationRuntime(
                target_name="example_cli",
                cmake_executable=cmake,
                ctest_executable=ctest,
                process_runner=process_runner,
            )
            self.assertNotIn(str(cmake), repr(runtime))
            request = _request(root, runtime)
            expected = ValidationReport(
                request.configuration_digest,
                request.blueprint_digest,
                request.candidate.plan_digest,
                request.candidate.digest,
                (
                    ValidationCheck(
                        "cmake-configure",
                        ValidationStatus.PASS,
                        required=True,
                    ),
                ),
            )

            with patch(
                "scaffold_compiler.cmake_project_validation_adapter.execute_cmake_validation",
                return_value=expected,
            ) as execute_validation:
                report = execute_cmake_project_validation(request)

            self.assertIs(report, expected)
            execute_validation.assert_called_once_with(
                request.candidate,
                request.configuration_digest,
                request.blueprint_digest,
                request.required_validations,
                target_name="example_cli",
                cmake_executable=cmake,
                ctest_executable=ctest,
                validation_environment=request.validation_environment,
                process_runner=process_runner,
            )


if __name__ == "__main__":
    unittest.main()
