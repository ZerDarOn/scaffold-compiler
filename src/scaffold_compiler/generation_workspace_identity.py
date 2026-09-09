"""Fixed-length generation workspace identities and recovery bindings."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Final

from scaffold_compiler.path_safety import resolve_safe_output_path

CURRENT_WORKSPACE_SCHEME: Final = "short-v1"
_CURRENT_WORKSPACE_PREFIX: Final = ".scw-"
_CURRENT_WORKSPACE_DIGEST_HEX_LENGTH: Final = 32
_LEGACY_WORKSPACE_PREFIX_PATTERN: Final = re.compile(r"^\..+\.scaffold-")
_RUN_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def derive_generation_workspace(
    target: Path,
    *,
    run_id: str,
    configuration_digest: str,
) -> Path:
    """Return the canonical fixed-length sibling workspace for one run binding."""
    canonical_target = _canonical_target(target)
    _validate_binding_facts(run_id, configuration_digest)
    payload = json.dumps(
        {
            "configuration_digest": configuration_digest,
            "run_id": run_id,
            "scheme": CURRENT_WORKSPACE_SCHEME,
            "target_name": canonical_target.name,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    identity = hashlib.sha256(payload).hexdigest()[:_CURRENT_WORKSPACE_DIGEST_HEX_LENGTH]
    return resolve_safe_output_path(
        canonical_target.parent,
        f"{_CURRENT_WORKSPACE_PREFIX}{identity}",
    )


def resolve_bound_generation_target(
    workspace: Path,
    *,
    run_id: str,
    configuration_digest: str,
    target_name: str | None,
) -> Path:
    """Resolve only a target bound by the current hash or a valid legacy workspace name."""
    if not workspace.is_absolute():
        raise ValueError("Workspace path must be absolute.")
    _validate_binding_facts(run_id, configuration_digest)
    parent = workspace.parent.resolve(strict=True)
    canonical_workspace = resolve_safe_output_path(parent, workspace.name)
    if canonical_workspace != workspace.resolve(strict=False):
        raise ValueError("Workspace path spelling is not canonical.")

    if target_name is None:
        target_name = _legacy_target_name(canonical_workspace.name, run_id=run_id)
        validate_generation_target_name(target_name)
        return resolve_safe_output_path(parent, target_name)

    validate_generation_target_name(target_name)
    target = resolve_safe_output_path(parent, target_name)
    expected_workspace = derive_generation_workspace(
        target,
        run_id=run_id,
        configuration_digest=configuration_digest,
    )
    if canonical_workspace != expected_workspace:
        raise ValueError("Workspace name does not bind the session evidence.")
    return target


def validate_generation_target_name(target_name: str) -> None:
    """Reject target evidence that is not one canonical path leaf."""
    if (
        not isinstance(target_name, str)
        or not target_name
        or target_name in {".", ".."}
        or "/" in target_name
        or "\\" in target_name
        or "\0" in target_name
    ):
        raise ValueError("Target name must be a canonical path leaf.")


def _canonical_target(target: Path) -> Path:
    if not target.is_absolute():
        raise ValueError("Target path must be absolute.")
    parent = target.parent.resolve(strict=True)
    canonical_target = resolve_safe_output_path(parent, target.name)
    if canonical_target != target.resolve(strict=False):
        raise ValueError("Target path spelling is not canonical.")
    return canonical_target


def _validate_binding_facts(run_id: str, configuration_digest: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Generation run ID is invalid.")
    if not _DIGEST_PATTERN.fullmatch(configuration_digest):
        raise ValueError("Configuration digest is invalid.")


def _legacy_target_name(workspace_name: str, *, run_id: str) -> str:
    suffix = f".scaffold-{run_id}"
    if not _LEGACY_WORKSPACE_PREFIX_PATTERN.match(workspace_name) or not workspace_name.endswith(
        suffix
    ):
        raise ValueError("Legacy workspace name does not bind the session run ID.")
    target_name = workspace_name[1 : -len(suffix)]
    if not target_name:
        raise ValueError("Legacy workspace target name is invalid.")
    return target_name
