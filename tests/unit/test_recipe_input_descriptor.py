from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from scaffold_compiler.recipe_input_descriptor import (
    RecipeInputDescriptor,
    RecipeInputDescriptorError,
    RecipeInputOmission,
    RecipeInputType,
)


class RecipeInputDescriptorTests(unittest.TestCase):
    def test_serializes_strict_immutable_public_metadata(self) -> None:
        descriptors = (
            RecipeInputDescriptor(
                key="package_name",
                label="Python package name",
                input_type=RecipeInputType.STRING,
                omission=RecipeInputOmission.OMIT,
            ),
            RecipeInputDescriptor(
                key="strict_warnings",
                label="Strict compiler warnings",
                input_type=RecipeInputType.BOOLEAN,
                omission=RecipeInputOmission.LITERAL,
                literal_default=True,
            ),
            RecipeInputDescriptor(
                key="database",
                label="Database",
                input_type=RecipeInputType.CHOICE,
                omission=RecipeInputOmission.LITERAL,
                choices=("none", "postgres"),
                literal_default="none",
            ),
        )

        self.assertEqual(
            [descriptor.to_public_data() for descriptor in descriptors],
            [
                {
                    "choices": [],
                    "interactive_default": None,
                    "key": "package_name",
                    "label": "Python package name",
                    "omission": "omit",
                    "required": False,
                    "type": "string",
                },
                {
                    "choices": [],
                    "interactive_default": True,
                    "key": "strict_warnings",
                    "label": "Strict compiler warnings",
                    "omission": "literal",
                    "required": False,
                    "type": "boolean",
                },
                {
                    "choices": ["none", "postgres"],
                    "interactive_default": "none",
                    "key": "database",
                    "label": "Database",
                    "omission": "literal",
                    "required": False,
                    "type": "choice",
                },
            ],
        )
        with self.assertRaises(FrozenInstanceError):
            descriptors[0].label = "changed"  # type: ignore[misc]

    def test_rejects_invalid_identity_label_and_enum_values(self) -> None:
        cases = (
            (
                {"key": "Bad-Key", "label": "Name"},
                "invalid_input_key",
            ),
            (
                {"key": "name", "label": "line\nbreak"},
                "invalid_input_label",
            ),
            (
                {"key": "name", "label": "Name", "input_type": "string"},
                "invalid_input_type",
            ),
            (
                {"key": "name", "label": "Name", "omission": "omit"},
                "invalid_input_omission",
            ),
        )
        for overrides, expected_code in cases:
            arguments: dict[str, object] = {
                "key": "name",
                "label": "Name",
                "input_type": RecipeInputType.STRING,
                "omission": RecipeInputOmission.OMIT,
            }
            arguments.update(overrides)
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(RecipeInputDescriptorError) as error_context,
            ):
                RecipeInputDescriptor(**arguments)  # type: ignore[arg-type]

            self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_invalid_choice_and_default_combinations(self) -> None:
        cases = (
            (
                {
                    "input_type": RecipeInputType.STRING,
                    "choices": ("a",),
                },
                "unexpected_input_choices",
            ),
            (
                {
                    "input_type": RecipeInputType.CHOICE,
                    "choices": (),
                },
                "invalid_input_choices",
            ),
            (
                {
                    "input_type": RecipeInputType.CHOICE,
                    "choices": ("a", "a"),
                },
                "duplicate_input_choices",
            ),
            (
                {
                    "input_type": RecipeInputType.BOOLEAN,
                    "omission": RecipeInputOmission.LITERAL,
                    "literal_default": "true",
                },
                "input_default_type_mismatch",
            ),
            (
                {
                    "input_type": RecipeInputType.CHOICE,
                    "omission": RecipeInputOmission.LITERAL,
                    "choices": ("a", "b"),
                    "literal_default": "c",
                },
                "input_default_not_allowed",
            ),
            (
                {
                    "input_type": RecipeInputType.STRING,
                    "omission": RecipeInputOmission.LITERAL,
                },
                "missing_input_default",
            ),
            (
                {
                    "input_type": RecipeInputType.STRING,
                    "omission": RecipeInputOmission.OMIT,
                    "literal_default": "value",
                },
                "unexpected_input_default",
            ),
        )
        for overrides, expected_code in cases:
            arguments: dict[str, object] = {
                "key": "name",
                "label": "Name",
                "input_type": RecipeInputType.STRING,
                "omission": RecipeInputOmission.OMIT,
            }
            arguments.update(overrides)
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(RecipeInputDescriptorError) as error_context,
            ):
                RecipeInputDescriptor(**arguments)  # type: ignore[arg-type]

            self.assertEqual(error_context.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
