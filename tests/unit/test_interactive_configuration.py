from __future__ import annotations

import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO

from scaffold_compiler.interactive_configuration import (
    ConfigurationInitializationError,
    build_builtin_recipe_questionnaire_registry,
    write_interactive_configuration,
)
from scaffold_compiler.project_recipe_registry import build_builtin_project_recipe_registry
from scaffold_compiler.recipe_project_configuration import (
    JSONValue,
    build_builtin_answer_normalizers,
)
from scaffold_compiler.trusted_recipe_registration import build_recipe_questionnaire_registry


class InteractiveConfigurationTests(unittest.TestCase):
    def initialize(self, root: Path, responses: str, *, name: str = "project.json") -> Path:
        output_path = root / name
        write_interactive_configuration(
            output_path,
            input_stream=io.StringIO(responses),
            output_stream=io.StringIO(),
            recipe_registry=build_builtin_project_recipe_registry(),
            questionnaire_registry=build_builtin_recipe_questionnaire_registry(),
            answer_normalizers=build_builtin_answer_normalizers(),
            working_directory=root,
            home_directory=root / "home",
        )
        return output_path

    def test_fastapi_questionnaire_writes_a_canonical_validated_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            path = self.initialize(root, "1\nOrders API\norders\norders_api\npostgres\ndocker\n")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["recipe"], "python-fastapi-service")
            self.assertEqual(payload["project_name"], "Orders API")
            self.assertEqual(
                payload["target_directory"],
                (root / "orders").resolve(strict=False).as_posix(),
            )
            self.assertEqual(
                payload["answers"],
                {"database": "postgres", "delivery": "docker", "package_name": "orders_api"},
            )

    def test_cmake_questionnaire_supports_defaults_and_retries_invalid_choices(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = io.StringIO()

            path = root / "project.json"
            write_interactive_configuration(
                path,
                input_stream=io.StringIO("invalid\n2\nTiny Tool\ntiny-tool\n\n\n"),
                output_stream=transcript,
                recipe_registry=build_builtin_project_recipe_registry(),
                questionnaire_registry=build_builtin_recipe_questionnaire_registry(),
                answer_normalizers=build_builtin_answer_normalizers(),
                working_directory=root,
                home_directory=root / "home",
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("Please choose", transcript.getvalue())
            self.assertEqual(payload["recipe"], "c-cmake-cli")
            self.assertEqual(
                payload["answers"],
                {"strict_warnings": True, "target_name": "tiny_tool"},
            )

    def test_existing_output_is_never_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "project.json"
            path.write_text("keep me", encoding="utf-8")

            with self.assertRaises(ConfigurationInitializationError):
                self.initialize(root, "1\nDemo\ndelivery\n\n\n\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "keep me")

    def test_go_questionnaire_writes_defaults_through_the_strict_parser(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            path = self.initialize(root, "3\nExample Tool\ndelivery\n\n\n")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["recipe"], "go-cli")
            self.assertEqual(
                payload["answers"],
                {"binary_name": "example-tool", "module_path": "example.com/example-tool"},
            )

    def test_injected_questionnaire_collector_is_the_only_collector_invoked(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = build_builtin_recipe_questionnaire_registry()
            go_questionnaire = registry.registrations[2]
            selected = False

            def collect_go_answers(
                _source: TextIO,
                _sink: TextIO,
            ) -> dict[str, JSONValue]:
                nonlocal selected
                selected = True
                return {
                    "binary_name": "registered-tool",
                    "module_path": "example.com/registered-tool",
                }

            questionnaire_registry = build_recipe_questionnaire_registry(
                (
                    *registry.registrations[:2],
                    replace(go_questionnaire, collector=collect_go_answers),
                )
            )
            path = root / "project.json"

            write_interactive_configuration(
                path,
                input_stream=io.StringIO("3\nRegistered Tool\ndelivery\n"),
                output_stream=io.StringIO(),
                recipe_registry=build_builtin_project_recipe_registry(),
                questionnaire_registry=questionnaire_registry,
                answer_normalizers=build_builtin_answer_normalizers(),
                working_directory=root,
                home_directory=root / "home",
            )

            self.assertTrue(selected)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["answers"],
                {
                    "binary_name": "registered-tool",
                    "module_path": "example.com/registered-tool",
                },
            )

    def test_end_of_input_creates_no_configuration_or_temporary_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaises(ConfigurationInitializationError):
                self.initialize(root, "1\n")

            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
