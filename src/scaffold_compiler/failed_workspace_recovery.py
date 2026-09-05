"""Read-only inspection and exact discard of failed generation workspaces."""

from __future__ import annotations

import logging
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_ownership_store import (
    CandidateOwnershipCorruptionError,
    CandidateOwnershipStore,
)
from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.finalization import cleanup_owned_candidate
from scaffold_compiler.session_state_store import (
    FailureStage,
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
_ALLOWED_ROOT_ENTRIES: Final = frozenset(
    {
        "candidate",
        "candidate_ownership.json",
        "session.json",
        "validation-env",
        "validation_report.json",
    }
)
_DISCARDABLE_STATES: Final = frozenset({SessionState.FAILED_RETRYABLE, SessionState.CANCELLED})


@dataclass(frozen=True, slots=True)
class FailedWorkspaceInspection:
    """Safe user-facing facts recovered from bound external evidence."""

    run_id: str
    state: SessionState
    failed_stage: FailureStage | None
    candidate_digest: str | None
    failed_gates: tuple[str, ...]
    evidence_valid: bool


@dataclass(frozen=True, slots=True)
class FailedWorkspaceDiscardResult:
    """Outcome of an exact discard attempt."""

    completed: bool
    reason: str = ""


def inspect_failed_workspace(workspace: Path) -> FailedWorkspaceInspection:
    """Read fixed evidence without following linked workspace entries."""
    trusted_workspace, session = _load_workspace_session(workspace)
    candidate, candidate_valid = _load_bound_candidate(trusted_workspace, session)
    failed_gates, report_valid = _load_bound_failed_gates(trusted_workspace, session)
    evidence_valid = candidate_valid and report_valid
    LOGGER.info(
        "failed_workspace_inspected run_id=%s state=%s evidence_valid=%s failed_gates=%d",
        session.run_id,
        session.state.value,
        evidence_valid,
        len(failed_gates),
    )
    return FailedWorkspaceInspection(
        run_id=session.run_id,
        state=session.state,
        failed_stage=session.failed_stage,
        candidate_digest=candidate.digest if candidate is not None else session.candidate_digest,
        failed_gates=failed_gates,
        evidence_valid=evidence_valid,
    )


def discard_failed_workspace(workspace: Path) -> FailedWorkspaceDiscardResult:
    """Discard only a completely owned failed workspace, preserving all ambiguity."""
    try:
        trusted_workspace, session = _load_workspace_session(workspace)
    except (OSError, ValueError):
        return FailedWorkspaceDiscardResult(False, "Workspace evidence is invalid.")
    LOGGER.info(
        "failed_workspace_discard_started run_id=%s state=%s",
        session.run_id,
        session.state.value,
    )
    if session.state not in _DISCARDABLE_STATES:
        return _refuse(session, "Workspace state cannot be discarded.")
    try:
        entries = tuple(trusted_workspace.iterdir())
    except OSError:
        return _refuse(session, "Workspace entries could not be inspected.")
    if any(path.name not in _ALLOWED_ROOT_ENTRIES or _is_link_or_reparse(path) for path in entries):
        return _refuse(session, "Workspace contains an unowned or linked entry.")

    candidate, candidate_valid = _load_bound_candidate(trusted_workspace, session)
    _failed_gates, report_valid = _load_bound_failed_gates(trusted_workspace, session)
    validation_workspace = ValidationWorkspace(
        workspace=trusted_workspace,
        root=trusted_workspace / "validation-env",
        run_id=session.run_id,
    )
    if (
        not candidate_valid
        or not report_valid
        or not verify_validation_workspace_ownership(validation_workspace)
    ):
        return _refuse(session, "Workspace ownership evidence does not match disk contents.")

    if session.state is not SessionState.CANCELLED:
        try:
            session = transition_session(session, SessionEvent.CANCEL)
            SessionStateStore(trusted_workspace / "session.json").save(session)
        except OSError:
            return _refuse(session, "Cancellation intent could not be persisted.")
    if candidate is not None:
        candidate_cleanup = cleanup_owned_candidate(candidate)
        if not candidate_cleanup.completed:
            return _refuse(session, "Candidate cleanup is incomplete.")
    validation_cleanup = cleanup_validation_workspace(validation_workspace)
    if not validation_cleanup.completed:
        return _refuse(session, "Validation workspace cleanup is incomplete.")

    ownership_store = CandidateOwnershipStore(trusted_workspace / "candidate_ownership.json")
    report_store = ValidationReportStore(trusted_workspace / "validation_report.json")
    try:
        report_store.path.unlink(missing_ok=True)
        ownership_store.path.unlink(missing_ok=True)
        (trusted_workspace / "session.json").unlink()
        trusted_workspace.rmdir()
    except OSError:
        return _refuse(session, "Workspace metadata cleanup is incomplete.")
    LOGGER.info("failed_workspace_discard_completed run_id=%s", session.run_id)
    return FailedWorkspaceDiscardResult(True)


def _load_workspace_session(workspace: Path) -> tuple[Path, GenerationSession]:
    if not workspace.is_absolute():
        raise ValueError("Workspace path must be absolute.")
    trusted_workspace = workspace.resolve(strict=True)
    if trusted_workspace != workspace or not trusted_workspace.is_dir():
        raise ValueError("Workspace path is not canonical.")
    if _is_link_or_reparse(trusted_workspace):
        raise ValueError("Workspace cannot be linked or reparsed.")
    session_path = trusted_workspace / "session.json"
    if _is_link_or_reparse(session_path):
        raise ValueError("Session evidence cannot be linked or reparsed.")
    session = SessionStateStore(session_path).load()
    expected_suffix = f".scaffold-{session.run_id}"
    if not _WORKSPACE_PREFIX_PATTERN.match(
        trusted_workspace.name
    ) or not trusted_workspace.name.endswith(expected_suffix):
        raise ValueError("Workspace name does not bind the session run ID.")
    return trusted_workspace, session


def _load_bound_candidate(
    workspace: Path,
    session: GenerationSession,
) -> tuple[CandidateAssemblyResult | None, bool]:
    store = CandidateOwnershipStore(workspace / "candidate_ownership.json")
    candidate_exists = (workspace / "candidate").exists()
    if not candidate_exists:
        if session.state is SessionState.CANCELLED:
            return None, True
        if not store.path.exists():
            return None, session.candidate_digest is None
    if _is_link_or_reparse(store.path):
        return None, False
    try:
        candidate = store.load(workspace)
    except CandidateOwnershipCorruptionError:
        return None, False
    return candidate, (
        session.candidate_digest == candidate.digest
        and session.plan_digest == candidate.plan_digest
    )


def _load_bound_failed_gates(
    workspace: Path,
    session: GenerationSession,
) -> tuple[tuple[str, ...], bool]:
    store = ValidationReportStore(workspace / "validation_report.json")
    if not store.path.exists():
        return (), session.failed_stage is not FailureStage.VERIFY
    if _is_link_or_reparse(store.path):
        return (), False
    try:
        report = store.load(workspace)
    except ValidationReportCorruptionError:
        return (), False
    bound = (
        report.configuration_digest == session.configuration_digest
        and report.plan_digest == session.plan_digest
        and report.candidate_digest == session.candidate_digest
    )
    failed = tuple(
        check.name
        for check in report.checks
        if check.required and check.status is not ValidationStatus.PASS
    )
    return failed, bound


def _refuse(session: GenerationSession, reason: str) -> FailedWorkspaceDiscardResult:
    LOGGER.warning(
        "failed_workspace_discard_refused run_id=%s state=%s reason=%s",
        session.run_id,
        session.state.value,
        reason,
    )
    return FailedWorkspaceDiscardResult(False, reason)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)
