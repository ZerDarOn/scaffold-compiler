"""Strict public input metadata for trusted built-in recipe questionnaires."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn

_INPUT_KEY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class RecipeInputType(StrEnum):
    """Input shapes supported by the generic trusted questionnaire."""

    STRING = "string"
    BOOLEAN = "boolean"
    CHOICE = "choice"


class RecipeInputOmission(StrEnum):
    """How interactive collection handles an empty answer."""

    REJECT = "reject"
    OMIT = "omit"
    LITERAL = "literal"


class RecipeInputDescriptorError(ValueError):
    """A stable error raised for an invalid trusted input descriptor."""

    def __init__(self, *, code: str) -> None:
        self.code = code
        super().__init__("Recipe input descriptor is invalid.")


@dataclass(frozen=True, slots=True)
class RecipeInputDescriptor:
    """One immutable, non-executable recipe answer description."""

    key: str
    label: str
    input_type: RecipeInputType
    omission: RecipeInputOmission
    choices: tuple[str, ...] = ()
    literal_default: bool | str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not _INPUT_KEY_PATTERN.fullmatch(self.key):
            _raise_descriptor_error("invalid_input_key")
        if (
            not isinstance(self.label, str)
            or not self.label
            or self.label != self.label.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in self.label)
        ):
            _raise_descriptor_error("invalid_input_label")
        if not isinstance(self.input_type, RecipeInputType):
            _raise_descriptor_error("invalid_input_type")
        if not isinstance(self.omission, RecipeInputOmission):
            _raise_descriptor_error("invalid_input_omission")
        self._validate_choices()
        self._validate_default()

    @property
    def required(self) -> bool:
        """Return whether an empty interactive answer must be rejected."""
        return self.omission is RecipeInputOmission.REJECT

    def to_public_data(self) -> dict[str, object]:
        """Return deterministic JSON-compatible metadata without executable behavior."""
        return {
            "choices": list(self.choices),
            "interactive_default": (
                self.literal_default if self.omission is RecipeInputOmission.LITERAL else None
            ),
            "key": self.key,
            "label": self.label,
            "omission": self.omission.value,
            "required": self.required,
            "type": self.input_type.value,
        }

    def _validate_choices(self) -> None:
        if self.input_type is not RecipeInputType.CHOICE:
            if self.choices:
                _raise_descriptor_error("unexpected_input_choices")
            return
        if (
            not isinstance(self.choices, tuple)
            or not self.choices
            or any(
                not isinstance(choice, str)
                or not choice
                or choice != choice.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in choice)
                for choice in self.choices
            )
        ):
            _raise_descriptor_error("invalid_input_choices")
        if len(set(self.choices)) != len(self.choices):
            _raise_descriptor_error("duplicate_input_choices")

    def _validate_default(self) -> None:
        if self.omission is not RecipeInputOmission.LITERAL:
            if self.literal_default is not None:
                _raise_descriptor_error("unexpected_input_default")
            return
        if self.literal_default is None:
            _raise_descriptor_error("missing_input_default")
        if self.input_type is RecipeInputType.BOOLEAN:
            if not isinstance(self.literal_default, bool):
                _raise_descriptor_error("input_default_type_mismatch")
            return
        if not isinstance(self.literal_default, str) or not self.literal_default:
            _raise_descriptor_error("input_default_type_mismatch")
        if self.input_type is RecipeInputType.CHOICE and self.literal_default not in self.choices:
            _raise_descriptor_error("input_default_not_allowed")


def _raise_descriptor_error(code: str) -> NoReturn:
    raise RecipeInputDescriptorError(code=code)
