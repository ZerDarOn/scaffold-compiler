"""Cross-platform path validation for candidate project output."""

from __future__ import annotations

import re
import stat
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Final

_WINDOWS_DRIVE_PATTERN: Final = re.compile(r"^[A-Za-z]:($|/)")


class ProjectPathError(ValueError):
    """Base class for a rejected project-relative path."""


class AbsoluteProjectPathError(ProjectPathError):
    """Raised when a blueprint path is absolute on any supported platform."""


class PathTraversalError(ProjectPathError):
    """Raised when a blueprint path could escape its owned root."""


class LinkedProjectPathError(ProjectPathError):
    """Raised when an existing path component is a link or reparse point."""


class InvalidProjectPathError(ProjectPathError):
    """Raised when a relative path is empty or not canonically spelled."""


class DuplicateTargetPathError(ProjectPathError):
    """Raised when more than one planned output resolves to the same target."""


def resolve_safe_output_path(root: Path, relative_path: str) -> Path:
    """Resolve a canonical manifest path without following owned-path links."""
    parts = _relative_parts(relative_path)
    try:
        trusted_root = root.resolve(strict=True)
    except OSError as error:
        raise InvalidProjectPathError("Output root must already exist.") from error
    if not trusted_root.is_dir():
        raise InvalidProjectPathError("Output root must be a directory.")

    current = trusted_root
    _reject_link(current)
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_link(current)
    return current


def validate_unique_target_paths(relative_paths: Iterable[str]) -> tuple[str, ...]:
    """Validate and freeze a collision-free set of manifest target paths."""
    validated: list[str] = []
    seen: set[str] = set()
    for relative_path in relative_paths:
        canonical = "/".join(_relative_parts(relative_path))
        if canonical in seen:
            raise DuplicateTargetPathError(f"Duplicate output target: {canonical}.")
        seen.add(canonical)
        validated.append(canonical)
    return tuple(validated)


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path:
        raise InvalidProjectPathError("Project path must be a non-empty string.")
    normalized_for_checks = relative_path.replace("\\", "/")
    if (
        normalized_for_checks.startswith("/")
        or normalized_for_checks.startswith("//")
        or _WINDOWS_DRIVE_PATTERN.match(normalized_for_checks)
    ):
        raise AbsoluteProjectPathError("Absolute project paths are not allowed.")

    checked_parts = PurePosixPath(normalized_for_checks).parts
    if ".." in checked_parts:
        raise PathTraversalError("Parent traversal is not allowed in project paths.")
    if "\\" in relative_path:
        if normalized_for_checks.startswith("../"):
            raise PathTraversalError("Parent traversal is not allowed in project paths.")
        raise InvalidProjectPathError("Project paths must use forward slashes.")

    literal_parts = relative_path.split("/")
    if any(part in {"", "."} for part in literal_parts):
        raise InvalidProjectPathError("Project paths must use canonical relative spelling.")
    if any(":" in part or "\0" in part for part in literal_parts):
        raise InvalidProjectPathError("Project path contains a reserved character.")
    return tuple(literal_parts)


def _reject_link(path: Path) -> None:
    if path.is_symlink() or _is_windows_reparse_point(path):
        raise LinkedProjectPathError("Linked or reparse-point paths are not allowed.")


def _is_windows_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)
