"""Trusted interactive questionnaires that emit validated configuration only."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import TextIO, TypeVar

from scaffold_compiler.project_recipe_registry import (
    CMAKE_RECIPE_ID,
    FASTAPI_RECIPE_ID,
    GO_RECIPE_ID,
    ProjectRecipeRegistry,
)
from scaffold_compiler.recipe_input_descriptor import (
    RecipeInputDescriptor,
    RecipeInputOmission,
    RecipeInputType,
)
from scaffold_compiler.recipe_project_configuration import (
    AnswerNormalizer,
    JSONValue,
    parse_recipe_project_configuration,
)
from scaffold_compiler.trusted_recipe_registration import (
    RecipeQuestionnaireRegistration,
    RecipeQuestionnaireRegistry,
    build_recipe_questionnaire_registry,
)


class ConfigurationInitializationError(ValueError):
    """A value-free initialization failure safe to handle at the CLI boundary."""


Choice = TypeVar("Choice")


def build_fastapi_recipe_questionnaire_registration() -> RecipeQuestionnaireRegistration:
    """Return the trusted FastAPI questionnaire registration."""
    inputs = (
        RecipeInputDescriptor(
            "package_name",
            "Python package name",
            RecipeInputType.STRING,
            RecipeInputOmission.OMIT,
            interactive_hint="derived from project name",
        ),
        RecipeInputDescriptor(
            "database",
            "Database",
            RecipeInputType.CHOICE,
            RecipeInputOmission.LITERAL,
            choices=("none", "postgres"),
            literal_default="none",
        ),
        RecipeInputDescriptor(
            "delivery",
            "Delivery",
            RecipeInputType.CHOICE,
            RecipeInputOmission.LITERAL,
            choices=("none", "docker"),
            literal_default="none",
        ),
    )
    return build_declared_recipe_questionnaire_registration(
        FASTAPI_RECIPE_ID,
        "Python FastAPI service",
        inputs,
    )


def build_cmake_recipe_questionnaire_registration() -> RecipeQuestionnaireRegistration:
    """Return the trusted CMake questionnaire registration."""
    inputs = (
        RecipeInputDescriptor(
            "target_name",
            "CMake target name",
            RecipeInputType.STRING,
            RecipeInputOmission.OMIT,
            interactive_hint="derived from project name",
        ),
        RecipeInputDescriptor(
            "strict_warnings",
            "Strict compiler warnings",
            RecipeInputType.BOOLEAN,
            RecipeInputOmission.LITERAL,
            literal_default=True,
        ),
    )
    return build_declared_recipe_questionnaire_registration(
        CMAKE_RECIPE_ID,
        "C11/CMake command-line project",
        inputs,
    )


def build_go_recipe_questionnaire_registration() -> RecipeQuestionnaireRegistration:
    """Return the trusted Go questionnaire registration."""
    inputs = (
        RecipeInputDescriptor(
            "binary_name",
            "Go binary name",
            RecipeInputType.STRING,
            RecipeInputOmission.OMIT,
            interactive_hint="derived from project name",
        ),
        RecipeInputDescriptor(
            "module_path",
            "Go module path",
            RecipeInputType.STRING,
            RecipeInputOmission.OMIT,
            interactive_hint="example.com/<binary name>",
        ),
    )
    return build_declared_recipe_questionnaire_registration(
        GO_RECIPE_ID,
        "Go command-line project",
        inputs,
    )


def build_declared_recipe_questionnaire_registration(
    recipe_id: str,
    label: str,
    inputs: tuple[RecipeInputDescriptor, ...],
) -> RecipeQuestionnaireRegistration:
    """Build a questionnaire whose collector is entirely descriptor-driven."""
    return RecipeQuestionnaireRegistration(
        recipe_id=recipe_id,
        label=label,
        collector=partial(_collect_declared_answers, inputs=inputs),
        inputs=inputs,
    )


def build_builtin_recipe_questionnaire_registry() -> RecipeQuestionnaireRegistry:
    """Return the trusted questionnaires shipped with this compiler version."""
    return build_recipe_questionnaire_registry(
        (
            build_fastapi_recipe_questionnaire_registration(),
            build_cmake_recipe_questionnaire_registration(),
            build_go_recipe_questionnaire_registration(),
        )
    )


def write_interactive_configuration(
    output_path: Path,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    recipe_registry: ProjectRecipeRegistry,
    questionnaire_registry: RecipeQuestionnaireRegistry,
    answer_normalizers: Mapping[str, AnswerNormalizer],
    working_directory: Path,
    home_directory: Path,
) -> Path:
    """Ask trusted questions and atomically create one canonical schema-2 config."""
    try:
        available = questionnaire_registry.for_recipes(recipe_registry)
        if not available:
            raise ConfigurationInitializationError("No interactive recipe is available.")

        output_stream.write("Available project recipes:\n")
        for index, questionnaire in enumerate(available, start=1):
            output_stream.write(f"  {index}. {questionnaire.label} ({questionnaire.recipe_id})\n")
        selection = _ask_choice(
            "Recipe number or id: ",
            choices={str(index): item for index, item in enumerate(available, start=1)}
            | {item.recipe_id: item for item in available},
            input_stream=input_stream,
            output_stream=output_stream,
        )
        project_name = _ask_required(
            "Project name: ", input_stream=input_stream, output_stream=output_stream
        )
        target_directory = _ask_required(
            "Target directory: ", input_stream=input_stream, output_stream=output_stream
        )
        answers = selection.collector(input_stream, output_stream)
        raw_configuration: dict[str, object] = {
            "answers": answers,
            "project_name": project_name,
            "recipe": selection.recipe_id,
            "schema_version": 2,
            "target_directory": target_directory,
        }
        configuration = parse_recipe_project_configuration(
            raw_configuration,
            registry=recipe_registry,
            answer_normalizers=answer_normalizers,
            working_directory=working_directory,
            home_directory=home_directory,
        )
        canonical = {
            "answers": configuration.answers,
            "project_name": configuration.project_name,
            "recipe": configuration.recipe_id,
            "schema_version": configuration.schema_version,
            "target_directory": configuration.target_directory.as_posix(),
        }
        serialized = json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return _publish_without_overwrite(output_path, serialized, working_directory)
    except ConfigurationInitializationError:
        raise
    except (EOFError, OSError, TypeError, ValueError) as error:
        raise ConfigurationInitializationError("Configuration was not created.") from error


def _collect_declared_answers(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    inputs: tuple[RecipeInputDescriptor, ...],
) -> dict[str, JSONValue]:
    answers: dict[str, JSONValue] = {}
    for descriptor in inputs:
        value = _ask_declared_input(
            descriptor,
            input_stream=input_stream,
            output_stream=output_stream,
        )
        if descriptor.omission is RecipeInputOmission.OMIT and value == "":
            continue
        answers[descriptor.key] = value
    return answers


def _ask_declared_input(
    descriptor: RecipeInputDescriptor,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> JSONValue:
    prompt = _declared_input_prompt(descriptor)
    if descriptor.input_type is RecipeInputType.STRING:
        if descriptor.omission is RecipeInputOmission.REJECT:
            return _ask_required(prompt, input_stream=input_stream, output_stream=output_stream)
        value = _ask_optional(prompt, input_stream=input_stream, output_stream=output_stream)
        if value or descriptor.omission is RecipeInputOmission.OMIT:
            return value
        return descriptor.literal_default

    choices: dict[str, JSONValue]
    if descriptor.input_type is RecipeInputType.BOOLEAN:
        choices = {"y": True, "yes": True, "n": False, "no": False}
    else:
        choices = {choice.lower(): choice for choice in descriptor.choices}
    if descriptor.omission is RecipeInputOmission.LITERAL:
        choices[""] = descriptor.literal_default
    elif descriptor.omission is RecipeInputOmission.OMIT:
        choices[""] = ""
    return _ask_choice(
        prompt,
        choices=choices,
        input_stream=input_stream,
        output_stream=output_stream,
    )


def _declared_input_prompt(descriptor: RecipeInputDescriptor) -> str:
    hint = f" [{descriptor.interactive_hint}]" if descriptor.interactive_hint else ""
    if descriptor.input_type is RecipeInputType.STRING:
        default = (
            f" (default {descriptor.literal_default})"
            if descriptor.omission is RecipeInputOmission.LITERAL
            else ""
        )
        return f"{descriptor.label}{hint}{default}: "
    if descriptor.input_type is RecipeInputType.BOOLEAN:
        if descriptor.literal_default is True:
            choices = "Y/n"
        elif descriptor.literal_default is False:
            choices = "y/N"
        else:
            choices = "y/n"
        return f"{descriptor.label} [{choices}]{hint}: "
    default = (
        f" (default {descriptor.literal_default})"
        if descriptor.omission is RecipeInputOmission.LITERAL
        else ""
    )
    return f"{descriptor.label} [{'/'.join(descriptor.choices)}]{hint}{default}: "


def _ask_required(prompt: str, *, input_stream: TextIO, output_stream: TextIO) -> str:
    while True:
        value = _read_answer(prompt, input_stream=input_stream, output_stream=output_stream)
        if value:
            return value
        output_stream.write("A value is required.\n")


def _ask_optional(prompt: str, *, input_stream: TextIO, output_stream: TextIO) -> str:
    return _read_answer(prompt, input_stream=input_stream, output_stream=output_stream)


def _ask_choice(
    prompt: str,
    *,
    choices: Mapping[str, Choice],
    input_stream: TextIO,
    output_stream: TextIO,
) -> Choice:
    while True:
        value = _read_answer(prompt, input_stream=input_stream, output_stream=output_stream).lower()
        if value in choices:
            return choices[value]
        output_stream.write("Please choose one of the listed values.\n")


def _read_answer(prompt: str, *, input_stream: TextIO, output_stream: TextIO) -> str:
    output_stream.write(prompt)
    output_stream.flush()
    value = input_stream.readline()
    if value == "":
        raise EOFError("Interactive input ended.")
    return value.strip()


def _publish_without_overwrite(
    output_path: Path,
    content: str,
    working_directory: Path,
) -> Path:
    requested = output_path if output_path.is_absolute() else working_directory / output_path
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir() or requested.name in {"", ".", ".."}:
        raise ConfigurationInitializationError("Configuration output path is invalid.")
    destination = parent / requested.name
    descriptor, temporary_name = tempfile.mkstemp(prefix=".scaffold-config-", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.unlink()
    return destination
