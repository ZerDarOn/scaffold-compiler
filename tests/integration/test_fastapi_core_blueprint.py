from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.project_configuration import parse_project_configuration
from scaffold_compiler.v1_project_compiler import compile_v1_candidate
from tests.golden_projects.project_specifications import GOLDEN_PROJECT_SPECIFICATIONS


class FastApiCoreBlueprintTests(unittest.TestCase):
    def test_m01_materializes_the_complete_framework_agnostic_core(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            configuration = parse_project_configuration(
                {
                    "project_name": "Example API",
                    "package_name": "example",
                    "target_directory": str(root / "delivery"),
                    "database": "none",
                    "container": "none",
                },
                working_directory=root,
                home_directory=root / "home",
            )

            result = compile_v1_candidate(configuration, workspace)

            expected = next(
                specification
                for specification in GOLDEN_PROJECT_SPECIFICATIONS
                if specification.matrix_id == "M-01"
            )
            actual_paths = {file.path for file in result.files}
            expected_paths = {
                path.format(package_name="example") for path in expected.required_paths
            }
            forbidden_paths = {
                path.format(package_name="example") for path in expected.forbidden_paths
            }
            self.assertEqual(actual_paths, expected_paths)
            self.assertTrue(actual_paths.isdisjoint(forbidden_paths))
            self.assertEqual(
                (result.root / ".python-version").read_text(encoding="utf-8"),
                "3.14\n",
            )
            self.assertIn(
                "example.asgi:application",
                (result.root / "README.md").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "${",
                "".join(
                    path.read_text(encoding="utf-8")
                    for path in result.root.rglob("*")
                    if path.is_file()
                ),
            )

    def test_m03_adds_the_complete_postgres_vertical_slice(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            configuration = parse_project_configuration(
                {
                    "project_name": "Example API",
                    "package_name": "example",
                    "target_directory": str(root / "delivery"),
                    "database": "postgres",
                    "container": "none",
                },
                working_directory=root,
                home_directory=root / "home",
            )

            result = compile_v1_candidate(configuration, workspace)

            expected = next(
                specification
                for specification in GOLDEN_PROJECT_SPECIFICATIONS
                if specification.matrix_id == "M-03"
            )
            actual_paths = {file.path for file in result.files}
            self.assertEqual(
                actual_paths,
                {
                    path.format(package_name="example") for path in expected.required_paths
                },
            )
            self.assertTrue(
                actual_paths.isdisjoint(
                    path.format(package_name="example")
                    for path in expected.forbidden_paths
                )
            )
            settings = (
                result.root / "src/example/configuration/application_settings.py"
            ).read_text(encoding="utf-8")
            self.assertIn('validation_alias="DATABASE_URL"', settings)
            self.assertIn("postgresql\\+asyncpg", settings)
            self.assertIn(
                "await database_engine.dispose()",
                (result.root / "src/example/asgi.py").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
