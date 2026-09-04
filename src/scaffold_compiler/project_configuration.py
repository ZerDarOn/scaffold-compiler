"""Validation and normalization for the supported project configuration."""

from __future__ import annotations

import hashlib
import json
import keyword
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeVar

MAXIMUM_PROJECT_NAME_LENGTH: Final = 100

_ALLOWED_FIELDS: Final = frozenset(
    {
        "container",
        "database",
        "package_name",
        "project_name",
        "target_directory",
    }
)
_PACKAGE_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_PACKAGE_SEPARATOR_PATTERN: Final = re.compile(r"[^a-zA-Z0-9]+")

ChoiceType = TypeVar("ChoiceType", bound=StrEnum)


class DatabaseChoice(StrEnum):
    """Database variants supported by V1."""

    NONE = "none"
    POSTGRES = "postgres"


class ContainerChoice(StrEnum):
    """Container variants supported by V1."""

    NONE = "none"
    DOCKER = "docker"


class ConfigurationValidationError(ValueError):
    """A configuration error safe to surface without including input values."""

    def __init__(self, *, field: str, code: str, message: str) -> None:
        self.field = field
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProjectConfiguration:
    """Canonical, immutable inputs for one generation session."""

    project_name: str
    package_name: str
    target_directory: Path
    database: DatabaseChoice
    container: ContainerChoice

    @property
    def configuration_digest(self) -> str:
        """Return a stable digest for the canonical configuration."""
        canonical_payload = {
            "container": self.container.value,
            "database": self.database.value,
            "package_name": self.package_name,
            "project_name": self.project_name,
            "target_directory": self.target_directory.as_posix(),
        }
        serialized_payload = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()


def parse_project_configuration(
    raw_configuration: Mapping[str, object],
    *,
    working_directory: Path,
    home_directory: Path,
) -> ProjectConfiguration:
    """Validate untrusted values and return their canonical representation."""
    _reject_unknown_fields(raw_configuration)

    project_name = _normalize_project_name(raw_configuration.get("project_name"))
    package_name = _normalize_package_name(
        raw_configuration.get("package_name"),
        project_name=project_name,
    )
    target_directory = _normalize_target_directory(
        raw_configuration.get("target_directory"),
        working_directory=working_directory,
        home_directory=home_directory,
    )
    database = _parse_choice(
        raw_configuration.get("database", DatabaseChoice.POSTGRES.value),
        field="database",
        choice_type=DatabaseChoice,
    )
    container = _parse_choice(
        raw_configuration.get("container", ContainerChoice.DOCKER.value),
        field="container",
        choice_type=ContainerChoice,
    )

    return ProjectConfiguration(
        project_name=project_name,
        package_name=package_name,
        target_directory=target_directory,
        database=database,
        container=container,
    )


def _reject_unknown_fields(raw_configuration: Mapping[str, object]) -> None:
    unknown_fields = sorted(set(raw_configuration).difference(_ALLOWED_FIELDS))
    if unknown_fields:
        field_names = ", ".join(str(field) for field in unknown_fields)
        raise ConfigurationValidationError(
            field="configuration",
            code="unknown_fields",
            message=f"Unknown configuration fields: {field_names}",
        )


def _normalize_project_name(raw_project_name: object) -> str:
    if not isinstance(raw_project_name, str):
        raise ConfigurationValidationError(
            field="project_name",
            code="missing_or_invalid_project_name",
            message="Project name must be a string.",
        )
    if _contains_control_characters(raw_project_name):
        raise ConfigurationValidationError(
            field="project_name",
            code="invalid_project_name",
            message="Project name cannot contain control characters.",
        )

    project_name = " ".join(raw_project_name.split())
    if not project_name:
        raise ConfigurationValidationError(
            field="project_name",
            code="invalid_project_name",
            message="Project name cannot be empty.",
        )
    if len(project_name) > MAXIMUM_PROJECT_NAME_LENGTH:
        raise ConfigurationValidationError(
            field="project_name",
            code="invalid_project_name",
            message="Project name is too long.",
        )
    return project_name


def _normalize_package_name(raw_package_name: object, *, project_name: str) -> str:
    if raw_package_name is None:
        package_name = _derive_package_name(project_name)
        if not package_name:
            raise ConfigurationValidationError(
                field="package_name",
                code="package_name_required",
                message="An explicit package name is required for this project name.",
            )
    elif isinstance(raw_package_name, str):
        package_name = raw_package_name
    else:
        raise ConfigurationValidationError(
            field="package_name",
            code="invalid_package_name",
            message="Package name must be a lowercase Python identifier.",
        )

    if not _PACKAGE_NAME_PATTERN.fullmatch(package_name) or keyword.iskeyword(package_name):
        raise ConfigurationValidationError(
            field="package_name",
            code="invalid_package_name",
            message="Package name must be a lowercase Python identifier.",
        )
    return package_name


def _derive_package_name(project_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", project_name).encode("ascii", "ignore").decode()
    candidate = _PACKAGE_SEPARATOR_PATTERN.sub("_", ascii_name).strip("_").lower()
    if not _PACKAGE_NAME_PATTERN.fullmatch(candidate) or keyword.iskeyword(candidate):
        return ""
    return candidate


def _normalize_target_directory(
    raw_target_directory: object,
    *,
    working_directory: Path,
    home_directory: Path,
) -> Path:
    if not isinstance(raw_target_directory, str) or not raw_target_directory.strip():
        raise ConfigurationValidationError(
            field="target_directory",
            code="missing_or_invalid_target",
            message="Target directory must be a non-empty path string.",
        )
    if _contains_control_characters(raw_target_directory):
        raise ConfigurationValidationError(
            field="target_directory",
            code="invalid_target",
            message="Target directory cannot contain control characters.",
        )

    try:
        supplied_path = Path(raw_target_directory)
        target_directory = (
            supplied_path if supplied_path.is_absolute() else working_directory / supplied_path
        ).resolve(strict=False)
        normalized_home = home_directory.resolve(strict=False)
    except (OSError, ValueError) as error:
        raise ConfigurationValidationError(
            field="target_directory",
            code="invalid_target",
            message="Target directory could not be normalized.",
        ) from error

    if target_directory == Path(target_directory.anchor):
        _raise_unsafe_target("filesystem_root", "Filesystem root cannot be a target directory.")
    if target_directory == normalized_home:
        _raise_unsafe_target("home", "Home directory cannot be a target directory.")
    if target_directory.exists():
        _raise_unsafe_target("existing", "Target directory must not already exist.")
    if not target_directory.parent.is_dir():
        _raise_unsafe_target("missing_parent", "Target parent directory must already exist.")

    return target_directory


def _raise_unsafe_target(code: str, message: str) -> None:
    raise ConfigurationValidationError(
        field="target_directory",
        code=code,
        message=message,
    )


def _parse_choice(
    raw_choice: object,
    *,
    field: str,
    choice_type: type[ChoiceType],
) -> ChoiceType:
    if not isinstance(raw_choice, str):
        raise ConfigurationValidationError(
            field=field,
            code="unknown_choice",
            message=f"{field.replace('_', ' ').title()} choice is not supported.",
        )
    try:
        return choice_type(raw_choice)
    except ValueError as error:
        raise ConfigurationValidationError(
            field=field,
            code="unknown_choice",
            message=f"{field.replace('_', ' ').title()} choice is not supported.",
        ) from error


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)
