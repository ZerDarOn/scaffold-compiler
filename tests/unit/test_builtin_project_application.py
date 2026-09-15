from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.builtin_project_application import (
    build_builtin_project_command_application,
)
from scaffold_compiler.fastapi_project_validation_adapter import FastApiValidationRuntime
from scaffold_compiler.generation_workspace_identity import derive_generation_workspace
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    build_builtin_answer_normalizers,
    parse_recipe_project_configuration,
)


class BuiltinProjectApplicationTests(unittest.TestCase):
    def test_recipe_catalog_exposes_all_builtins_without_runtime_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            catalog_root = root / "catalog"
            catalog_root.mkdir()
            application = build_builtin_project_command_application(
                catalog_root=catalog_root,
                working_directory=root,
                home_directory=root,
                uv_executable=root / "private-uv",
                docker_executable=root / "private-docker",
                cmake_executable=root / "private-cmake",
                ctest_executable=root / "private-ctest",
                go_executable=root / "private-go",
                gofmt_executable=root / "private-gofmt",
                environment={"DATABASE_URL": "private-database-url"},
            )

            outcome = application.list_recipes()

            self.assertEqual(outcome.exit_code, 0)
            catalog = json.loads(outcome.message)
            self.assertEqual(
                [recipe["id"] for recipe in catalog["recipes"]],
                ["python-fastapi-service", "c-cmake-cli", "go-cli"],
            )
            self.assertNotIn(root.as_posix(), outcome.message)
            self.assertNotIn("private", outcome.message)

    def test_fastapi_runtime_forbids_the_bound_short_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            catalog_root = root / "catalog"
            catalog_root.mkdir()
            registry = build_builtin_project_recipe_registry()
            configuration = parse_recipe_project_configuration(
                {
                    "answers": {
                        "database": "none",
                        "delivery": "none",
                        "package_name": "delivery",
                    },
                    "project_name": "Delivery",
                    "recipe": FASTAPI_RECIPE_ID,
                    "schema_version": 2,
                    "target_directory": "delivery",
                },
                registry=registry,
                answer_normalizers=build_builtin_answer_normalizers(),
                working_directory=root,
                home_directory=root,
            )
            recipe = registry.get(configuration.recipe_id)
            application = build_builtin_project_command_application(
                catalog_root=catalog_root,
                working_directory=root,
                home_directory=root,
                uv_executable=root / "uv",
                docker_executable=None,
                cmake_executable=None,
                ctest_executable=None,
                environment={},
            )

            runtime = application._runtime_factory_registry.create(
                configuration,
                recipe,
                "run-1",
            )

            self.assertIsInstance(runtime, FastApiValidationRuntime)
            assert isinstance(runtime, FastApiValidationRuntime)
            expected_workspace = derive_generation_workspace(
                configuration.target_directory,
                run_id="run-1",
                configuration_digest=configuration.configuration_digest,
            )
            self.assertEqual(
                runtime.forbidden_absolute_paths,
                (catalog_root.parent, expected_workspace),
            )


if __name__ == "__main__":
    unittest.main()
