from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.builtin_trusted_recipe_registrations import (
    build_builtin_trusted_recipe_registrations,
)
from scaffold_compiler.cmake_project_validation_adapter import CMakeValidationRuntime
from scaffold_compiler.fastapi_project_validation_adapter import FastApiValidationRuntime
from scaffold_compiler.go_project_validation_adapter import GoValidationRuntime
from scaffold_compiler.recipe_project_configuration import (
    parse_recipe_project_configuration,
)
from scaffold_compiler.trusted_recipe_registration import (
    CompiledTrustedRecipeRegistrations,
    compile_trusted_recipe_registrations,
)


def _compile_builtins(root: Path) -> CompiledTrustedRecipeRegistrations:
    catalog_root = root / "blueprints"
    catalog_root.mkdir()
    return compile_trusted_recipe_registrations(
        build_builtin_trusted_recipe_registrations(
            catalog_root=catalog_root,
            uv_executable=root / "uv",
            docker_executable=root / "docker",
            cmake_executable=root / "cmake",
            ctest_executable=root / "ctest",
            go_executable=root / "go",
            gofmt_executable=root / "gofmt",
            environment={"DATABASE_URL": "private-value"},
        )
    )


class BuiltinTrustedRecipeRegistrationsTests(unittest.TestCase):
    def test_one_complete_registration_compiles_for_each_builtin_recipe(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            compiled = _compile_builtins(root)

            expected_recipe_ids = (
                "python-fastapi-service",
                "c-cmake-cli",
                "go-cli",
            )
            self.assertEqual(
                tuple(recipe.recipe_id for recipe in compiled.recipe_registry.recipes),
                expected_recipe_ids,
            )
            self.assertEqual(
                tuple(
                    item.recipe_id
                    for item in compiled.questionnaire_registry.for_recipes(
                        compiled.recipe_registry
                    )
                ),
                expected_recipe_ids,
            )
            self.assertEqual(
                tuple(item.recipe_id for item in compiled.runtime_factory_registry.registrations),
                expected_recipe_ids,
            )
            self.assertEqual(
                set(compiled.answer_normalizers),
                {"fastapi-answers", "cmake-answers", "go-answers"},
            )

    def test_each_recipe_dispatches_its_own_runtime_factory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            compiled = _compile_builtins(root)
            raw_configurations = (
                {
                    "answers": {
                        "database": "postgres",
                        "delivery": "docker",
                        "package_name": "orders_api",
                    },
                    "project_name": "Orders API",
                    "recipe": "python-fastapi-service",
                    "schema_version": 2,
                    "target_directory": "fastapi-delivery",
                },
                {
                    "answers": {"strict_warnings": True, "target_name": "tiny_tool"},
                    "project_name": "Tiny Tool",
                    "recipe": "c-cmake-cli",
                    "schema_version": 2,
                    "target_directory": "cmake-delivery",
                },
                {
                    "answers": {
                        "binary_name": "example-tool",
                        "module_path": "example.com/example-tool",
                    },
                    "project_name": "Example Tool",
                    "recipe": "go-cli",
                    "schema_version": 2,
                    "target_directory": "go-delivery",
                },
            )
            runtimes: list[object] = []
            for raw in raw_configurations:
                configuration = parse_recipe_project_configuration(
                    raw,
                    registry=compiled.recipe_registry,
                    answer_normalizers=compiled.answer_normalizers,
                    working_directory=root,
                    home_directory=root,
                )
                recipe = compiled.recipe_registry.get(configuration.recipe_id)
                runtimes.append(
                    compiled.runtime_factory_registry.create(
                        configuration,
                        recipe,
                        f"{recipe.recipe_id}-run",
                    )
                )

            fastapi, cmake, go = runtimes
            self.assertIsInstance(fastapi, FastApiValidationRuntime)
            self.assertIsInstance(cmake, CMakeValidationRuntime)
            self.assertIsInstance(go, GoValidationRuntime)
            assert isinstance(fastapi, FastApiValidationRuntime)
            assert isinstance(cmake, CMakeValidationRuntime)
            assert isinstance(go, GoValidationRuntime)
            self.assertEqual(fastapi.database_url, "private-value")
            self.assertEqual(fastapi.uv_executable, root / "uv")
            self.assertEqual(cmake.target_name, "tiny_tool")
            self.assertEqual(cmake.cmake_executable, root / "cmake")
            self.assertEqual(go.binary_name, "example-tool")
            self.assertEqual(go.go_executable, root / "go")


if __name__ == "__main__":
    unittest.main()
