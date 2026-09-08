from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.project_configuration import ConfigurationValidationError
from scaffold_compiler.project_recipe_registry import (
    CMAKE_RECIPE_ID,
    FASTAPI_RECIPE_ID,
    build_builtin_project_recipe_registry,
    build_project_recipe_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    JSONValue,
    parse_builtin_recipe_project_configuration,
    parse_recipe_project_configuration,
)


def _normalize_c_answers(
    raw_answers: Mapping[str, object],
    project_name: str,
) -> dict[str, JSONValue]:
    target_name = raw_answers.get("target_name", project_name.lower().replace(" ", "_"))
    if not isinstance(target_name, str):
        raise ConfigurationValidationError(
            field="target_name",
            code="invalid_target_name",
            message="Target name must be a string.",
        )
    strict_warnings = raw_answers.get("strict_warnings", True)
    if not isinstance(strict_warnings, bool):
        raise ConfigurationValidationError(
            field="strict_warnings",
            code="invalid_strict_warnings",
            message="Strict warnings must be a boolean.",
        )
    return {"strict_warnings": strict_warnings, "target_name": target_name}


def _c_recipe_record(version: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "c-cmake-cli",
        "version": version,
        "answer_parser": "c-test-answers",
        "assembly_adapter": "c-test-assembly",
        "validation_adapter": "c-test-validation",
        "required_capabilities": ["c-runtime"],
        "capability_rules": [],
        "allowed_blueprints": ["c-runtime"],
        "allowed_validations": ["cmake-build"],
        "prerequisites": ["A C11 compiler", "CMake"],
    }


