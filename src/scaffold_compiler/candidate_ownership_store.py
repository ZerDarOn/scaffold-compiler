"""Strict external ownership evidence for retryable candidate cleanup."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.path_safety import validate_unique_target_paths

_SCHEMA_VERSION: Final = 1
_MANIFEST_NAME: Final = "candidate_ownership.json"
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class CandidateOwnershipCorruptionError(ValueError):
    """Raised when persisted candidate ownership is incomplete or inconsistent."""


class CandidateOwnershipStore:
    """Atomically persist and strictly load one candidate ownership manifest."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, candidate: CandidateAssemblyResult) -> None:
        """Replace ownership evidence only for the fixed workspace candidate."""
        workspace = candidate.root.parent.resolve(strict=True)
        canonical_path = self.path.parent.resolve(strict=True) / self.path.name
        if canonical_path != workspace / _MANIFEST_NAME or self.path.is_symlink():
            raise ValueError("Candidate ownership manifest is not the fixed workspace file.")
        if (
            candidate.root.name != "candidate"
            or candidate.root.parent.resolve(strict=True) != workspace
        ):
            raise ValueError("Candidate is not the fixed workspace child.")
        payload = {
            "candidate_digest": candidate.digest,
            "files": [asdict(record) for record in candidate.files],
            "plan_digest": candidate.plan_digest,
            "schema_version": _SCHEMA_VERSION,
        }
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        temporary = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def load(self, workspace: Path) -> CandidateAssemblyResult:
        """Load ownership evidence bound to the fixed candidate child and its bytes."""
        return self._load(workspace, verify_complete_tree=True)

    def load_for_cleanup(self, workspace: Path) -> CandidateAssemblyResult:
        """Load strict ownership records while allowing an already-partial owned cleanup."""
        return self._load(workspace, verify_complete_tree=False)

    def _load(
        self,
        workspace: Path,
        *,
        verify_complete_tree: bool,
    ) -> CandidateAssemblyResult:
        try:
            trusted_workspace = workspace.resolve(strict=True)
            canonical_path = self.path.parent.resolve(strict=True) / self.path.name
            if canonical_path != trusted_workspace / _MANIFEST_NAME or self.path.is_symlink():
                raise ValueError("Manifest is not the fixed workspace file.")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "candidate_digest",
                "files",
                "plan_digest",
                "schema_version",
            }:
                raise ValueError("Candidate ownership fields are invalid.")
            if raw["schema_version"] != _SCHEMA_VERSION:
                raise ValueError("Candidate ownership schema is unsupported.")
            candidate_digest = _required_digest(raw, "candidate_digest")
            plan_digest = _required_digest(raw, "plan_digest")
            files = _parse_file_records(raw["files"])
            candidate = CandidateAssemblyResult(
                root=trusted_workspace / "candidate",
                files=files,
                digest=candidate_digest,
                plan_digest=plan_digest,
            )
            if (
                verify_complete_tree
                and calculate_candidate_digest(candidate.root, candidate.files) != candidate.digest
            ):
                raise ValueError("Candidate bytes do not match ownership evidence.")
            return candidate
        except (
            OSError,
            CandidateChangedError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise CandidateOwnershipCorruptionError(
                "Candidate ownership evidence is invalid."
            ) from error


def _parse_file_records(raw_files: object) -> tuple[CandidateFileRecord, ...]:
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Candidate ownership requires files.")
    records: list[CandidateFileRecord] = []
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != {"owner", "path", "sha256", "size"}:
            raise ValueError("Candidate file ownership fields are invalid.")
        path = raw["path"]
        owner = raw["owner"]
        size = raw["size"]
        sha256 = raw["sha256"]
        if not isinstance(path, str) or not isinstance(owner, str) or not owner:
            raise TypeError("Candidate file identity is invalid.")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise TypeError("Candidate file size is invalid.")
        if not isinstance(sha256, str) or not _DIGEST_PATTERN.fullmatch(sha256):
            raise TypeError("Candidate file digest is invalid.")
        records.append(CandidateFileRecord(path, owner, size, sha256))
    validate_unique_target_paths(record.path for record in records)
    return tuple(records)


def _required_digest(record: dict[str, object], field: str) -> str:
    value = record[field]
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise TypeError(f"{field} is invalid.")
    return value
