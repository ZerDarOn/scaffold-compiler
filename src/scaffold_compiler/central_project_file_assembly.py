"""Deterministic assembly for project files shared by multiple blueprints."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from scaffold_compiler.version_policy import (
    GENERATED_PROJECT_PYTHON,
    SETUPTOOLS_VERSION,
    UV_VERSION,
)

_PACKAGE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTRIBUTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_REQUIREMENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:\[[a-z0-9,-]+\])?$")
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[a-z0-9.-]*)?$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class CentralAssemblyError(ValueError):
    """Raised when structured contributions cannot be safely combined."""


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    project_name: str
    package_name: str
    description: str

    def __post_init__(self) -> None:
        if not self.project_name.strip() or not self.description.strip():
            raise CentralAssemblyError("Project name and description cannot be empty.")
        if not _PACKAGE_NAME_PATTERN.fullmatch(self.package_name):
            raise CentralAssemblyError("Project package name is invalid.")
        if any(character in self.project_name + self.description for character in "\r\n\0"):
            raise CentralAssemblyError("Project metadata cannot contain control characters.")


@dataclass(frozen=True, slots=True)
class PackageRequirement:
    name: str
    version: str

    def __post_init__(self) -> None:
        if not _REQUIREMENT_NAME_PATTERN.fullmatch(self.name):
            raise CentralAssemblyError("Dependency name is invalid.")
        if not _VERSION_PATTERN.fullmatch(self.version):
            raise CentralAssemblyError("Dependency version must be exact and numeric.")

    @property
    def pinned(self) -> str:
        return f"{self.name}=={self.version}"


@dataclass(frozen=True, slots=True)
class EnvironmentVariableContribution:
    name: str
    description: str
    example: str
    secret: bool = False

    def __post_init__(self) -> None:
        if not _ENVIRONMENT_NAME_PATTERN.fullmatch(self.name):
            raise CentralAssemblyError("Environment variable name is invalid.")
        if not self.description or any(character in self.description for character in "\r\n\0"):
            raise CentralAssemblyError("Environment variable description is invalid.")
        if any(character in self.example for character in "\r\n\0"):
            raise CentralAssemblyError("Environment example must fit on one line.")
        if self.secret and self.example:
            raise CentralAssemblyError("Secret environment variables cannot contain examples.")


@dataclass(frozen=True, slots=True)
class ReadmeSectionContribution:
    key: str
    title: str
    body: str
    order: int

    def __post_init__(self) -> None:
        if not _CONTRIBUTION_KEY_PATTERN.fullmatch(self.key):
            raise CentralAssemblyError("README section key is invalid.")
        if not self.title.strip() or not self.body.strip() or self.order < 0:
            raise CentralAssemblyError("README section content is invalid.")


@dataclass(frozen=True, slots=True)
class BlueprintContribution:
    owner: str
    runtime_dependencies: tuple[PackageRequirement, ...] = ()
    development_dependencies: tuple[PackageRequirement, ...] = ()
    environment_variables: tuple[EnvironmentVariableContribution, ...] = ()
    readme_sections: tuple[ReadmeSectionContribution, ...] = ()
    quality_commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CONTRIBUTION_KEY_PATTERN.fullmatch(self.owner):
            raise CentralAssemblyError("Contribution owner is invalid.")
        if any(
            not command.strip() or any(character in command for character in "\r\n\0")
            for command in self.quality_commands
        ):
            raise CentralAssemblyError("Quality commands must be non-empty single lines.")


@dataclass(frozen=True, slots=True)
class CentralProjectFile:
    path: str
    content: str


def parse_blueprint_contribution(
    owner: str,
    serialized_contribution: str,
) -> BlueprintContribution:
    """Decode one strict, non-executable contribution object from a manifest."""
    try:
        raw = json.loads(serialized_contribution)
    except json.JSONDecodeError as error:
        raise CentralAssemblyError("Blueprint contribution JSON is invalid.") from error
    if not isinstance(raw, dict):
        raise CentralAssemblyError("Blueprint contributions must be an object.")
    allowed_fields = {
        "runtime_dependencies",
        "development_dependencies",
        "environment_variables",
        "readme_sections",
        "quality_commands",
    }
    if not set(raw) <= allowed_fields:
        raise CentralAssemblyError("Blueprint contribution contains unknown fields.")
    return BlueprintContribution(
        owner=owner,
        runtime_dependencies=_parse_requirements(raw.get("runtime_dependencies", {})),
        development_dependencies=_parse_requirements(raw.get("development_dependencies", {})),
        environment_variables=_parse_environment_variables(raw.get("environment_variables", [])),
        readme_sections=_parse_readme_sections(raw.get("readme_sections", [])),
        quality_commands=_parse_string_array(raw.get("quality_commands", []), "commands"),
    )


def _parse_requirements(value: object) -> tuple[PackageRequirement, ...]:
    if not isinstance(value, dict):
        raise CentralAssemblyError("Dependency contributions must be an object.")
    requirements: list[PackageRequirement] = []
    for name, version in value.items():
        if not isinstance(name, str) or not isinstance(version, str):
            raise CentralAssemblyError("Dependency contributions must map strings to strings.")
        requirements.append(PackageRequirement(name=name, version=version))
    return tuple(sorted(requirements, key=lambda requirement: requirement.name))


def _parse_environment_variables(
    value: object,
) -> tuple[EnvironmentVariableContribution, ...]:
    records = _object_array(value, "environment variables")
    result: list[EnvironmentVariableContribution] = []
    for record in records:
        if set(record) != {"name", "description", "example", "secret"}:
            raise CentralAssemblyError("Environment contribution fields are invalid.")
        name = record["name"]
        description = record["description"]
        example = record["example"]
        secret = record["secret"]
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(example, str)
            or not isinstance(secret, bool)
        ):
            raise CentralAssemblyError("Environment contribution types are invalid.")
        result.append(EnvironmentVariableContribution(name, description, example, secret))
    return tuple(result)


def _parse_readme_sections(value: object) -> tuple[ReadmeSectionContribution, ...]:
    records = _object_array(value, "README sections")
    result: list[ReadmeSectionContribution] = []
    for record in records:
        if set(record) != {"key", "title", "body", "order"}:
            raise CentralAssemblyError("README contribution fields are invalid.")
        key = record["key"]
        title = record["title"]
        body = record["body"]
        order = record["order"]
        if (
            not isinstance(key, str)
            or not isinstance(title, str)
            or not isinstance(body, str)
            or not isinstance(order, int)
            or isinstance(order, bool)
        ):
            raise CentralAssemblyError("README contribution types are invalid.")
        result.append(ReadmeSectionContribution(key, title, body, order))
    return tuple(result)


def _object_array(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CentralAssemblyError(f"Blueprint {label} contribution must be an object array.")
    return tuple(value)


def _parse_string_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CentralAssemblyError(f"Blueprint {label} contribution must be a string array.")
    return tuple(value)


def assemble_central_project_files(
    identity: ProjectIdentity,
    contributions: tuple[BlueprintContribution, ...],
) -> tuple[CentralProjectFile, ...]:
    """Merge structured contributions and render each shared file once."""
    normalized = _normalize_contributions(contributions)
    runtime, development, environment, sections, commands = normalized
    files = (
        CentralProjectFile(
            path="pyproject.toml",
            content=_render_pyproject(identity, runtime, development),
        ),
        CentralProjectFile(
            path="README.md",
            content=_render_readme(identity, sections, commands),
        ),
        CentralProjectFile(path=".env.example", content=_render_environment(environment)),
        CentralProjectFile(
            path=".github/workflows/quality.yml",
            content=_render_ci(commands),
        ),
    )
    return tuple(sorted(files, key=lambda file: file.path))


def _normalize_contributions(
    contributions: tuple[BlueprintContribution, ...],
) -> tuple[
    tuple[PackageRequirement, ...],
    tuple[PackageRequirement, ...],
    tuple[EnvironmentVariableContribution, ...],
    tuple[ReadmeSectionContribution, ...],
    tuple[str, ...],
]:
    owners: set[str] = set()
    runtime: dict[str, PackageRequirement] = {}
    development: dict[str, PackageRequirement] = {}
    environment: dict[str, EnvironmentVariableContribution] = {}
    sections: dict[str, ReadmeSectionContribution] = {}
    commands: set[str] = set()
    for contribution in contributions:
        if contribution.owner in owners:
            raise CentralAssemblyError(
                f"Contribution owner appears more than once: {contribution.owner}."
            )
        owners.add(contribution.owner)
        _add_unique_requirements(runtime, contribution.runtime_dependencies)
        _add_unique_requirements(development, contribution.development_dependencies)
        for variable in contribution.environment_variables:
            if variable.name in environment:
                raise CentralAssemblyError(
                    f"Environment variable has multiple contributors: {variable.name}."
                )
            environment[variable.name] = variable
        for section in contribution.readme_sections:
            if section.key in sections:
                raise CentralAssemblyError(
                    f"README section has multiple contributors: {section.key}."
                )
            sections[section.key] = section
        for command in contribution.quality_commands:
            if command in commands:
                raise CentralAssemblyError(f"Quality command is duplicated: {command}.")
            commands.add(command)
    overlap = sorted(set(runtime) & set(development))
    if overlap:
        raise CentralAssemblyError(
            f"Dependencies cannot be both runtime and development-only: {', '.join(overlap)}."
        )
    return (
        tuple(runtime[name] for name in sorted(runtime)),
        tuple(development[name] for name in sorted(development)),
        tuple(environment[name] for name in sorted(environment)),
        tuple(sorted(sections.values(), key=lambda section: (section.order, section.key))),
        tuple(sorted(commands)),
    )


def _add_unique_requirements(
    destination: dict[str, PackageRequirement],
    requirements: tuple[PackageRequirement, ...],
) -> None:
    for requirement in requirements:
        if requirement.name in destination:
            raise CentralAssemblyError(f"Dependency has multiple contributors: {requirement.name}.")
        destination[requirement.name] = requirement


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...]) -> str:
    if not values:
        return "[]"
    items = "\n".join(f"    {_toml_string(value)}," for value in values)
    return f"[\n{items}\n]"


def _render_pyproject(
    identity: ProjectIdentity,
    runtime: tuple[PackageRequirement, ...],
    development: tuple[PackageRequirement, ...],
) -> str:
    distribution_name = identity.package_name.replace("_", "-")
    python_minor = GENERATED_PROJECT_PYTHON
    python_major, python_minor_number = (int(part) for part in python_minor.split("."))
    next_python_minor = f"{python_major}.{python_minor_number + 1}"
    runtime_pins = tuple(requirement.pinned for requirement in runtime)
    development_pins = tuple(requirement.pinned for requirement in development)
    return f"""[build-system]
