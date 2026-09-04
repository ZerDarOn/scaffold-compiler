"""Executable release entry with success-gated external capsule cleanup."""

from __future__ import annotations

import io
import logging
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO, cast

from scaffold_compiler.capsule_package import (
    CapsuleCleanupSupervisor,
    CapsuleOwnershipError,
    VerifiedCapsule,
    start_capsule_cleanup_supervisor,
    verify_capsule_from_entry,
    write_capsule_cleanup_journal,
)
from scaffold_compiler.command_line_interface import ScaffoldCommandService, run_command_line
from scaffold_compiler.v1_command_application import V1CommandApplication

LOGGER = logging.getLogger(__name__)

_ACTIVE_CLEANUP_SUPERVISOR: CapsuleCleanupSupervisor | None = None


def main(
    arguments: Sequence[str] | None = None,
    *,
    entry_path: Path | None = None,
    application: ScaffoldCommandService | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run the CLI and arm self-cleanup only after a successful packaged run."""
    selected_arguments = list(sys.argv[1:] if arguments is None else arguments)
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    selected_environment = dict(os.environ if environment is None else environment)
    actual_entry = Path(sys.argv[0] if entry_path is None else entry_path).absolute()
    try:
        capsule = _load_capsule_if_packaged(actual_entry)
    except CapsuleOwnershipError:
        LOGGER.error("capsule_ownership_verification_failed")
        selected_stderr.write("Release capsule ownership verification failed.\n")
        return 1

    selected_application = application
    if any(argument in {"-h", "--help"} for argument in selected_arguments):
        return run_command_line(
            selected_arguments,
            service=cast(ScaffoldCommandService, object()),
            stdout=selected_stdout,
            stderr=selected_stderr,
        )
    if selected_application is None:
        requires_uv = selected_arguments[:1] == ["run"]
        uv_executable = resolve_uv_executable(selected_environment) if requires_uv else None
        docker_executable = (
            resolve_docker_executable(selected_environment) if requires_uv else None
        )
        if requires_uv and uv_executable is None:
            selected_stderr.write(
                "uv was not found; set SCAFFOLD_COMPILER_UV to an existing executable.\n"
            )
            return 1
        runtime_root = capsule.root if capsule is not None else Path(__file__).parents[2]
        try:
            selected_application = V1CommandApplication(
                catalog_root=runtime_root / "blueprints",
                working_directory=Path.cwd(),
                home_directory=Path.home(),
                uv_executable=uv_executable,
                docker_executable=docker_executable,
                environment=selected_environment,
            )
        except (OSError, ValueError):
            LOGGER.error("release_runtime_initialization_failed")
            selected_stderr.write("Release runtime could not be initialized.\n")
            return 1

    buffered_stdout = io.StringIO()
    exit_code = run_command_line(
        selected_arguments,
        service=selected_application,
        stdout=buffered_stdout,
        stderr=selected_stderr,
    )
    if exit_code != 0:
        return exit_code
    if capsule is not None and selected_arguments[:1] == ["run"]:
        try:
            _arm_capsule_cleanup(capsule)
        except (OSError, ValueError):
            LOGGER.error("capsule_cleanup_supervision_failed capsule_id=%s", capsule.capsule_id)
            selected_stderr.write(
                "Project completed, but generator self-cleanup could not be armed.\n"
            )
            return 1
    selected_stdout.write(buffered_stdout.getvalue())
    return 0


def resolve_uv_executable(environment: Mapping[str, str]) -> Path | None:
    """Resolve uv from an explicit release setting or the process PATH."""
    return _resolve_external_executable(environment, "SCAFFOLD_COMPILER_UV", "uv")


def resolve_docker_executable(environment: Mapping[str, str]) -> Path | None:
    """Resolve an optional Docker CLI without requiring its daemon to be available."""
    return _resolve_external_executable(environment, "SCAFFOLD_COMPILER_DOCKER", "docker")


def _resolve_external_executable(
    environment: Mapping[str, str],
    setting_name: str,
    command_name: str,
) -> Path | None:
    explicit = environment.get(setting_name)
    found = (
        shutil.which(command_name, path=environment.get("PATH")) if not explicit else None
    )
    candidate = Path(explicit) if explicit else Path(found) if found else None
    if candidate is None:
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if candidate.is_symlink() or not resolved.is_file():
        return None
    return resolved


def _load_capsule_if_packaged(entry_path: Path) -> VerifiedCapsule | None:
    if entry_path.name != "scaffold_compiler.pyz":
        return None
    if not (entry_path.parent / "capsule_manifest.json").is_file():
        return None
    return verify_capsule_from_entry(entry_path)


def _arm_capsule_cleanup(capsule: VerifiedCapsule) -> None:
    global _ACTIVE_CLEANUP_SUPERVISOR
    cleanup_root = capsule.root.parent / f".scaffold-capsule-cleanup-{capsule.capsule_id}"
    cleanup_root.mkdir()
    journal_path = cleanup_root / "capsule_cleanup_journal.json"
    try:
        write_capsule_cleanup_journal(capsule, journal_path)
        _ACTIVE_CLEANUP_SUPERVISOR = start_capsule_cleanup_supervisor(
            capsule,
            journal_path=journal_path,
            interpreter=Path(sys.executable),
            working_directory=capsule.root.parent,
        )
    except Exception:
        try:
            journal_path.unlink(missing_ok=True)
            cleanup_root.rmdir()
        except OSError:
            LOGGER.error("capsule_cleanup_coordination_cleanup_failed")
        raise
