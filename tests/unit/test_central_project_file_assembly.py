from __future__ import annotations

import tomllib
import unittest
from collections.abc import Mapping

from scaffold_compiler.central_project_file_assembly import (
    BlueprintContribution,
    CentralAssemblyError,
    EnvironmentVariableContribution,
    PackageRequirement,
    ProjectIdentity,
    ReadmeSectionContribution,
    assemble_central_project_files,
)
from scaffold_compiler.version_policy import (
    GENERATED_PROJECT_DEVELOPMENT_DEPENDENCIES,
    GENERATED_PROJECT_POSTGRES_DEPENDENCIES,
    GENERATED_PROJECT_RUNTIME_DEPENDENCIES,
)

QUALITY_COMMANDS = (
    "uv run ruff format --check .",
    "uv run ruff check .",
    "uv run mypy",
    "uv run pytest",
)


def requirements(versions: Mapping[str, str]) -> tuple[PackageRequirement, ...]:
    return tuple(
        PackageRequirement(name=name, version=version) for name, version in versions.items()
    )


def contributions_for(*, database: str, container: str) -> tuple[BlueprintContribution, ...]:
    contributions = [
        BlueprintContribution(
            owner="fastapi-http-api",
            runtime_dependencies=requirements(GENERATED_PROJECT_RUNTIME_DEPENDENCIES),
            development_dependencies=requirements(GENERATED_PROJECT_DEVELOPMENT_DEPENDENCIES),
            environment_variables=(
                EnvironmentVariableContribution(
                    name="APP_ENVIRONMENT",
                    description="Application environment name.",
                    example="local",
                ),
            ),
            readme_sections=(
                ReadmeSectionContribution(
                    key="run",
                    title="Run locally",
                    body="Start with `uv run uvicorn example.asgi:application --reload`.",
                    order=20,
                ),
            ),
            quality_commands=QUALITY_COMMANDS,
        )
    ]
    if database == "postgres":
        contributions.append(
            BlueprintContribution(
                owner="postgres-persistence",
                runtime_dependencies=requirements(GENERATED_PROJECT_POSTGRES_DEPENDENCIES),
                environment_variables=(
                    EnvironmentVariableContribution(
                        name="DATABASE_URL",
                        description="Async PostgreSQL connection URL.",
                        example="",
                        secret=True,
                    ),
                ),
                readme_sections=(
                    ReadmeSectionContribution(
                        key="database",
                        title="PostgreSQL",
                        body="Set `DATABASE_URL`, then run `uv run alembic upgrade head`.",
                        order=30,
                    ),
                ),
            )
        )
    if container == "docker":
        contributions.append(
            BlueprintContribution(
                owner="docker-delivery",
                readme_sections=(
                    ReadmeSectionContribution(
                        key="docker",
                        title="Docker",
                        body="Build with `docker build -t example .`.",
                        order=40,
                    ),
                ),
            )
        )
    return tuple(contributions)


class CentralProjectFileAssemblyTests(unittest.TestCase):
    def test_four_contribution_sets_produce_only_selected_dependencies_and_environment(
        self,
    ) -> None:
        for database, container in (
            ("none", "none"),
            ("none", "docker"),
            ("postgres", "none"),
            ("postgres", "docker"),
        ):
            with self.subTest(database=database, container=container):
                files = assemble_central_project_files(
                    ProjectIdentity(
                        project_name="Example API",
                        package_name="example",
                        description="An example FastAPI service.",
                    ),
                    contributions_for(database=database, container=container),
                )
                by_path = {file.path: file.content for file in files}
                project = tomllib.loads(by_path["pyproject.toml"])["project"]
                dependency_names = {
                    dependency.split("==", 1)[0] for dependency in project["dependencies"]
                }

                self.assertIn("APP_ENVIRONMENT=local", by_path[".env.example"])
                if database == "postgres":
                    self.assertIn("sqlalchemy[asyncio]", dependency_names)
                    self.assertIn("DATABASE_URL=", by_path[".env.example"])
                    self.assertNotIn("postgresql+asyncpg://", by_path[".env.example"])
                else:
                    self.assertNotIn("sqlalchemy[asyncio]", dependency_names)
                    self.assertNotIn("DATABASE_URL", by_path[".env.example"])

                if container == "docker":
                    self.assertIn("## Docker", by_path["README.md"])
                else:
                    self.assertNotIn("## Docker", by_path["README.md"])

    def test_pyproject_is_valid_and_uses_exact_frozen_versions(self) -> None:
        files = assemble_central_project_files(
            ProjectIdentity("Example API", "example", "Example."),
            contributions_for(database="postgres", container="none"),
        )
        pyproject = tomllib.loads(
            next(file.content for file in files if file.path == "pyproject.toml")
        )

        self.assertEqual(pyproject["project"]["requires-python"], ">=3.14,<3.15")
        self.assertEqual(
            set(pyproject["project"]["dependencies"]),
            {
                f"{name}=={version}"
                for name, version in (
                    GENERATED_PROJECT_RUNTIME_DEPENDENCIES | GENERATED_PROJECT_POSTGRES_DEPENDENCIES
                ).items()
            },
        )

    def test_readme_and_ci_use_the_same_explicit_quality_commands(self) -> None:
        files = assemble_central_project_files(
            ProjectIdentity("Example API", "example", "Example."),
            contributions_for(database="none", container="none"),
        )
        by_path = {file.path: file.content for file in files}

        for command in QUALITY_COMMANDS:
            self.assertIn(command, by_path["README.md"])
            self.assertIn(command, by_path[".github/workflows/quality.yml"])

    def test_assembly_is_byte_stable_when_contribution_order_changes(self) -> None:
        identity = ProjectIdentity("Example API", "example", "Example.")
        contributions = contributions_for(database="postgres", container="docker")

        first = assemble_central_project_files(identity, contributions)
        second = assemble_central_project_files(identity, tuple(reversed(contributions)))

        self.assertEqual(first, second)

    def test_rejects_duplicate_structured_contribution_keys(self) -> None:
        duplicate_dependency = PackageRequirement("fastapi", "0.141.1")
        contributions = (
            BlueprintContribution(
                owner="first",
                runtime_dependencies=(duplicate_dependency,),
            ),
            BlueprintContribution(
                owner="second",
                runtime_dependencies=(duplicate_dependency,),
            ),
        )

        with self.assertRaisesRegex(CentralAssemblyError, "fastapi"):
            assemble_central_project_files(
                ProjectIdentity("Example API", "example", "Example."),
                contributions,
            )


if __name__ == "__main__":
    unittest.main()