class RecipeProjectConfigurationTests(unittest.TestCase):
    def test_normalizes_builtin_cmake_answers_and_applies_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": CMAKE_RECIPE_ID,
                    "project_name": "  Example   CLI  ",
                    "target_directory": "example-cli",
                    "answers": {},
                },
                working_directory=Path(directory),
                home_directory=Path(directory) / "home",
            )

            self.assertEqual(configuration.recipe_id, CMAKE_RECIPE_ID)
            self.assertEqual(
                configuration.answers,
                {"strict_warnings": True, "target_name": "example_cli"},
            )

    def test_rejects_nonportable_cmake_target_names_and_non_boolean_warning_flag(self) -> None:
        invalid_cases = (
            ({"target_name": "1invalid"}, "invalid_target_name"),
            ({"target_name": "invalid-name"}, "invalid_target_name"),
            ({"target_name": "UPPER_CASE"}, "invalid_target_name"),
            ({"target_name": "a" * 64}, "invalid_target_name"),
            ({"strict_warnings": 1}, "invalid_strict_warnings"),
            ({"strict_warnings": "true"}, "invalid_strict_warnings"),
            ({"extra": True}, "unknown_answer_fields"),
        )
        for answers, expected_code in invalid_cases:
            with self.subTest(answers=answers), TemporaryDirectory() as directory:
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_builtin_recipe_project_configuration(
                        {
                            "schema_version": 2,
                            "recipe": CMAKE_RECIPE_ID,
                            "project_name": "Example CLI",
                            "target_directory": "example-cli",
                            "answers": answers,
                        },
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.code, expected_code)

    def test_normalizes_v2_fastapi_input_and_applies_recipe_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            configuration = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": FASTAPI_RECIPE_ID,
                    "project_name": "  Billing   API  ",
                    "target_directory": "billing-service",
                    "answers": {},
                },
                working_directory=working_directory,
                home_directory=working_directory / "home",
            )

            self.assertEqual(configuration.schema_version, 2)
            self.assertEqual(configuration.recipe_id, FASTAPI_RECIPE_ID)
            self.assertEqual(configuration.recipe_version, "1.0.0")
            self.assertEqual(configuration.project_name, "Billing API")
            self.assertEqual(
                configuration.answers,
                {
                    "database": "postgres",
                    "delivery": "docker",
                    "package_name": "billing_api",
                },
            )

    def test_maps_legacy_v1_input_to_the_same_fastapi_envelope(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            shared_arguments = {
                "working_directory": working_directory,
                "home_directory": working_directory / "home",
            }
            legacy = parse_builtin_recipe_project_configuration(
                {
                    "project_name": "Billing API",
                    "package_name": "billing_api",
                    "target_directory": "billing-service",
                    "database": "none",
                    "container": "none",
                },
                **shared_arguments,
            )
            v2 = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": FASTAPI_RECIPE_ID,
                    "project_name": "Billing API",
                    "target_directory": "billing-service",
                    "answers": {
                        "package_name": "billing_api",
                        "database": "none",
                        "delivery": "none",
                    },
                },
                **shared_arguments,
            )

            self.assertEqual(legacy, v2)
            self.assertEqual(legacy.configuration_digest, v2.configuration_digest)

    def test_rejects_mixed_legacy_and_v2_fields(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationValidationError) as error_context:
                parse_builtin_recipe_project_configuration(
                    {
                        "schema_version": 2,
                        "recipe": FASTAPI_RECIPE_ID,
                        "project_name": "Billing API",
                        "target_directory": "billing-service",
                        "answers": {},
                        "database": "postgres",
                    },
                    working_directory=Path(directory),
                    home_directory=Path(directory) / "home",
                )

            self.assertEqual(error_context.exception.code, "mixed_configuration_schema")

    def test_rejects_non_integer_v2_schema_versions(self) -> None:
        for schema_version in (2.0, True):
            with self.subTest(schema_version=schema_version), TemporaryDirectory() as directory:
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_builtin_recipe_project_configuration(
                        {
                            "schema_version": schema_version,
                            "recipe": FASTAPI_RECIPE_ID,
                            "project_name": "Billing API",
                            "target_directory": "billing-service",
                            "answers": {},
                        },
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.code, "unsupported_schema_version")

    def test_rejects_unknown_recipe_and_unknown_recipe_answer(self) -> None:
        invalid_cases = (
            ({"recipe": "unknown-recipe", "answers": {}}, "unknown_recipe"),
            (
                {"recipe": FASTAPI_RECIPE_ID, "answers": {"password": "secret"}},
                "unknown_answer_fields",
            ),
        )

        for overrides, expected_code in invalid_cases:
            with self.subTest(expected_code=expected_code), TemporaryDirectory() as directory:
                raw_configuration: dict[str, object] = {
                    "schema_version": 2,
                    "recipe": FASTAPI_RECIPE_ID,
                    "project_name": "Billing API",
                    "target_directory": "billing-service",
                    "answers": {},
                }
                raw_configuration.update(overrides)
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_builtin_recipe_project_configuration(
                        raw_configuration,
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.code, expected_code)
                self.assertNotIn("secret", str(error_context.exception))

    def test_reuses_target_path_safety_for_v2_input(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            home_directory = working_directory / "home"
            home_directory.mkdir()
            existing_target = working_directory / "existing"
            existing_target.mkdir()

            for expected_code, target in {
                "filesystem_root": Path(working_directory.anchor),
                "home": home_directory,
                "existing": existing_target,
                "missing_parent": working_directory / "missing" / "project",
            }.items():
                with (
                    self.subTest(expected_code=expected_code),
                    self.assertRaises(ConfigurationValidationError) as error_context,
                ):
                    parse_builtin_recipe_project_configuration(
                        {
                            "schema_version": 2,
                            "recipe": FASTAPI_RECIPE_ID,
                            "project_name": "Billing API",
                            "target_directory": str(target),
                            "answers": {},
                        },
                        working_directory=working_directory,
                        home_directory=home_directory,
                    )

                self.assertEqual(error_context.exception.code, expected_code)

    def test_digest_is_canonical_and_binds_recipe_version(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            shared_arguments = {
                "working_directory": working_directory,
                "home_directory": working_directory / "home",
            }
            first = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": FASTAPI_RECIPE_ID,
                    "project_name": "Billing API",
                    "target_directory": "billing-service",
                    "answers": {"database": "none", "delivery": "docker"},
                },
                **shared_arguments,
            )
            reordered = parse_builtin_recipe_project_configuration(
                {
                    "answers": {"delivery": "docker", "database": "none"},
                    "target_directory": "billing-service",
                    "project_name": "Billing API",
                    "recipe": FASTAPI_RECIPE_ID,
                    "schema_version": 2,
                },
                **shared_arguments,
            )
            recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)

            self.assertEqual(first.configuration_digest, reordered.configuration_digest)
            self.assertRegex(first.configuration_digest, r"^[0-9a-f]{64}$")
            self.assertIn(recipe.version, first.canonical_payload)

    def test_generic_envelope_supports_a_non_python_recipe_and_binds_its_version(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            raw_configuration: dict[str, object] = {
                "schema_version": 2,
                "recipe": "c-cmake-cli",
                "project_name": "Example CLI",
                "target_directory": "example-cli",
                "answers": {"target_name": "example_cli", "strict_warnings": True},
            }

            configurations = []
            for version in ("1.0.0", "1.0.1"):
                registry = build_project_recipe_registry(
                    [_c_recipe_record(version)],
                    trusted_answer_parser_keys={"c-test-answers"},
                    trusted_assembly_adapter_keys={"c-test-assembly"},
                    trusted_validation_adapter_keys={"c-test-validation"},
                )
                configurations.append(
                    parse_recipe_project_configuration(
                        raw_configuration,
                        registry=registry,
                        answer_normalizers={"c-test-answers": _normalize_c_answers},
                        working_directory=working_directory,
                        home_directory=working_directory / "home",
                    )
                )

            self.assertEqual(configurations[0].recipe_id, "c-cmake-cli")
            self.assertEqual(configurations[0].answers["target_name"], "example_cli")
            self.assertNotEqual(
                configurations[0].configuration_digest,
                configurations[1].configuration_digest,
            )

    def test_configuration_is_immutable_and_answers_are_defensive_copies(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": FASTAPI_RECIPE_ID,
                    "project_name": "Billing API",
                    "target_directory": "billing-service",
                    "answers": {},
                },
                working_directory=Path(directory),
                home_directory=Path(directory) / "home",
            )

            answers = configuration.answers
            answers["database"] = "none"

            self.assertEqual(configuration.answers["database"], "postgres")
            with self.assertRaises(FrozenInstanceError):
                configuration.project_name = "Changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
