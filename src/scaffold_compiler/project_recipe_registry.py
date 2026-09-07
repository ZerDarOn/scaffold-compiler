"""Strict declarations and registration for trusted built-in project recipes."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Final, NoReturn

RECIPE_DECLARATION_SCHEMA_VERSION: Final = 1
FASTAPI_RECIPE_ID: Final = "python-fastapi-service"
FASTAPI_ANSWER_PARSER_KEY: Final = "fastapi-answers"
FASTAPI_ASSEMBLY_ADAPTER_KEY: Final = "fastapi-v1-assembly"
FASTAPI_VALIDATION_ADAPTER_KEY: Final = "fastapi-v1-validation"

_IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SEMANTIC_VERSION_PATTERN: Final = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_RECIPE_FIELDS: Final = frozenset(
    {
        "allowed_blueprints",
        "allowed_validations",
        "answer_parser",
        "assembly_adapter",
        "capability_rules",
        "id",
        "prerequisites",
        "required_capabilities",
        "schema_version",
        "validation_adapter",
        "version",
    }
)


class ProjectRecipeRegistryError(ValueError):
    """A recipe declaration error safe to surface without declaration values."""

    def __init__(self, *, field: str, code: str, message: str) -> None:
        self.field = field
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RecipeCapabilityRule:
    """Declarative answer conditions that request one or more capabilities."""

    conditions: tuple[tuple[str, str], ...]
    requested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectRecipe:
    """Immutable metadata for a recipe whose executable adapters are trusted code."""

    recipe_id: str
    version: str
    answer_parser_key: str
    assembly_adapter_key: str
    validation_adapter_key: str
    required_capabilities: tuple[str, ...]
    capability_rules: tuple[RecipeCapabilityRule, ...]
    allowed_blueprint_ids: tuple[str, ...]
    allowed_validation_gates: tuple[str, ...]
    prerequisites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectRecipeRegistry:
    """Immutable collection of uniquely identified project recipes."""

    recipes: tuple[ProjectRecipe, ...]

    def get(self, recipe_id: str) -> ProjectRecipe:
        """Return a registered recipe or raise a stable, value-free error."""
        for recipe in self.recipes:
            if recipe.recipe_id == recipe_id:
                return recipe
        raise ProjectRecipeRegistryError(
            field="recipe",
            code="unknown_recipe",
            message="Project recipe is not registered.",
        )


def build_project_recipe_registry(
    raw_recipes: Iterable[Mapping[str, object]],
    *,
    trusted_answer_parser_keys: Set[str],
    trusted_assembly_adapter_keys: Set[str],
    trusted_validation_adapter_keys: Set[str],
) -> ProjectRecipeRegistry:
    """Parse declarations and bind them only to explicitly trusted adapter keys."""
    recipes: list[ProjectRecipe] = []
    recipe_ids: set[str] = set()
    for raw_recipe in raw_recipes:
        recipe = _parse_project_recipe(raw_recipe)
        if recipe.recipe_id in recipe_ids:
            raise ProjectRecipeRegistryError(
                field="id",
                code="duplicate_recipe_id",
                message="Recipe identifiers must be unique.",
            )
        _require_trusted_adapter(
            recipe.answer_parser_key,
            trusted_keys=trusted_answer_parser_keys,
            field="answer_parser",
            code="unknown_answer_parser",
        )
        _require_trusted_adapter(
            recipe.assembly_adapter_key,
            trusted_keys=trusted_assembly_adapter_keys,
            field="assembly_adapter",
            code="unknown_assembly_adapter",
        )
        _require_trusted_adapter(
            recipe.validation_adapter_key,
            trusted_keys=trusted_validation_adapter_keys,
            field="validation_adapter",
            code="unknown_validation_adapter",
        )
        recipe_ids.add(recipe.recipe_id)
        recipes.append(recipe)
    return ProjectRecipeRegistry(recipes=tuple(recipes))


def build_builtin_project_recipe_registry() -> ProjectRecipeRegistry:
    """Return the registry shipped with this compiler version."""
    return build_project_recipe_registry(
        [_FASTAPI_RECIPE_DECLARATION],
        trusted_answer_parser_keys={FASTAPI_ANSWER_PARSER_KEY},
        trusted_assembly_adapter_keys={FASTAPI_ASSEMBLY_ADAPTER_KEY},
        trusted_validation_adapter_keys={FASTAPI_VALIDATION_ADAPTER_KEY},
    )


def _parse_project_recipe(raw_recipe: Mapping[str, object]) -> ProjectRecipe:
    if set(raw_recipe).difference(_RECIPE_FIELDS):
        raise ProjectRecipeRegistryError(
            field="recipe",
            code="unknown_recipe_fields",
            message="Recipe declaration contains unsupported fields.",
        )
    raw_schema_version = raw_recipe.get("schema_version")
    if (
        not isinstance(raw_schema_version, int)
        or isinstance(raw_schema_version, bool)
        or raw_schema_version != RECIPE_DECLARATION_SCHEMA_VERSION
    ):
        _raise_recipe_error("schema_version", "unsupported_recipe_schema")

    recipe_id = _parse_identifier(raw_recipe.get("id"), "id", "invalid_recipe_id")
    version = raw_recipe.get("version")
    if not isinstance(version, str) or not _SEMANTIC_VERSION_PATTERN.fullmatch(version):
        _raise_recipe_error("version", "invalid_recipe_version")

    allowed_blueprints = _parse_identifier_sequence(
        raw_recipe.get("allowed_blueprints"),
        field="allowed_blueprints",
        duplicate_code="duplicate_blueprint_id",
    )
    required_capabilities = _parse_identifier_sequence(
        raw_recipe.get("required_capabilities"),
        field="required_capabilities",
        duplicate_code="duplicate_required_capability",
    )
    allowed_validations = _parse_identifier_sequence(
        raw_recipe.get("allowed_validations"),
        field="allowed_validations",
        duplicate_code="duplicate_validation_gate",
    )
    allowed_blueprint_set = set(allowed_blueprints)
    if not set(required_capabilities).issubset(allowed_blueprint_set):
        _raise_recipe_error("required_capabilities", "unknown_capability")

    capability_rules = _parse_capability_rules(
        raw_recipe.get("capability_rules"),
        allowed_capabilities=allowed_blueprint_set,
    )
    prerequisites = _parse_prerequisites(raw_recipe.get("prerequisites"))

    return ProjectRecipe(
        recipe_id=recipe_id,
        version=version,
        answer_parser_key=_parse_nonempty_string(raw_recipe.get("answer_parser"), "answer_parser"),
        assembly_adapter_key=_parse_nonempty_string(
            raw_recipe.get("assembly_adapter"), "assembly_adapter"
        ),
        validation_adapter_key=_parse_nonempty_string(
            raw_recipe.get("validation_adapter"), "validation_adapter"
        ),
        required_capabilities=required_capabilities,
        capability_rules=capability_rules,
        allowed_blueprint_ids=allowed_blueprints,
        allowed_validation_gates=allowed_validations,
        prerequisites=prerequisites,
    )


def _parse_capability_rules(
    raw_rules: object,
    *,
    allowed_capabilities: set[str],
) -> tuple[RecipeCapabilityRule, ...]:
    if not isinstance(raw_rules, list):
        _raise_recipe_error("capability_rules", "invalid_capability_rules")
    rules: list[RecipeCapabilityRule] = []
    seen_conditions: set[tuple[tuple[str, str], ...]] = set()
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, Mapping) or set(raw_rule) != {"when", "request"}:
            _raise_recipe_error("capability_rules", "invalid_capability_rule")
        raw_conditions = raw_rule["when"]
        if not isinstance(raw_conditions, Mapping) or not raw_conditions:
            _raise_recipe_error("capability_rules", "invalid_capability_rule")
        conditions: list[tuple[str, str]] = []
        for key, value in raw_conditions.items():
            if not isinstance(key, str) or not key or not isinstance(value, str) or not value:
                _raise_recipe_error("capability_rules", "invalid_capability_rule")
            conditions.append((key, value))
        canonical_conditions = tuple(sorted(conditions))
        if canonical_conditions in seen_conditions:
            _raise_recipe_error("capability_rules", "duplicate_capability_rule")
        requested = _parse_identifier_sequence(
            raw_rule["request"],
            field="capability_rules",
            duplicate_code="duplicate_requested_capability",
        )
        if not requested or not set(requested).issubset(allowed_capabilities):
            _raise_recipe_error("capability_rules", "unknown_capability")
        seen_conditions.add(canonical_conditions)
        rules.append(
            RecipeCapabilityRule(
                conditions=canonical_conditions,
                requested_capabilities=requested,
            )
        )
    return tuple(rules)


def _parse_identifier_sequence(
    raw_values: object,
    *,
    field: str,
    duplicate_code: str,
) -> tuple[str, ...]:
    if not isinstance(raw_values, list):
        _raise_recipe_error(field, "invalid_identifier_list")
    values = tuple(
        _parse_identifier(raw_value, field, "invalid_identifier") for raw_value in raw_values
    )
    if len(values) != len(set(values)):
        _raise_recipe_error(field, duplicate_code)
    return values


def _parse_prerequisites(raw_prerequisites: object) -> tuple[str, ...]:
    if not isinstance(raw_prerequisites, list):
        _raise_recipe_error("prerequisites", "invalid_prerequisites")
    prerequisites: list[str] = []
    for prerequisite in raw_prerequisites:
        if not isinstance(prerequisite, str) or not prerequisite.strip():
            _raise_recipe_error("prerequisites", "invalid_prerequisites")
        prerequisites.append(prerequisite.strip())
    return tuple(prerequisites)


def _parse_identifier(raw_value: object, field: str, code: str) -> str:
    if not isinstance(raw_value, str) or not _IDENTIFIER_PATTERN.fullmatch(raw_value):
        _raise_recipe_error(field, code)
    return raw_value


def _parse_nonempty_string(raw_value: object, field: str) -> str:
    if not isinstance(raw_value, str) or not raw_value:
        _raise_recipe_error(field, "invalid_adapter_key")
    return raw_value


def _require_trusted_adapter(
    adapter_key: str,
    *,
    trusted_keys: Set[str],
    field: str,
    code: str,
) -> None:
    if adapter_key not in trusted_keys:
        _raise_recipe_error(field, code)


def _raise_recipe_error(field: str, code: str) -> NoReturn:
    raise ProjectRecipeRegistryError(
        field=field,
        code=code,
        message="Project recipe declaration is invalid.",
    )


_FASTAPI_RECIPE_DECLARATION: Final[Mapping[str, object]] = {
    "schema_version": 1,
    "id": FASTAPI_RECIPE_ID,
    "version": "1.0.0",
    "answer_parser": FASTAPI_ANSWER_PARSER_KEY,
    "assembly_adapter": FASTAPI_ASSEMBLY_ADAPTER_KEY,
    "validation_adapter": FASTAPI_VALIDATION_ADAPTER_KEY,
    "required_capabilities": [
        "project-quality",
        "python-runtime",
        "fastapi-http-api",
        "final-project-assembly",
    ],
    "capability_rules": [
        {"when": {"database": "postgres"}, "request": ["postgres-persistence"]},
        {"when": {"delivery": "docker"}, "request": ["docker-delivery"]},
        {
            "when": {"database": "postgres", "delivery": "docker"},
            "request": ["postgres-docker-integration"],
        },
    ],
    "allowed_blueprints": [
        "project-quality",
        "python-runtime",
        "fastapi-http-api",
        "postgres-persistence",
        "docker-delivery",
        "postgres-docker-integration",
        "final-project-assembly",
    ],
    "allowed_validations": [
        "ruff-format",
        "ruff-lint",
        "mypy",
        "pytest",
        "python-syntax",
        "application-start",
        "health-live",
        "health-ready",
        "openapi",
        "postgres-connect",
        "alembic-upgrade",
        "database-readiness",
        "session-rollback",
        "docker-build",
        "container-non-root",
        "container-health",
        "compose-config",
        "compose-up",
        "compose-health",
        "compose-cleanup",
    ],
    "prerequisites": ["Python 3.11 or newer", "uv 0.12.9"],
}
