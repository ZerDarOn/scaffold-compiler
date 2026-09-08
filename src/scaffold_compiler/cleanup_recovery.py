"""Safe retry for a published generation left in cleanup-pending state."""

from __future__ import annotations

import logging
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_ownership_store import (
    CandidateOwnershipCorruptionError,
    CandidateOwnershipStore,
)
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    verify_candidate_digest,
)
from scaffold_compiler.finalization import (
    cleanup_owned_candidate,
    verify_owned_candidate_cleanup_safety,
)
from scaffold_compiler.session_state_store import (
    GenerationSession,
    SessionEvent,
    SessionState,
    SessionStateStore,
    transition_session,
)
from scaffold_compiler.validation import ValidationStatus
from scaffold_compiler.validation_report_store import (
    ValidationReportCorruptionError,
    ValidationReportStore,
)
from scaffold_compiler.validation_workspace import (
    ValidationWorkspace,
    cleanup_validation_workspace,
    verify_validation_workspace_ownership,
)

LOGGER = logging.getLogger(__name__)

_WORKSPACE_PREFIX_PATTERN: Final = re.compile(r"^\..+\.scaffold-")
_ALLOWED_ENTRIES: Final = frozenset(
    {
        "candidate",
        "candidate_ownership.json",
        "session.json",
        "validation-env",
        "validation_report.json",
    }
)


@dataclass(frozen=True, slots=True)
class CleanupRecoveryResult:
    """Outcome of one bounded cleanup recovery attempt."""

    completed: bool
    reason: str = ""


def retry_cleanup_pending_workspace(
    workspace: Path,
    *,
    candidate_unlinker: Callable[[Path], None] | None = None,
    validation_unlinker: Callable[[Path], None] | None = None,
) -> CleanupRecoveryResult:
    """Finish cleanup only after proving the already-published target is unchanged."""
    try:
        trusted_workspace, session, target = _load_context(workspace)
        entries = tuple(trusted_workspace.iterdir())
    except (OSError, ValueError):
        return CleanupRecoveryResult(False, "Cleanup workspace evidence is invalid.")
    if session.state not in {SessionState.CLEANUP_PENDING, SessionState.DONE}:
        return _refuse(session, "Workspace state is not cleanup pending.")
    if any(path.name not in _ALLOWED_ENTRIES or _is_link_or_reparse(path) for path in entries):
        return _refuse(session, "Cleanup workspace contains an unowned or linked entry.")

    ownership_store = CandidateOwnershipStore(trusted_workspace / "candidate_ownership.json")
    candidate = _load_cleanup_candidate(ownership_store, trusted_workspace, session)
    if candidate is None:
        if session.state is not SessionState.DONE or {path.name for path in entries} != {
            "session.json"
        }:
            return _refuse(session, "Candidate ownership evidence is invalid.")
    elif not _published_target_matches(candidate, target):
        return _refuse(session, "Published target does not match cleanup evidence.")

    if session.state is SessionState.CLEANUP_PENDING:
        if candidate is None or not _report_is_bound(trusted_workspace, session):
            return _refuse(session, "Validation report does not match cleanup evidence.")
        if not verify_owned_candidate_cleanup_safety(candidate):
            return _refuse(session, "Candidate ownership preflight is invalid.")
        validation_workspace = ValidationWorkspace(
            workspace=trusted_workspace,
            root=trusted_workspace / "validation-env",
            run_id=session.run_id,
        )
        if not verify_validation_workspace_ownership(validation_workspace):
            return _refuse(session, "Validation workspace ownership is invalid.")
        validation_cleanup = cleanup_validation_workspace(
            validation_workspace,
            unlinker=validation_unlinker,
        )
        if not validation_cleanup.completed:
            return _refuse(session, "Validation workspace cleanup is incomplete.")
        candidate_cleanup = cleanup_owned_candidate(
            candidate,
            unlinker=candidate_unlinker,
        )
        if not candidate_cleanup.completed:
            return _refuse(session, "Candidate cleanup is incomplete.")
        try:
            ValidationReportStore(trusted_workspace / "validation_report.json").path.unlink(
                missing_ok=True
            )
            session = transition_session(session, SessionEvent.CLEANUP_SUCCEEDED)
            SessionStateStore(trusted_workspace / "session.json").save(session)
        except OSError:
            return _refuse(session, "Cleanup result could not be persisted.")

    try:
        ownership_store.path.unlink(missing_ok=True)
        (trusted_workspace / "session.json").unlink()
        trusted_workspace.rmdir()
    except OSError:
        return _refuse(session, "Cleanup metadata removal is incomplete.")
    LOGGER.info("cleanup_recovery_completed run_id=%s", session.run_id)
    return CleanupRecoveryResult(True)


def _load_context(workspace: Path) -> tuple[Path, GenerationSession, Path]:
    if not workspace.is_absolute() or _is_link_or_reparse(workspace):
        raise ValueError("Cleanup workspace path is invalid.")
    trusted_workspace = workspace.resolve(strict=True)
    if not trusted_workspace.is_dir():
        raise ValueError("Cleanup workspace must be a directory.")
    session_path = trusted_workspace / "session.json"
    if _is_link_or_reparse(session_path):
        raise ValueError("Cleanup session evidence is invalid.")
    session = SessionStateStore(session_path).load()
    suffix = f".scaffold-{session.run_id}"
    if not _WORKSPACE_PREFIX_PATTERN.match(
        trusted_workspace.name
    ) or not trusted_workspace.name.endswith(suffix):
        raise ValueError("Cleanup workspace name does not bind the run ID.")
    target_name = trusted_workspace.name[1 : -len(suffix)]
    if not target_name:
        raise ValueError("Cleanup target name is invalid.")
    return trusted_workspace, session, trusted_workspace.parent / target_name


def _load_cleanup_candidate(
    store: CandidateOwnershipStore,
    workspace: Path,
    session: GenerationSession,
) -> CandidateAssemblyResult | None:
    if not store.path.exists():
        return None
    if _is_link_or_reparse(store.path):
        return None
    try:
        candidate = store.load_for_cleanup(workspace)
    except CandidateOwnershipCorruptionError:
        return None
    if candidate.digest != session.candidate_digest or candidate.plan_digest != session.plan_digest:
        return None
    return candidate


def _published_target_matches(candidate: CandidateAssemblyResult, target: Path) -> bool:
    if _is_link_or_reparse(target) or not target.is_dir():
        return False
    published = CandidateAssemblyResult(
        root=target,
        files=candidate.files,
        digest=candidate.digest,
        plan_digest=candidate.plan_digest,
    )
    try:
        verify_candidate_digest(published)
    except (CandidateChangedError, OSError):
        return False
    return True


def _report_is_bound(workspace: Path, session: GenerationSession) -> bool:
    store = ValidationReportStore(workspace / "validation_report.json")
    if not store.path.exists():
        return True
    if _is_link_or_reparse(store.path):
        return False
    try:
        report = store.load(workspace)
    except ValidationReportCorruptionError:
        return False
    return (
        report.configuration_digest == session.configuration_digest
        and report.plan_digest == session.plan_digest
        and report.candidate_digest == session.candidate_digest
        and all(
            not check.required or check.status is ValidationStatus.PASS for check in report.checks
        )
    )


def _refuse(session: GenerationSession, reason: str) -> CleanupRecoveryResult:
    LOGGER.warning(
        "cleanup_recovery_refused run_id=%s state=%s reason=%s",
        session.run_id,
        session.state.value,
        reason,
    )
    return CleanupRecoveryResult(False, reason)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)
