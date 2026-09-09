"""Atomic compilation of complete trusted built-in recipe registrations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import NoReturn, TextIO, TypeAlias

from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyAdapter,
    ProjectAssemblyAdapterRegistry,
    ProjectAssemblyAdapterRegistryError,
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_recipe_registry import (
    ProjectRecipe,
    ProjectRecipeRegistry,
    ProjectRecipeRegistryError,
    build_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationAdapterRegistry,
    ProjectValidationAdapterRegistryError,
    build_project_validation_adapter_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    AnswerNormalizer,
    JSONValue,
    RecipeProjectConfiguration,
)

LOGGER = logging.getLogger(__name__)

RecipeAnswerCollector: TypeAlias = Callable[[TextIO, TextIO], dict[str, JSONValue]]
RecipeRuntimeFactory: TypeAlias = Callable[
    [RecipeProjectConfiguration, ProjectRecipe, str],
    object,
]


class TrustedRecipeRegistrationError(RuntimeError):
    """A value-free error raised while compiling trusted recipe components."""

    def __init__(self, *, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RecipeQuestionnaireRegistration:
    """One trusted interactive questionnaire bound to a recipe identity."""

    recipe_id: str
    label: str
    collector: RecipeAnswerCollector


@dataclass(frozen=True, slots=True)
class RecipeRuntimeFactoryRegistration:
    """One trusted validation-runtime factory bound to a recipe identity."""

    recipe_id: str
    factory: RecipeRuntimeFactory


@dataclass(frozen=True, slots=True)
class TrustedRecipeRegistration:
    """Every executable and declarative component required by one built-in recipe."""

    declaration: Mapping[str, object]
    answer_parser_key: str
    answer_normalizer: AnswerNormalizer
    questionnaire: RecipeQuestionnaireRegistration
    assembly_adapter_key: str
    assembly_adapter: ProjectAssemblyAdapter
    validation_adapter: ProjectValidationAdapterRegistration
    runtime_factory: RecipeRuntimeFactoryRegistration


@dataclass(frozen=True, slots=True)
class CompiledTrustedRecipeRegistrations:
    """All immutable registries produced by one successful atomic compilation."""

    recipe_registry: ProjectRecipeRegistry
    answer_normalizers: Mapping[str, AnswerNormalizer]
    questionnaires: tuple[RecipeQuestionnaireRegistration, ...]
    assembly_registry: ProjectAssemblyAdapterRegistry
    validation_registry: ProjectValidationAdapterRegistry
    runtime_factories: tuple[RecipeRuntimeFactoryRegistration, ...]


def compile_trusted_recipe_registrations(
    registrations: Iterable[TrustedRecipeRegistration],
) -> CompiledTrustedRecipeRegistrations:
    """Validate a complete registration set before exposing any compiled registry."""
    frozen = tuple(registrations)
    LOGGER.info("trusted_recipe_registration_compile_started registrations=%d", len(frozen))
    try:
        compiled = _compile_trusted_recipe_registrations(frozen)
    except TrustedRecipeRegistrationError as error:
        LOGGER.error("trusted_recipe_registration_compile_failed code=%s", error.code)
        raise
    except (
        ProjectAssemblyAdapterRegistryError,
        ProjectRecipeRegistryError,
        ProjectValidationAdapterRegistryError,
    ) as error:
        code = error.code
        LOGGER.error("trusted_recipe_registration_compile_failed code=%s", code)
        raise TrustedRecipeRegistrationError(
            code=code,
            message="Trusted recipe registrations could not be compiled.",
        ) from error
    except Exception as error:
        LOGGER.error(
            "trusted_recipe_registration_compile_failed code=unexpected_error error_type=%s",
            type(error).__name__,
        )
        raise
    LOGGER.info(
        "trusted_recipe_registration_compile_completed recipes=%d",
        len(compiled.recipe_registry.recipes),
    )
    return compiled


def _compile_trusted_recipe_registrations(
    registrations: tuple[TrustedRecipeRegistration, ...],
) -> CompiledTrustedRecipeRegistrations:
    if not registrations:
        _raise_registration_error("empty_registration_set")
    for registration in registrations:
        _validate_executable_components(registration)

    recipe_registry = build_project_recipe_registry(
        [registration.declaration for registration in registrations],
        trusted_answer_parser_keys={
            registration.answer_parser_key for registration in registrations
        },
        trusted_assembly_adapter_keys={
            registration.assembly_adapter_key for registration in registrations
        },
        trusted_validation_adapter_keys={
            registration.validation_adapter.adapter_key for registration in registrations
        },
    )
    for recipe, registration in zip(recipe_registry.recipes, registrations, strict=True):
        _validate_recipe_bindings(recipe, registration)

    answer_normalizers = _compile_answer_normalizers(registrations)
    assembly_registry = build_project_assembly_adapter_registry(
        (registration.assembly_adapter_key, registration.assembly_adapter)
        for registration in registrations
    )
    validation_registry = build_project_validation_adapter_registry(
        registration.validation_adapter for registration in registrations
    )
    return CompiledTrustedRecipeRegistrations(
        recipe_registry=recipe_registry,
        answer_normalizers=MappingProxyType(answer_normalizers),
        questionnaires=tuple(registration.questionnaire for registration in registrations),
        assembly_registry=assembly_registry,
        validation_registry=validation_registry,
        runtime_factories=tuple(registration.runtime_factory for registration in registrations),
    )


def _validate_executable_components(registration: TrustedRecipeRegistration) -> None:
    if not isinstance(registration.declaration, Mapping):
        _raise_registration_error("invalid_recipe_declaration")
    if not registration.answer_parser_key or not callable(registration.answer_normalizer):
        _raise_registration_error("invalid_answer_normalizer")
    if (
        not registration.questionnaire.recipe_id
        or not registration.questionnaire.label.strip()
        or not callable(registration.questionnaire.collector)
    ):
        _raise_registration_error("invalid_questionnaire")
    if not registration.assembly_adapter_key or not callable(registration.assembly_adapter):
        _raise_registration_error("invalid_assembly_adapter")
    if not callable(registration.validation_adapter.adapter):
        _raise_registration_error("invalid_validation_adapter")
    if not registration.runtime_factory.recipe_id or not callable(
        registration.runtime_factory.factory
    ):
        _raise_registration_error("invalid_runtime_factory")


def _validate_recipe_bindings(
    recipe: ProjectRecipe,
    registration: TrustedRecipeRegistration,
) -> None:
    if recipe.answer_parser_key != registration.answer_parser_key:
        _raise_registration_error("answer_parser_binding_mismatch")
    if recipe.assembly_adapter_key != registration.assembly_adapter_key:
        _raise_registration_error("assembly_adapter_binding_mismatch")
    if recipe.validation_adapter_key != registration.validation_adapter.adapter_key:
        _raise_registration_error("validation_adapter_binding_mismatch")
    if recipe.recipe_id != registration.questionnaire.recipe_id:
        _raise_registration_error("questionnaire_recipe_mismatch")
    if recipe.recipe_id != registration.runtime_factory.recipe_id:
        _raise_registration_error("runtime_factory_recipe_mismatch")
    if set(recipe.allowed_validation_gates) != set(
        registration.validation_adapter.validation_gates
    ):
        _raise_registration_error("validation_gate_binding_mismatch")


def _compile_answer_normalizers(
    registrations: tuple[TrustedRecipeRegistration, ...],
) -> dict[str, AnswerNormalizer]:
    compiled: dict[str, AnswerNormalizer] = {}
    for registration in registrations:
        if registration.answer_parser_key in compiled:
            _raise_registration_error("duplicate_answer_parser")
        compiled[registration.answer_parser_key] = registration.answer_normalizer
    return compiled


def _raise_registration_error(code: str) -> NoReturn:
    raise TrustedRecipeRegistrationError(
        code=code,
        message="Trusted recipe registrations could not be compiled.",
    )