requires = ["setuptools=={SETUPTOOLS_VERSION}"]
build-backend = "setuptools.build_meta"

[project]
name = {_toml_string(distribution_name)}
version = "0.1.0"
description = {_toml_string(identity.description)}
readme = "README.md"
requires-python = ">={python_minor},<{next_python_minor}"
dependencies = {_toml_array(runtime_pins)}

[dependency-groups]
dev = {_toml_array(development_pins)}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
addopts = ["-ra", "--strict-config", "--strict-markers"]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
target-version = "py314"

[tool.ruff.lint]
select = ["B", "E", "F", "I", "RUF", "SIM", "UP"]

[tool.mypy]
files = ["src", "tests"]
mypy_path = "src"
python_version = "{python_minor}"
strict = true
"""


def _render_readme(
    identity: ProjectIdentity,
    sections: tuple[ReadmeSectionContribution, ...],
    commands: tuple[str, ...],
) -> str:
    content = [
        f"# {identity.project_name}",
        "",
        identity.description,
        "",
        "## Setup",
        "",
        "Install the frozen environment with `uv sync --all-groups --frozen`.",
        "",
    ]
    for section in sections:
        content.extend((f"## {section.title}", "", section.body, ""))
    content.extend(("## Quality", "", "```console", *commands, "```", ""))
    return "\n".join(content)


def _render_environment(
    environment: tuple[EnvironmentVariableContribution, ...],
) -> str:
    lines: list[str] = []
    for variable in environment:
        lines.extend((f"# {variable.description}", f"{variable.name}={variable.example}", ""))
    return "\n".join(lines)


def _render_ci(commands: tuple[str, ...]) -> str:
    command_steps = "\n".join(
        f"      - name: Quality {index}\n        run: {command}"
        for index, command in enumerate(commands, start=1)
    )
    return f"""name: quality

on:
  pull_request:
  push:

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v6
        with:
          python-version: "{GENERATED_PROJECT_PYTHON}"
      - uses: astral-sh/setup-uv@v7
        with:
          version: "{UV_VERSION}"
      - run: uv sync --all-groups --frozen
{command_steps}
"""
