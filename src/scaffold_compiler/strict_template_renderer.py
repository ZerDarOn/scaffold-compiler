"""A deliberately small, non-executable template renderer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

_VARIABLE_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER_PATTERN: Final = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")
_EXECUTABLE_STYLE_DELIMITERS: Final = ("{{", "{%", "{#", "<%")


class TemplateRenderingError(ValueError):
    """Base class for a template that cannot be safely rendered."""


class UnsafeTemplateSyntaxError(TemplateRenderingError):
    """Raised when the template contains syntax outside simple placeholders."""


class UnknownTemplateVariableError(TemplateRenderingError):
    """Raised when a placeholder or supplied value was not declared."""


class MissingTemplateVariableError(TemplateRenderingError):
    """Raised when a declared placeholder has no supplied value."""


def render_strict_template(
    template: str,
    *,
    declared_variables: frozenset[str],
    values: Mapping[str, str],
) -> str:
    """Substitute only ``${name}`` tokens without evaluating expressions."""
    if any(not _VARIABLE_NAME_PATTERN.fullmatch(name) for name in declared_variables):
        raise UnsafeTemplateSyntaxError("Declared template variable name is invalid.")
    unknown_values = sorted(set(values) - declared_variables)
    if unknown_values:
        raise UnknownTemplateVariableError(
            f"Values were supplied for undeclared variables: {', '.join(unknown_values)}."
        )
    if any(not isinstance(value, str) for value in values.values()):
        raise UnsafeTemplateSyntaxError("Template variable values must be strings.")
    if any(delimiter in template for delimiter in _EXECUTABLE_STYLE_DELIMITERS):
        raise UnsafeTemplateSyntaxError("Executable-style template syntax is not allowed.")

    matches = tuple(_PLACEHOLDER_PATTERN.finditer(template))
    valid_placeholder_positions = {match.start() for match in matches}
    placeholder_open_positions = {
        index for index in range(len(template)) if template.startswith("${", index)
    }
    if placeholder_open_positions != valid_placeholder_positions:
        raise UnsafeTemplateSyntaxError("Only ${name} template placeholders are allowed.")

    placeholder_names = {match.group(1) for match in matches}
    unknown_placeholders = sorted(placeholder_names - declared_variables)
    if unknown_placeholders:
        raise UnknownTemplateVariableError(
            f"Template uses undeclared variables: {', '.join(unknown_placeholders)}."
        )
    missing_values = sorted(placeholder_names - set(values))
    if missing_values:
        raise MissingTemplateVariableError(
            f"Template values are missing: {', '.join(missing_values)}."
        )

    rendered = _PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)
    if "${" in rendered:
        raise UnsafeTemplateSyntaxError("Rendered values cannot introduce placeholders.")
    return rendered
