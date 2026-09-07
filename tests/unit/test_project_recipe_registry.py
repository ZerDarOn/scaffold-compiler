from __future__ import annotations

import unittest

from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    ProjectRecipeRegistryError,
    build_builtin_project_recipe_registry,
    build_project_recipe_registry,
)


def _recipe_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "id": "test-recipe",
        "version": "1.2.3",
        "answer_parser": "test-answers",
        "assembly_adapter": "test-assembly",
        "validation_adapter": "test-validation",
        "required_capabilities": ["project-baseline"],
        "capability_rules": [
            {"when": {"feature": "enabled"}, "request": ["optional-feature"]},
        ],
        "allowed_blueprints": ["project-baseline", "optional-feature"],
        "allowed_validations": ["syntax", "unit-tests"],
        "prerequisites": ["A C11 compiler"],
    }
    record.update(overrides)
    return record


class ProjectRecipeRegistryTests(unittest.TestCase):
    def test_builds_a_strict_trusted_registry(self) -> None:
        registry = build_project_recipe_registry(
            [_recipe_record()],
            trusted_answer_parser_keys={"test-answers"},
            trusted_assembly_adapter_keys={"test-assembly"},
            trusted_validation_adapter_keys={"test-validation"},
        )

        recipe = registry.get("test-recipe")

        self.assertEqual(recipe.version, "1.2.3")
        self.assertEqual(recipe.required_capabilities, ("project-baseline",))
        self.assertEqual(recipe.capability_rules[0].conditions, (("feature", "enabled"),))
        self.assertEqual(recipe.capability_rules[0].requested_capabilities, ("optional-feature",))

    def test_rejects_duplicate_recipe_ids(self) -> None:
        with self.assertRaises(ProjectRecipeRegistryError) as error_context:
            build_project_recipe_registry(
                [_recipe_record(), _recipe_record(version="1.2.4")],
                trusted_answer_parser_keys={"test-answers"},
                trusted_assembly_adapter_keys={"test-assembly"},
                trusted_validation_adapter_keys={"test-validation"},
            )

        self.assertEqual(error_context.exception.code, "duplicate_recipe_id")

    def test_rejects_unknown_adapter_keys(self) -> None:
        adapter_cases = (
            ("answer_parser", "unknown_answer_parser"),
            ("assembly_adapter", "unknown_assembly_adapter"),
            ("validation_adapter", "unknown_validation_adapter"),
        )

        for field, expected_code in adapter_cases:
            with self.subTest(field=field), self.assertRaises(
                ProjectRecipeRegistryError
            ) as error_context:
                build_project_recipe_registry(
                    [_recipe_record(**{field: "arbitrary.module:callable"})],
                    trusted_answer_parser_keys={"test-answers"},
                    trusted_assembly_adapter_keys={"test-assembly"},
                    trusted_validation_adapter_keys={"test-validation"},
                )

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_executable_or_unknown_recipe_fields(self) -> None:
        for field in ("command", "hook", "download_url", "import_path"):
            with self.subTest(field=field), self.assertRaises(
                ProjectRecipeRegistryError
            ) as error_context:
                build_project_recipe_registry(
                    [_recipe_record(**{field: "do-not-execute"})],
                    trusted_answer_parser_keys={"test-answers"},
                    trusted_assembly_adapter_keys={"test-assembly"},
                    trusted_validation_adapter_keys={"test-validation"},
                )

            self.assertEqual(error_context.exception.code, "unknown_recipe_fields")

    def test_rejects_invalid_identifiers_versions_and_references(self) -> None:
        invalid_cases = (
            ({"schema_version": 1.0}, "unsupported_recipe_schema"),
            ({"schema_version": True}, "unsupported_recipe_schema"),
            ({"id": "../../recipe"}, "invalid_recipe_id"),
            ({"version": "latest"}, "invalid_recipe_version"),
            ({"required_capabilities": ["missing"]}, "unknown_capability"),
            ({"allowed_blueprints": ["duplicate", "duplicate"]}, "duplicate_blueprint_id"),
            ({"allowed_validations": ["syntax", "syntax"]}, "duplicate_validation_gate"),
        )

        for overrides, expected_code in invalid_cases:
            with self.subTest(expected_code=expected_code), self.assertRaises(
                ProjectRecipeRegistryError
            ) as error_context:
                build_project_recipe_registry(
                    [_recipe_record(**overrides)],
                    trusted_answer_parser_keys={"test-answers"},
                    trusted_assembly_adapter_keys={"test-assembly"},
                    trusted_validation_adapter_keys={"test-validation"},
                )

            self.assertEqual(error_context.exception.code, expected_code)

    def test_builtin_fastapi_recipe_declares_defaults_and_capability_mapping(self) -> None:
        recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)

        self.assertEqual(recipe.version, "1.0.0")
        self.assertEqual(recipe.answer_parser_key, "fastapi-answers")
        self.assertIn("final-project-assembly", recipe.required_capabilities)
        self.assertIn("postgres-persistence", recipe.allowed_blueprint_ids)
        self.assertIn("docker-build", recipe.allowed_validation_gates)
        self.assertEqual(
            {rule.conditions for rule in recipe.capability_rules},
            {
                (("database", "postgres"),),
                (("delivery", "docker"),),
                (("database", "postgres"), ("delivery", "docker")),
            },
        )


if __name__ == "__main__":
    unittest.main()
