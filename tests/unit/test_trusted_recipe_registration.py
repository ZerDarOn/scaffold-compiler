from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TextIO, cast

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.project_assembly_adapter_registry import ProjectAssemblyRequest
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
)
from scaffold_compiler.recipe_project_configuration import (
    JSONValue,
    RecipeProjectConfiguration,
)
from scaffold_compiler.trusted_recipe_registration import (
    CompiledTrustedRecipeRegistrations,
    RecipeQuestionnaireRegistration,
    RecipeQuestionnaireRegistryError,
    RecipeRuntimeFactoryRegistration,
    RecipeRuntimeFactoryRegistryError,
    TrustedRecipeRegistration,
    TrustedRecipeRegistrationError,
    build_recipe_questionnaire_registry,
    build_recipe_runtime_factory_registry,
    compile_trusted_recipe_registrations,
)
from scaffold_compiler.validation import ValidationReport


def _declaration(
    recipe_id: str = "example-cli",
    *,
    answer_key: str = "example-answers",
    assembly_key: str = "example-assembly",
    validation_key: str = "example-validation",
    gates: tuple[str, ...] = ("example-test",),
) -> dict[str, object]:
    return {
        "allowed_blueprints": ["example-project"],
        "allowed_validations": list(gates),
        "answer_parser": answer_key,
        "assembly_adapter": assembly_key,
        "capability_rules": [],
        "id": recipe_id,
        "prerequisites": ["Example tool 1.0 or newer"],
        "required_capabilities": ["example-project"],
        "schema_version": 1,
        "validation_adapter": validation_key,
        "version": "1.0.0",
    }


def _normalize_answers(
    raw_answers: Mapping[str, object],
    project_name: str,
) -> Mapping[str, JSONValue]:
    del raw_answers, project_name
    raise AssertionError("registration compilation must not normalize answers")


def _collect_answers(source: TextIO, sink: TextIO) -> dict[str, JSONValue]:
    del source, sink
    raise AssertionError("registration compilation must not collect answers")


def _assemble(request: ProjectAssemblyRequest) -> CandidateAssemblyResult:
    del request
    raise AssertionError("registration compilation must not assemble a candidate")


def _validate(request: ProjectValidationRequest) -> ValidationReport:
    del request
    raise AssertionError("registration compilation must not validate a candidate")


def _runtime_factory(
    configuration: RecipeProjectConfiguration,
    recipe: ProjectRecipe,
    run_id: str,
) -> object:
    del configuration, recipe, run_id
    raise AssertionError("registration compilation must not create runtime context")


def _registration(
    recipe_id: str = "example-cli",
    *,
    answer_key: str = "example-answers",
    assembly_key: str = "example-assembly",
    validation_key: str = "example-validation",
    gates: tuple[str, ...] = ("example-test",),
    questionnaire_recipe_id: str | None = None,
    runtime_recipe_id: str | None = None,
) -> TrustedRecipeRegistration:
    return TrustedRecipeRegistration(
        declaration=_declaration(
            recipe_id,
            answer_key=answer_key,
            assembly_key=assembly_key,
            validation_key=validation_key,
            gates=gates,
        ),
        answer_parser_key=answer_key,
        answer_normalizer=_normalize_answers,
        questionnaire=RecipeQuestionnaireRegistration(
            recipe_id=questionnaire_recipe_id or recipe_id,
            label="Example command-line project",
            collector=_collect_answers,
        ),
        assembly_adapter_key=assembly_key,
        assembly_adapter=_assemble,
        validation_adapter=ProjectValidationAdapterRegistration(
            adapter_key=validation_key,
            validation_gates=gates,
            adapter=_validate,
        ),
        runtime_factory=RecipeRuntimeFactoryRegistration(
            recipe_id=runtime_recipe_id or recipe_id,
            factory=_runtime_factory,
        ),
    )


def _recipe_from(compiled: CompiledTrustedRecipeRegistrations) -> ProjectRecipe:
    return compiled.recipe_registry.recipes[0]


def _configuration_for(recipe: ProjectRecipe) -> RecipeProjectConfiguration:
    return RecipeProjectConfiguration(
        schema_version=2,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        project_name="Example CLI",
        target_directory=Path("delivery"),
        _answers_json="{}",
    )


