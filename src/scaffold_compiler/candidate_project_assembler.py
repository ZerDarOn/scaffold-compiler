"""Materialize a frozen blueprint plan into an isolated candidate directory."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scaffold_compiler.blueprint_catalog import (
    BlueprintCatalog,
    BlueprintFileKind,
    BlueprintManifest,
)
from scaffold_compiler.blueprint_plan_compiler import GenerationPlan
from scaffold_compiler.path_safety import (
    LinkedProjectPathError,
    resolve_safe_output_path,
)
from scaffold_compiler.strict_template_renderer import render_strict_template

LOGGER = logging.getLogger(__name__)


class CandidateAssemblyError(RuntimeError):
    """Raised when an isolated candidate cannot be completely assembled."""


class CandidateAlreadyExistsError(CandidateAssemblyError):
    """Raised rather than reusing or overwriting an earlier candidate."""


class CandidateChangedError(CandidateAssemblyError):
    """Raised when candidate disk facts no longer match the frozen manifest."""


@dataclass(frozen=True, slots=True)
class CandidateFileRecord:
    """The frozen owner and content facts for one generated file."""

    path: str
    owner: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CandidateAssemblyResult:
    """A complete candidate and its deterministic verification input."""

    root: Path
    files: tuple[CandidateFileRecord, ...]
    digest: str
    plan_digest: str


@dataclass(frozen=True, slots=True)
class GeneratedCandidateFile:
    """One deterministic in-memory file produced by the final assembly stage."""

    path: str
    owner: str
    content: bytes


def assemble_candidate_project(
    catalog: BlueprintCatalog,
    plan: GenerationPlan,
    workspace: Path,
    *,
    values: Mapping[str, str],
    generated_files: tuple[GeneratedCandidateFile, ...] = (),
) -> CandidateAssemblyResult:
    """Create ``workspace/candidate`` exactly once from a frozen plan."""
    if not workspace.is_dir():
        raise CandidateAssemblyError("Workspace must already exist.")
    candidate = workspace / "candidate"
    LOGGER.info("candidate_assembly_started plan_digest=%s", plan.digest)
    try:
        candidate.mkdir()
    except FileExistsError as error:
        raise CandidateAlreadyExistsError("Candidate path already exists.") from error
    except OSError as error:
        raise CandidateAssemblyError("Candidate directory could not be created.") from error

    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        manifests = _selected_manifests(catalog, plan)
        declared_variables = frozenset(
            variable for manifest in manifests for variable in manifest.variables
        )
        records: list[CandidateFileRecord] = []
        rendered_targets: set[str] = set()
        for manifest in manifests:
            for blueprint_file in manifest.files:
                source = resolve_safe_output_path(manifest.directory, blueprint_file.source)
                if not source.is_file():
                    raise CandidateAssemblyError(
                        f"Blueprint source is not a regular file: {blueprint_file.source}."
                    )
                rendered_target = render_strict_template(
                    blueprint_file.target,
                    declared_variables=declared_variables,
                    values=values,
                )
                if rendered_target in rendered_targets:
                    raise CandidateAssemblyError(
                        f"Rendered output target is duplicated: {rendered_target}."
                    )
                rendered_targets.add(rendered_target)
                target = resolve_safe_output_path(candidate, rendered_target)
                _create_missing_parents(target.parent, candidate, created_directories)
                if blueprint_file.kind is BlueprintFileKind.TEMPLATE:
                    try:
                        template = source.read_text(encoding="utf-8")
                    except (OSError, UnicodeError) as error:
                        raise CandidateAssemblyError(
                            f"Template could not be read: {blueprint_file.source}."
                        ) from error
                    content = render_strict_template(
                        template,
                        declared_variables=declared_variables,
                        values=values,
                    ).encode()
                else:
                    try:
                        content = source.read_bytes()
                    except OSError as error:
                        raise CandidateAssemblyError(
                            f"Static file could not be read: {blueprint_file.source}."
                        ) from error
                created_files.append(target)
                _write_new_file(target, content)
                records.append(
                    CandidateFileRecord(
                        path=rendered_target,
                        owner=manifest.blueprint_id,
                        size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
                )

        for generated_file in sorted(generated_files, key=lambda item: item.path):
            if generated_file.path in rendered_targets:
                raise CandidateAssemblyError(
                    f"Generated output target is duplicated: {generated_file.path}."
                )
            rendered_targets.add(generated_file.path)
            target = resolve_safe_output_path(candidate, generated_file.path)
            _create_missing_parents(target.parent, candidate, created_directories)
            created_files.append(target)
            _write_new_file(target, generated_file.content)
            records.append(
                CandidateFileRecord(
                    path=generated_file.path,
                    owner=generated_file.owner,
                    size=len(generated_file.content),
                    sha256=hashlib.sha256(generated_file.content).hexdigest(),
                )
            )

        frozen_records = tuple(sorted(records, key=lambda item: item.path))
        digest = calculate_candidate_digest(candidate, frozen_records)
    except Exception as error:
        LOGGER.error("candidate_assembly_failed plan_digest=%s", plan.digest)
        _remove_incomplete_candidate(candidate, created_files, created_directories)
        if isinstance(error, CandidateAssemblyError):
            raise
        raise CandidateAssemblyError("Candidate assembly failed.") from error

    LOGGER.info(
        "candidate_assembly_completed plan_digest=%s files=%d",
        plan.digest,
        len(frozen_records),
    )
    return CandidateAssemblyResult(
        root=candidate,
        files=frozen_records,
        digest=digest,
        plan_digest=plan.digest,
    )


def calculate_candidate_digest(
    candidate_root: Path,
    expected_files: tuple[CandidateFileRecord, ...],
) -> str:
    """Recompute a candidate tree digest and reject additions or links."""
    expected_by_path = {record.path: record for record in expected_files}
    actual_paths: set[str] = set()
    actual_records: list[dict[str, object]] = []
    try:
        for path in candidate_root.rglob("*"):
            if path.is_symlink():
                raise LinkedProjectPathError("Candidate tree contains a link.")
            if not path.is_file():
                continue
            relative_path = path.relative_to(candidate_root).as_posix()
            actual_paths.add(relative_path)
            owner_record = expected_by_path.get(relative_path)
            if owner_record is None:
                raise CandidateChangedError(
                    f"Candidate contains an unexpected file: {relative_path}."
                )
            content = path.read_bytes()
            content_digest = hashlib.sha256(content).hexdigest()
            if len(content) != owner_record.size or not hmac.compare_digest(
                content_digest,
                owner_record.sha256,
            ):
                raise CandidateChangedError(
                    f"Candidate file metadata no longer matches: {relative_path}."
                )
            actual_records.append(
                {
                    "path": relative_path,
                    "owner": owner_record.owner,
                    "size": len(content),
                    "sha256": content_digest,
                }
            )
    except OSError as error:
        raise CandidateChangedError("Candidate tree could not be read completely.") from error

    missing = sorted(set(expected_by_path) - actual_paths)
    if missing:
        raise CandidateChangedError(f"Candidate files are missing: {', '.join(missing)}.")
    serialized = json.dumps(
        sorted(actual_records, key=lambda record: str(record["path"])),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def verify_candidate_digest(result: CandidateAssemblyResult) -> None:
    """Require current candidate bytes to match their frozen assembly digest."""
    current_digest = calculate_candidate_digest(result.root, result.files)
    if not hmac.compare_digest(current_digest, result.digest):
        raise CandidateChangedError("Candidate digest no longer matches assembly.")


def _selected_manifests(
    catalog: BlueprintCatalog,
    plan: GenerationPlan,
) -> tuple[BlueprintManifest, ...]:
    try:
        manifests = tuple(catalog.by_id(blueprint_id) for blueprint_id in plan.blueprint_ids)
    except KeyError as error:
        raise CandidateAssemblyError("Plan references an unknown blueprint.") from error
    declared_owners = tuple(
        sorted(
            (blueprint_file.target, manifest.blueprint_id)
            for manifest in manifests
            for blueprint_file in manifest.files
        )
    )
    if declared_owners != plan.file_owners:
        raise CandidateAssemblyError("Plan file ownership no longer matches the catalog.")
    return manifests


def _create_missing_parents(
    parent: Path,
    candidate: Path,
    created_directories: list[Path],
) -> None:
    missing: list[Path] = []
    current = parent
    while current != candidate and not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        created_directories.append(directory)


def _write_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as output_file:
        output_file.write(content)
        output_file.flush()
        os.fsync(output_file.fileno())


def _remove_incomplete_candidate(
    candidate: Path,
    created_files: list[Path],
    created_directories: list[Path],
) -> None:
    cleanup_failed = False
    for path in reversed(created_files):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    for path in reversed(created_directories):
        try:
            path.rmdir()
        except OSError:
            cleanup_failed = True
    try:
        candidate.rmdir()
    except OSError:
        cleanup_failed = True
    if cleanup_failed:
        LOGGER.error("candidate_cleanup_incomplete candidate=%s", candidate.name)
