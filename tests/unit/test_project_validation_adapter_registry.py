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
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationAdapterRegistryError,
    ProjectValidationRequest,
    build_project_validation_adapter_registry,
    execute_project_validation,
)
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.validation import (
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)


def _candidate(root: Path) -> CandidateAssemblyResult:
    candidate_root = root / "candidate"
    candidate_root.mkdir()
    content = b"ok\n"
    (candidate_root / "result.txt").write_bytes(content)
    record = CandidateFileRecord(
        path="result.txt",
        owner="core",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return CandidateAssemblyResult(
        root=candidate_root,
        files=(record,),
        digest=calculate_candidate_digest(candidate_root, (record,)),
        plan_digest="2" * 64,
    )


def _request(
    root: Path, *, required: tuple[str, ...] = ("unit-tests",)
) -> ProjectValidationRequest:
    recipe = ProjectRecipe(
        recipe_id="test-recipe",
        version="1.0.0",
        answer_parser_key="test-answers",
        assembly_adapter_key="test-assembly",
        validation_adapter_key="test-validation",
        required_capabilities=("core",),
        capability_rules=(),
        allowed_blueprint_ids=("core",),
        allowed_validation_gates=("unit-tests",),
        prerequisites=(),
    )
    return ProjectValidationRequest(
        configuration=RecipeProjectConfiguration(
            schema_version=2,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            project_name="Test",
            target_directory=root / "delivery",
            _answers_json="{}",
        ),
        recipe=recipe,
        candidate=_candidate(root),
        configuration_digest="0" * 64,
        blueprint_digest="1" * 64,
        required_validations=required,
        validation_environment=root / "validation",
        runtime_context=object(),
    )


def _passing_adapter(request: ProjectValidationRequest) -> ValidationReport:
    return ValidationReport(
        configuration_digest=request.configuration_digest,
        blueprint_digest=request.blueprint_digest,
        plan_digest=request.candidate.plan_digest,
        candidate_digest=request.candidate.digest,
        checks=(
            ValidationCheck("static-safety", ValidationStatus.PASS, required=True),
            *(
                ValidationCheck(name, ValidationStatus.PASS, required=True)
                for name in request.required_validations
            ),
        ),
    )


def _registration(
    adapter: object = _passing_adapter,
    *,
    key: str = "test-validation",
    gates: tuple[str, ...] = ("static-safety", "unit-tests"),
) -> ProjectValidationAdapterRegistration:
    return ProjectValidationAdapterRegistration(
        adapter_key=key,
        validation_gates=gates,
        adapter=adapter,  # type: ignore[arg-type]
    )


class ProjectValidationAdapterRegistryTests(unittest.TestCase):
    def test_rejects_duplicate_adapter_or_gate_providers(self) -> None:
        invalid_registrations = (
            (_registration(), _registration()),
            (_registration(), _registration(key="other-validation", gates=("unit-tests",))),
        )
        expected_codes = ("duplicate_validation_adapter", "duplicate_validation_gate_provider")
        for registrations, expected_code in zip(
            invalid_registrations,
            expected_codes,
            strict=True,
        ):
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(ProjectValidationAdapterRegistryError) as error_context,
            ):
                build_project_validation_adapter_registry(registrations)

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_unknown_adapter_or_unprovided_required_gate_before_execution(self) -> None:
        for registry, required, expected_code in (
            (build_project_validation_adapter_registry(()), ("unit-tests",), "unknown_adapter"),
            (
                build_project_validation_adapter_registry((_registration(gates=()),)),
                ("unit-tests",),
                "unsupported_required_gate",
            ),
        ):
            with self.subTest(expected_code=expected_code), TemporaryDirectory() as directory:
                request = _request(Path(directory), required=required)
                with self.assertRaises(ProjectValidationAdapterRegistryError) as error_context:
                    execute_project_validation(request, registry)

                self.assertEqual(error_context.exception.code, expected_code)

    def test_accepts_a_complete_bound_report(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(Path(directory))
            registry = build_project_validation_adapter_registry((_registration(),))

            report = execute_project_validation(request, registry)

            self.assertEqual(report.candidate_digest, request.candidate.digest)
            self.assertEqual(report.plan_digest, request.candidate.plan_digest)

    def test_rejects_missing_unrequired_or_unknown_report_gates(self) -> None:
        def missing(request: ProjectValidationRequest) -> ValidationReport:
            report = _passing_adapter(request)
            return ValidationReport(
                report.configuration_digest,
                report.blueprint_digest,
                report.plan_digest,
                report.candidate_digest,
                (report.checks[0],),
            )

        def unrequired(request: ProjectValidationRequest) -> ValidationReport:
            report = _passing_adapter(request)
            return ValidationReport(
                report.configuration_digest,
                report.blueprint_digest,
                report.plan_digest,
                report.candidate_digest,
                (
                    report.checks[0],
                    ValidationCheck("unit-tests", ValidationStatus.PASS, required=False),
                ),
            )

        def unknown(request: ProjectValidationRequest) -> ValidationReport:
            report = _passing_adapter(request)
            return ValidationReport(
                report.configuration_digest,
                report.blueprint_digest,
                report.plan_digest,
                report.candidate_digest,
                (*report.checks, ValidationCheck("shell-hook", ValidationStatus.PASS, True)),
            )

        for adapter in (missing, unrequired, unknown):
            with self.subTest(adapter=adapter), TemporaryDirectory() as directory:
                request = _request(Path(directory))
                registry = build_project_validation_adapter_registry((_registration(adapter),))

                with self.assertRaises(ProjectValidationAdapterRegistryError):
                    execute_project_validation(request, registry)

    def test_rejects_report_digest_mismatch_and_candidate_mutation(self) -> None:
        def mismatched(request: ProjectValidationRequest) -> ValidationReport:
            report = _passing_adapter(request)
            return ValidationReport(
                "f" * 64,
                report.blueprint_digest,
                report.plan_digest,
                report.candidate_digest,
                report.checks,
            )

        def mutating(request: ProjectValidationRequest) -> ValidationReport:
            (request.candidate.root / "result.txt").write_text("changed", encoding="utf-8")
            return _passing_adapter(request)

        for adapter, expected_code in (
            (mismatched, "validation_report_binding_mismatch"),
            (mutating, "validation_candidate_changed"),
        ):
            with self.subTest(adapter=adapter), TemporaryDirectory() as directory:
                request = _request(Path(directory))
                registry = build_project_validation_adapter_registry((_registration(adapter),))

                with self.assertRaises(ProjectValidationAdapterRegistryError) as error_context:
                    execute_project_validation(request, registry)

                self.assertEqual(error_context.exception.code, expected_code)

    def test_failure_logs_trusted_identifiers_without_opaque_runtime_secrets(self) -> None:
        secret = "not-for-logs"

        def failing(request: ProjectValidationRequest) -> ValidationReport:
            raise RuntimeError(f"adapter failed with {request.runtime_context}")

        with TemporaryDirectory() as directory:
            request = _request(Path(directory))
            request = ProjectValidationRequest(
                configuration=request.configuration,
                recipe=request.recipe,
                candidate=request.candidate,
                configuration_digest=request.configuration_digest,
                blueprint_digest=request.blueprint_digest,
                required_validations=request.required_validations,
                validation_environment=request.validation_environment,
                runtime_context=secret,
            )
            registry = build_project_validation_adapter_registry((_registration(failing),))

            with (
                self.assertLogs(
                    "scaffold_compiler.project_validation_adapter_registry", level="INFO"
                ) as captured,
                self.assertRaises(ProjectValidationAdapterRegistryError),
            ):
                execute_project_validation(request, registry)

            logs = "\n".join(captured.output)
            self.assertIn("recipe_id=test-recipe", logs)
            self.assertIn("gates=unit-tests", logs)
            self.assertNotIn(secret, logs)


if __name__ == "__main__":
    unittest.main()
