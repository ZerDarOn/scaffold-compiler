"""Trusted interactive questionnaires that emit validated configuration only."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO, TypeVar

from scaffold_compiler.project_recipe_registry import (
    CMAKE_RECIPE_ID,
    FASTAPI_RECIPE_ID,
    ProjectRecipeRegistry,
)
from scaffold_compiler.recipe_project_configuration import (
    AnswerNormalizer,
    JSONValue,
    parse_recipe_project_configuration,
)


class ConfigurationInitializationError(ValueError):
    """A value-free initialization failure safe to handle at the CLI boundary."""


AnswerCollector = Callable[[TextIO, TextIO], dict[str, JSONValue]]
Choice = TypeVar("Choice")


def _collect_fastapi_answers(source: TextIO, sink: TextIO) -> dict[str, JSONValue]:
    return _fastapi_answers(source, sink)


def _collect_cmake_answers(source: TextIO, sink: TextIO) -> dict[str, JSONValue]:
    return _cmake_answers(source, sink)


_QUESTIONNAIRES: tuple[tuple[str, str, AnswerCollector], ...] = (
    (FASTAPI_RECIPE_ID, "Python FastAPI service", _collect_fastapi_answers),
    (CMAKE_RECIPE_ID, "C11/CMake command-line project", _collect_cmake_answers),
)


def write_interactive_configuration(
    output_path: Path,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    recipe_registry: ProjectRecipeRegistry,
    answer_normalizers: Mapping[str, AnswerNormalizer],
    working_directory: Path,
    home_directory: Path,
) -> Path:
    """Ask trusted questions and atomically create one canonical schema-2 config."""
    try:
        available = tuple(
            questionnaire
            for questionnaire in _QUESTIONNAIRES
            if _recipe_is_registered(recipe_registry, questionnaire[0])
        )
        if not available:
            raise ConfigurationInitializationError("No interactive recipe is available.")

        output_stream.write("Available project recipes:\n")
        for index, (recipe_id, label, _collector) in enumerate(available, start=1):
            output_stream.write(f"  {index}. {label} ({recipe_id})\n")
        selection = _ask_choice(
            "Recipe number or id: ",
            choices={str(index): item for index, item in enumerate(available, start=1)}
            | {item[0]: item for item in available},
            input_stream=input_stream,
            output_stream=output_stream,
        )
        recipe_id, _label, collect_answers = selection
        project_name = _ask_required(
            "Project name: ", input_stream=input_stream, output_stream=output_stream
        )
        target_directory = _ask_required(
            "Target directory: ", input_stream=input_stream, output_stream=output_stream
        )
        answers = collect_answers(input_stream, output_stream)
        raw_configuration: dict[str, object] = {
            "answers": answers,
            "project_name": project_name,
            "recipe": recipe_id,
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


def _fastapi_answers(input_stream: TextIO, output_stream: TextIO) -> dict[str, JSONValue]:
    package_name = _ask_optional(
        "Python package name [derived from project name]: ",
        input_stream=input_stream,
        output_stream=output_stream,
    )
    database = _ask_choice(
        "Database [none/postgres] (default none): ",
        choices={"": "none", "none": "none", "postgres": "postgres"},
        input_stream=input_stream,
        output_stream=output_stream,
    )
    delivery = _ask_choice(
        "Delivery [none/docker] (default none): ",
        choices={"": "none", "none": "none", "docker": "docker"},
        input_stream=input_stream,
        output_stream=output_stream,
    )
    answers: dict[str, JSONValue] = {"database": database, "delivery": delivery}
    if package_name:
        answers["package_name"] = package_name
    return answers


def _cmake_answers(input_stream: TextIO, output_stream: TextIO) -> dict[str, JSONValue]:
    target_name = _ask_optional(
        "CMake target name [derived from project name]: ",
        input_stream=input_stream,
        output_stream=output_stream,
    )
    strict_warnings = _ask_choice(
        "Strict compiler warnings [Y/n]: ",
        choices={"": True, "y": True, "yes": True, "n": False, "no": False},
        input_stream=input_stream,
        output_stream=output_stream,
    )
    answers: dict[str, JSONValue] = {"strict_warnings": strict_warnings}
    if target_name:
        answers["target_name"] = target_name
    return answers


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


def _recipe_is_registered(registry: ProjectRecipeRegistry, recipe_id: str) -> bool:
    try:
        registry.get(recipe_id)
    except ValueError:
        return False
    return True


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