class TrustedRecipeRegistrationTests(unittest.TestCase):
    def test_compiles_every_registry_from_one_complete_registration_set(self) -> None:
        registrations = (
            _registration(),
            _registration(
                "other-cli",
                answer_key="other-answers",
                assembly_key="other-assembly",
                validation_key="other-validation",
                gates=("other-test",),
            ),
        )

        with self.assertLogs(
            "scaffold_compiler.trusted_recipe_registration",
            level="INFO",
        ) as logs:
            compiled = compile_trusted_recipe_registrations(registrations)

        self.assertEqual(
            tuple(recipe.recipe_id for recipe in compiled.recipe_registry.recipes),
            ("example-cli", "other-cli"),
        )
        self.assertEqual(tuple(compiled.answer_normalizers), ("example-answers", "other-answers"))
        self.assertEqual(
            tuple(
                item.recipe_id
                for item in compiled.questionnaire_registry.for_recipes(compiled.recipe_registry)
            ),
            ("example-cli", "other-cli"),
        )
        self.assertIs(compiled.assembly_registry.get("example-assembly"), _assemble)
        self.assertEqual(
            compiled.validation_registry.get("other-validation").validation_gates,
            ("other-test",),
        )
        self.assertEqual(
            tuple(item.recipe_id for item in compiled.runtime_factory_registry.registrations),
            ("example-cli", "other-cli"),
        )
        self.assertIn("trusted_recipe_registration_compile_completed recipes=2", logs.output[-1])

    def test_questionnaire_registry_requires_the_exact_recipe_set(self) -> None:
        compiled = compile_trusted_recipe_registrations((_registration(),))
        extra_recipe = replace(_recipe_from(compiled), recipe_id="other-cli")

        with self.assertRaises(RecipeQuestionnaireRegistryError) as error_context:
            compiled.questionnaire_registry.for_recipes(
                type(compiled.recipe_registry)((_recipe_from(compiled), extra_recipe))
            )

        self.assertEqual(error_context.exception.code, "questionnaire_recipe_set_mismatch")

    def test_runtime_factory_registry_dispatches_only_the_matching_recipe(self) -> None:
        called: list[str] = []

        def selected_factory(
            configuration: RecipeProjectConfiguration,
            recipe: ProjectRecipe,
            run_id: str,
        ) -> object:
            del configuration, recipe
            called.append(run_id)
            return "selected-runtime"

        def other_factory(*_args: object) -> object:
            raise AssertionError("unselected runtime factory must not run")

        first = _registration()
        second = _registration(
            "other-cli",
            answer_key="other-answers",
            assembly_key="other-assembly",
            validation_key="other-validation",
            gates=("other-test",),
        )
        compiled = compile_trusted_recipe_registrations(
            (
                replace(
                    first,
                    runtime_factory=replace(first.runtime_factory, factory=selected_factory),
                ),
                replace(
                    second,
                    runtime_factory=replace(second.runtime_factory, factory=other_factory),
                ),
            )
        )
        recipe = _recipe_from(compiled)
        configuration = _configuration_for(recipe)

        runtime = compiled.runtime_factory_registry.create(
            configuration,
            recipe,
            "runtime-run",
        )

        self.assertEqual(runtime, "selected-runtime")
        self.assertEqual(called, ["runtime-run"])

    def test_runtime_factory_registry_rejects_identity_mismatch_before_dispatch(self) -> None:
        called = False

        def factory(*_args: object) -> object:
            nonlocal called
            called = True
            return object()

        registration = _registration()
        compiled = compile_trusted_recipe_registrations(
            (
                replace(
                    registration,
                    runtime_factory=replace(registration.runtime_factory, factory=factory),
                ),
            )
        )
        recipe = _recipe_from(compiled)
        configuration = replace(_configuration_for(recipe), recipe_version="9.9.9")

        with self.assertRaises(RecipeRuntimeFactoryRegistryError) as error_context:
            compiled.runtime_factory_registry.create(configuration, recipe, "runtime-run")

        self.assertEqual(error_context.exception.code, "runtime_request_recipe_mismatch")
        self.assertFalse(called)

    def test_component_registries_reject_duplicate_recipe_bindings(self) -> None:
        questionnaire = _registration().questionnaire
        runtime_factory = _registration().runtime_factory
        cases = (
            (
                lambda: build_recipe_questionnaire_registry((questionnaire, questionnaire)),
                RecipeQuestionnaireRegistryError,
                "duplicate_questionnaire_recipe",
            ),
            (
                lambda: build_recipe_runtime_factory_registry((runtime_factory, runtime_factory)),
                RecipeRuntimeFactoryRegistryError,
                "duplicate_runtime_factory_recipe",
            ),
        )

        for build, expected_error, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(expected_error) as error_context,
            ):
                build()

            registered_error = cast(
                RecipeQuestionnaireRegistryError | RecipeRuntimeFactoryRegistryError,
                error_context.exception,
            )
            self.assertEqual(registered_error.code, expected_code)

    def test_runtime_factory_registry_rejects_an_unregistered_recipe(self) -> None:
        compiled = compile_trusted_recipe_registrations((_registration(),))
        recipe = replace(_recipe_from(compiled), recipe_id="other-cli")

        with self.assertRaises(RecipeRuntimeFactoryRegistryError) as error_context:
            compiled.runtime_factory_registry.create(
                _configuration_for(recipe),
                recipe,
                "runtime-run",
            )

        self.assertEqual(error_context.exception.code, "unknown_runtime_factory")

    def test_rejects_cross_component_identity_mismatches(self) -> None:
        cases = (
            (
                replace(_registration(), answer_parser_key="different-answers"),
                "unknown_answer_parser",
            ),
            (
                replace(_registration(), assembly_adapter_key="different-assembly"),
                "unknown_assembly_adapter",
            ),
            (
                replace(
                    _registration(),
                    validation_adapter=replace(
                        _registration().validation_adapter,
                        adapter_key="different-validation",
                    ),
                ),
                "unknown_validation_adapter",
            ),
            (
                _registration(questionnaire_recipe_id="other-cli"),
                "questionnaire_recipe_mismatch",
            ),
            (
                _registration(runtime_recipe_id="other-cli"),
                "runtime_factory_recipe_mismatch",
            ),
        )

        for registration, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(TrustedRecipeRegistrationError) as error_context,
            ):
                compile_trusted_recipe_registrations((registration,))

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_duplicate_component_keys_and_gate_providers(self) -> None:
        cases = (
            (
                (
                    _registration(),
                    _registration(
                        "other-cli",
                        answer_key="example-answers",
                        assembly_key="other-assembly",
                        validation_key="other-validation",
                        gates=("other-test",),
                    ),
                ),
                "duplicate_answer_parser",
            ),
            (
                (
                    _registration(),
                    _registration(
                        "other-cli",
                        assembly_key="example-assembly",
                        answer_key="other-answers",
                        validation_key="other-validation",
                        gates=("other-test",),
                    ),
                ),
                "duplicate_assembly_adapter",
            ),
            (
                (
                    _registration(),
                    _registration(
                        "other-cli",
                        assembly_key="other-assembly",
                        answer_key="other-answers",
                        validation_key="other-validation",
                        gates=("example-test",),
                    ),
                ),
                "duplicate_validation_gate_provider",
            ),
        )

        for registrations, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(TrustedRecipeRegistrationError) as error_context,
            ):
                compile_trusted_recipe_registrations(registrations)

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_invalid_or_incomplete_callable_components(self) -> None:
        registration = _registration()
        cases = (
            (
                replace(registration, answer_normalizer=None),  # type: ignore[arg-type]
                "invalid_answer_normalizer",
            ),
            (
                replace(
                    registration,
                    questionnaire=replace(registration.questionnaire, collector=None),  # type: ignore[arg-type]
                ),
                "invalid_questionnaire",
            ),
            (
                replace(registration, assembly_adapter=None),  # type: ignore[arg-type]
                "invalid_assembly_adapter",
            ),
            (
                replace(
                    registration,
                    validation_adapter=replace(registration.validation_adapter, adapter=None),  # type: ignore[arg-type]
                ),
                "invalid_validation_adapter",
            ),
            (
                replace(
                    registration,
                    runtime_factory=replace(registration.runtime_factory, factory=None),  # type: ignore[arg-type]
                ),
                "invalid_runtime_factory",
            ),
        )

        for invalid, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(TrustedRecipeRegistrationError) as error_context,
            ):
                compile_trusted_recipe_registrations((invalid,))

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_duplicate_recipes_and_validation_gate_binding_mismatch(self) -> None:
        duplicate = (_registration(), _registration())
        gate_mismatch = replace(
            _registration(),
            validation_adapter=replace(
                _registration().validation_adapter,
                validation_gates=("different-test",),
            ),
        )

        cases = (
            (duplicate, "duplicate_recipe_id"),
            ((gate_mismatch,), "validation_gate_binding_mismatch"),
        )

        for registrations, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(TrustedRecipeRegistrationError) as error_context,
            ):
                compile_trusted_recipe_registrations(registrations)

            self.assertEqual(error_context.exception.code, expected_code)

    def test_validation_adapter_may_report_fixed_internal_gates(self) -> None:
        registration = _registration()
        registration = replace(
            registration,
            validation_adapter=replace(
                registration.validation_adapter,
                validation_gates=("example-test", "internal-safety"),
            ),
        )

        compiled = compile_trusted_recipe_registrations((registration,))

        self.assertEqual(
            compiled.validation_registry.get("example-validation").validation_gates,
            ("example-test", "internal-safety"),
        )

    def test_rejects_empty_registration_set(self) -> None:
        with self.assertRaises(TrustedRecipeRegistrationError) as error_context:
            compile_trusted_recipe_registrations(())

        self.assertEqual(error_context.exception.code, "empty_registration_set")


if __name__ == "__main__":
    unittest.main()
