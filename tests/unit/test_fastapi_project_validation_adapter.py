from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.fastapi_project_validation_adapter import (
    FastApiValidationRuntime,
    build_fastapi_project_validation_adapter_registration,
    execute_fastapi_project_validation,
)
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    FASTAPI_VALIDATION_ADAPTER_KEY,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import ProjectValidationRequest
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.v1_validation_executor import V1_VALIDATION_REPORT_GATES
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)


def _request(root: Path, runtime_context: object) -> ProjectValidationRequest:
    recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)
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
            _answers_json='{"database":"none","delivery":"none","package_name":"example"}',
        ),
        recipe=recipe,
        candidate=candidate,
        configuration_digest="0" * 64,
        blueprint_digest="1" * 64,
        required_validations=("pytest",),
        validation_environment=root / "validation",
        runtime_context=runtime_context,
    )


class FastApiProjectValidationAdapterTests(unittest.TestCase):
    def test_registration_exposes_only_the_fixed_v1_gate_set(self) -> None:
        registration = build_fastapi_project_validation_adapter_registration()

        self.assertEqual(registration.adapter_key, FASTAPI_VALIDATION_ADAPTER_KEY)
        self.assertEqual(registration.validation_gates, V1_VALIDATION_REPORT_GATES)
        self.assertNotIn("shell-hook", registration.validation_gates)

    def test_rejects_an_untrusted_runtime_context(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(Path(directory), {"DATABASE_URL": "secret"})

            with self.assertRaises(TypeError):
                execute_fastapi_project_validation(request)

    def test_delegates_runtime_only_values_to_the_bounded_v1_executor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv_executable = root / "uv"
            docker_executable = root / "docker"

            def process_runner(specification: object) -> ControlledProcessResult:
                del specification
                return ControlledProcessResult(0, False, "", "", False, 1)

            def password_factory() -> str:
                return "compose-password"

            runtime = FastApiValidationRuntime(
                package_name="example",
                uv_executable=uv_executable,
                database_url="postgresql+asyncpg://user:secret@localhost/example",
                run_id="validation-run",
                docker_executable=docker_executable,
                forbidden_absolute_paths=(root / "capsule",),
                secrets=("secret",),
                process_runner=process_runner,
                compose_password_factory=password_factory,
            )
            self.assertNotIn("secret", repr(runtime))
            request = _request(root, runtime)
            expected = ValidationReport(
                request.configuration_digest,
                request.blueprint_digest,
                request.candidate.plan_digest,
                request.candidate.digest,
                (ValidationCheck("pytest", ValidationStatus.PASS, required=True),),
            )

            with patch(
                "scaffold_compiler.fastapi_project_validation_adapter.execute_v1_validation",
                return_value=expected,
            ) as execute_validation:
                report = execute_fastapi_project_validation(request)

            self.assertIs(report, expected)
            execute_validation.assert_called_once_with(
                request.candidate,
                request.configuration_digest,
                request.blueprint_digest,
                request.required_validations,
                package_name="example",
                uv_executable=uv_executable,
                validation_environment=request.validation_environment,
                database_url="postgresql+asyncpg://user:secret@localhost/example",
                run_id="validation-run",
                docker_executable=docker_executable,
                forbidden_absolute_paths=(root / "capsule",),
                secrets=("secret",),
                process_runner=process_runner,
                compose_password_factory=password_factory,
            )


if __name__ == "__main__":
    unittest.main()
