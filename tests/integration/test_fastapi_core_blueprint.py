from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.project_configuration import parse_project_configuration
from scaffold_compiler.v1_project_compiler import compile_v1_candidate
from tests.golden_projects.project_specifications import GOLDEN_PROJECT_SPECIFICATIONS


class FastApiCoreBlueprintTests(unittest.TestCase):
    def compile_matrix_candidate(
        self,
        root: Path,
        *,
        database: str,
        container: str,
    ) -> CandidateAssemblyResult:
        workspace = root / "workspace"
        workspace.mkdir()
        configuration = parse_project_configuration(
            {
                "project_name": "Example API",
                "package_name": "example",
                "target_directory": str(root / "delivery"),
                "database": database,
                "container": container,
            },
            working_directory=root,
            home_directory=root / "home",
        )
        return compile_v1_candidate(configuration, workspace)

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
                {path.format(package_name="example") for path in expected.required_paths},
            )
            self.assertTrue(
                actual_paths.isdisjoint(
                    path.format(package_name="example") for path in expected.forbidden_paths
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

    def test_m02_adds_a_frozen_non_root_docker_delivery(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.compile_matrix_candidate(
                root,
                database="none",
                container="docker",
            )

            expected = next(
                specification
                for specification in GOLDEN_PROJECT_SPECIFICATIONS
                if specification.matrix_id == "M-02"
            )
            actual_paths = {file.path for file in result.files}
            self.assertEqual(
                actual_paths,
                {path.format(package_name="example") for path in expected.required_paths},
            )
            self.assertTrue(actual_paths.isdisjoint(expected.forbidden_paths))

            dockerfile = (result.root / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("uv sync --frozen --no-dev", dockerfile)
            self.assertIn("USER application", dockerfile)
            self.assertIn("HEALTHCHECK", dockerfile)
            self.assertIn("example.asgi:application", dockerfile)
            self.assertNotIn("compose.yaml", actual_paths)

            dockerignore = (result.root / ".dockerignore").read_text(encoding="utf-8")
            self.assertIn(".env", dockerignore.splitlines())
            self.assertIn("*.pem", dockerignore.splitlines())

    def test_m04_adds_compose_with_database_and_migration_health_gates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.compile_matrix_candidate(
                root,
                database="postgres",
                container="docker",
            )

            expected = next(
                specification
                for specification in GOLDEN_PROJECT_SPECIFICATIONS
                if specification.matrix_id == "M-04"
            )
            actual_paths = {file.path for file in result.files}
            self.assertEqual(
                actual_paths,
                {path.format(package_name="example") for path in expected.required_paths},
            )

            compose = (result.root / "compose.yaml").read_text(encoding="utf-8")
            self.assertIn("condition: service_healthy", compose)
            self.assertIn("condition: service_completed_successfully", compose)
            self.assertIn('command: ["alembic", "upgrade", "head"]', compose)
            self.assertIn("POSTGRES_PASSWORD", compose)
            self.assertIn("${APPLICATION_PORT:-8000}:8000", compose)
            self.assertIn(
                "APPLICATION_PORT=8000",
                (result.root / ".env.example").read_text(encoding="utf-8"),
            )
            self.assertNotIn("change-me", compose)
            self.assertNotIn("postgres_docker_integration", compose)


if __name__ == "__main__":
    unittest.main()
