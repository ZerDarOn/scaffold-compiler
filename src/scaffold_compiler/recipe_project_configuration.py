"""Language-neutral configuration envelopes resolved through trusted recipes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, TypeAlias, cast

from scaffold_compiler.project_configuration import (
    ConfigurationValidationError,
    ProjectConfiguration,
    normalize_project_name,
    normalize_python_package_name,
    normalize_target_directory,
    parse_project_configuration,
)
from scaffold_compiler.project_recipe_registry import (
    CMAKE_ANSWER_PARSER_KEY,
    FASTAPI_ANSWER_PARSER_KEY,
    FASTAPI_RECIPE_ID,
    ProjectRecipe,
    ProjectRecipeRegistry,
    ProjectRecipeRegistryError,
    build_builtin_project_recipe_registry,
)

CONFIGURATION_SCHEMA_VERSION: Final = 2
_V2_FIELDS: Final = frozenset(
    {"answers", "project_name", "recipe", "schema_version", "target_directory"}
)
_V2_MARKER_FIELDS: Final = frozenset({"answers", "recipe", "schema_version"})
_LEGACY_ONLY_FIELDS: Final = frozenset({"container", "database", "package_name"})
_FASTAPI_ANSWER_FIELDS: Final = frozenset({"database", "delivery", "package_name"})
_CMAKE_ANSWER_FIELDS: Final = frozenset({"strict_warnings", "target_name"})
_CMAKE_TARGET_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

JSONScalar: TypeAlias = bool | int | float | str | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
AnswerNormalizer: TypeAlias = Callable[[Mapping[str, object], str], Mapping[str, JSONValue]]


@dataclass(frozen=True, slots=True)
class RecipeProjectConfiguration:
    """Canonical immutable configuration shared by every recipe."""

    schema_version: int
    recipe_id: str
    recipe_version: str
    project_name: str
    target_directory: Path
    _answers_json: str

    @property
    def answers(self) -> dict[str, JSONValue]:
        """Return a defensive copy of canonical recipe answers."""
        return cast(dict[str, JSONValue], json.loads(self._answers_json))

    @property
    def canonical_payload(self) -> str:
        """Return the stable serialized payload used to derive the digest."""
        return json.dumps(
            {
                "answers": self.answers,
                "project_name": self.project_name,
                "recipe": self.recipe_id,
                "recipe_version": self.recipe_version,
                "schema_version": self.schema_version,
                "target_directory": self.target_directory.as_posix(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def configuration_digest(self) -> str:
        """Bind the canonical configuration and selected recipe version."""
        return hashlib.sha256(self.canonical_payload.encode("utf-8")).hexdigest()


def parse_recipe_project_configuration(
    raw_configuration: Mapping[str, object],
    *,
    registry: ProjectRecipeRegistry,
    answer_normalizers: Mapping[str, AnswerNormalizer],
    working_directory: Path,
    home_directory: Path,
) -> RecipeProjectConfiguration:
    """Parse V2 input or migrate legacy V1 input into one canonical envelope."""
    if set(raw_configuration).intersection(_V2_MARKER_FIELDS):
        return _parse_v2_configuration(
            raw_configuration,
            registry=registry,
            answer_normalizers=answer_normalizers,
            working_directory=working_directory,
            home_directory=home_directory,
        )
    return _parse_legacy_fastapi_configuration(
        raw_configuration,
        registry=registry,
        working_directory=working_directory,
        home_directory=home_directory,
    )


def parse_builtin_recipe_project_configuration(
    raw_configuration: Mapping[str, object],
    *,
    working_directory: Path,
    home_directory: Path,
) -> RecipeProjectConfiguration:
    """Parse configuration using only adapters shipped as trusted compiler code."""
    return parse_recipe_project_configuration(
        raw_configuration,
        registry=build_builtin_project_recipe_registry(),
        answer_normalizers=build_builtin_answer_normalizers(),
        working_directory=working_directory,
        home_directory=home_directory,
    )


def build_builtin_answer_normalizers() -> Mapping[str, AnswerNormalizer]:
    """Return the statically imported answer normalizers shipped by the compiler."""
    return {
        FASTAPI_ANSWER_PARSER_KEY: _normalize_fastapi_answers,
        CMAKE_ANSWER_PARSER_KEY: _normalize_cmake_answers,
    }


def convert_legacy_project_configuration(
    configuration: ProjectConfiguration,
    *,
    registry: ProjectRecipeRegistry | None = None,
) -> RecipeProjectConfiguration:
    """Convert one validated legacy FastAPI configuration to the canonical envelope."""
    recipe = _get_recipe(registry or build_builtin_project_recipe_registry(), FASTAPI_RECIPE_ID)
    return _build_configuration(
        recipe=recipe,
        project_name=configuration.project_name,
        target_directory=configuration.target_directory,
        answers={
            "database": configuration.database.value,
            "delivery": configuration.container.value,
            "package_name": configuration.package_name,
        },
    )


def _parse_v2_configuration(
    raw_configuration: Mapping[str, object],
    *,
    registry: ProjectRecipeRegistry,
    answer_normalizers: Mapping[str, AnswerNormalizer],
    working_directory: Path,
    home_directory: Path,
) -> RecipeProjectConfiguration:
    if set(raw_configuration).intersection(_LEGACY_ONLY_FIELDS):
        _raise_configuration_error(
            "configuration",
            "mixed_configuration_schema",
            "Legacy fields cannot be mixed with V2 configuration fields.",
        )
    if set(raw_configuration).difference(_V2_FIELDS):
        _raise_configuration_error(
            "configuration",
            "unknown_fields",
            "Configuration contains unsupported fields.",
        )
    raw_schema_version = raw_configuration.get("schema_version")
    if (
        not isinstance(raw_schema_version, int)
        or isinstance(raw_schema_version, bool)
        or raw_schema_version != CONFIGURATION_SCHEMA_VERSION
    ):
        _raise_configuration_error(
            "schema_version",
            "unsupported_schema_version",
            "Configuration schema version is not supported.",
        )
    raw_recipe_id = raw_configuration.get("recipe")
    if not isinstance(raw_recipe_id, str):
        _raise_configuration_error("recipe", "unknown_recipe", "Project recipe is not registered.")
    recipe = _get_recipe(registry, raw_recipe_id)
    project_name = normalize_project_name(raw_configuration.get("project_name"))
    target_directory = normalize_target_directory(
        raw_configuration.get("target_directory"),
        working_directory=working_directory,
        home_directory=home_directory,
    )
    raw_answers = raw_configuration.get("answers")
    if not isinstance(raw_answers, Mapping) or any(not isinstance(key, str) for key in raw_answers):
        _raise_configuration_error(
            "answers",
            "invalid_answers",
            "Recipe answers must be an object with string keys.",
        )
    normalizer = answer_normalizers.get(recipe.answer_parser_key)
    if normalizer is None:
        _raise_configuration_error(
            "recipe",
            "unavailable_answer_parser",
            "Recipe answer parser is unavailable.",
        )
    answers = normalizer(cast(Mapping[str, object], raw_answers), project_name)
    return _build_configuration(
        recipe=recipe,
        project_name=project_name,
        target_directory=target_directory,
        answers=answers,
    )


def _parse_legacy_fastapi_configuration(
    raw_configuration: Mapping[str, object],
    *,
    registry: ProjectRecipeRegistry,
    working_directory: Path,
    home_directory: Path,
) -> RecipeProjectConfiguration:
    legacy = parse_project_configuration(
        raw_configuration,
        working_directory=working_directory,
        home_directory=home_directory,
    )
    return convert_legacy_project_configuration(legacy, registry=registry)


def _normalize_fastapi_answers(
    raw_answers: Mapping[str, object],
    project_name: str,
) -> Mapping[str, JSONValue]:
    if set(raw_answers).difference(_FASTAPI_ANSWER_FIELDS):
        _raise_configuration_error(
            "answers",
            "unknown_answer_fields",
            "Recipe answers contain unsupported fields.",
        )
    package_name = normalize_python_package_name(
        raw_answers.get("package_name"),
        project_name=project_name,
    )
    database = _normalize_choice(raw_answers.get("database", "postgres"), field="database")
    delivery = _normalize_choice(raw_answers.get("delivery", "docker"), field="delivery")
    return {
        "database": database,
        "delivery": delivery,
        "package_name": package_name,
    }


def _normalize_cmake_answers(
    raw_answers: Mapping[str, object],
    project_name: str,
) -> Mapping[str, JSONValue]:
    if set(raw_answers).difference(_CMAKE_ANSWER_FIELDS):
        _raise_configuration_error(
            "answers",
            "unknown_answer_fields",
            "Recipe answers contain unsupported fields.",
        )
    default_target_name = re.sub(r"[^a-z0-9]+", "_", project_name.lower()).strip("_")
    target_name = raw_answers.get("target_name", default_target_name)
    if not isinstance(target_name, str) or not _CMAKE_TARGET_NAME_PATTERN.fullmatch(target_name):
        _raise_configuration_error(
            "target_name",
            "invalid_target_name",
            "CMake target name is not a portable C identifier.",
        )
    strict_warnings = raw_answers.get("strict_warnings", True)
    if type(strict_warnings) is not bool:
        _raise_configuration_error(
            "strict_warnings",
            "invalid_strict_warnings",
            "Strict warnings must be a boolean.",
        )
    return {"strict_warnings": strict_warnings, "target_name": target_name}


def _normalize_choice(raw_choice: object, *, field: str) -> str:
    allowed_choices = {"database": {"none", "postgres"}, "delivery": {"none", "docker"}}
    if not isinstance(raw_choice, str) or raw_choice not in allowed_choices[field]:
        _raise_configuration_error(
            field,
            "unknown_choice",
            f"{field.replace('_', ' ').title()} choice is not supported.",
        )
    return raw_choice


def _build_configuration(
    *,
    recipe: ProjectRecipe,
    project_name: str,
    target_directory: Path,
    answers: Mapping[str, JSONValue],
) -> RecipeProjectConfiguration:
    return RecipeProjectConfiguration(
        schema_version=CONFIGURATION_SCHEMA_VERSION,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        project_name=project_name,
        target_directory=target_directory,
        _answers_json=_canonicalize_answers(answers),
    )


def _canonicalize_answers(answers: Mapping[str, JSONValue]) -> str:
    _validate_json_value(dict(answers))
    return json.dumps(
        answers,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        _raise_configuration_error("answers", "invalid_answers", "Recipe answers must be JSON.")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            _raise_configuration_error("answers", "invalid_answers", "Recipe answers must be JSON.")
        for item in value.values():
            _validate_json_value(item)
        return
    _raise_configuration_error("answers", "invalid_answers", "Recipe answers must be JSON.")


def _get_recipe(registry: ProjectRecipeRegistry, recipe_id: str) -> ProjectRecipe:
    try:
        return registry.get(recipe_id)
    except ProjectRecipeRegistryError as error:
        raise ConfigurationValidationError(
            field="recipe",
            code="unknown_recipe",
            message="Project recipe is not registered.",
        ) from error


def _raise_configuration_error(field: str, code: str, message: str) -> NoReturn:
    raise ConfigurationValidationError(field=field, code=code, message=message)
