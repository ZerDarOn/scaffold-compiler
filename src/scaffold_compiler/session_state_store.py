"""Generation session state transitions and atomic JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final

LOGGER = logging.getLogger(__name__)

SESSION_SCHEMA_VERSION: Final = 1
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class SessionState(StrEnum):
    """Persisted lifecycle states for one generation run."""

    NEW = "new"
    PLANNED = "planned"
    MATERIALIZING = "materializing"
    MATERIALIZED = "materialized"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMMITTING = "committing"
    COMMITTED = "committed"
    CLEANUP_PENDING = "cleanup_pending"
    DONE = "done"
    FAILED_RETRYABLE = "failed_retryable"
    AMBIGUOUS = "ambiguous"
    CANCELLED = "cancelled"


class FailureStage(StrEnum):
    """Stages with a fixed safe state for retry."""

    PLAN = "plan"
    MATERIALIZE = "materialize"
    VERIFY = "verify"
    FINALIZE_PRECHECK = "finalize_precheck"


class SessionEvent(StrEnum):
    """Events accepted by the pure session state machine."""

    PLAN_SUCCEEDED = "plan_succeeded"
    PLAN_FAILED = "plan_failed"
    MATERIALIZE_REQUESTED = "materialize_requested"
    MATERIALIZE_SUCCEEDED = "materialize_succeeded"
    MATERIALIZE_FAILED = "materialize_failed"
    VERIFY_REQUESTED = "verify_requested"
    VERIFY_SUCCEEDED = "verify_succeeded"
    VERIFY_FAILED = "verify_failed"
    RETRY = "retry"
    CANDIDATE_CHANGED = "candidate_changed"
    RECONFIGURE = "reconfigure"
    FINALIZE_REQUESTED = "finalize_requested"
    FINALIZE_CONFIRMED = "finalize_confirmed"
    FINALIZE_PRECHECK_FAILED = "finalize_precheck_failed"
    PUBLISH_OBSERVED = "publish_observed"
    RECOVER_BEFORE_PUBLISH = "recover_before_publish"
    RECOVER_PUBLISHED = "recover_published"
    RECOVER_AMBIGUOUS = "recover_ambiguous"
    CLEANUP_STARTED = "cleanup_started"
    CLEANUP_SUCCEEDED = "cleanup_succeeded"
    CLEANUP_FAILED = "cleanup_failed"
    CANCEL = "cancel"
    STALE_LOCK_RECOVERED = "stale_lock_recovered"
    STALE_LOCK_AMBIGUOUS = "stale_lock_ambiguous"


class InvalidSessionTransition(ValueError):
    """Raised when an event violates the approved lifecycle contract."""


class SessionStateCorruptionError(ValueError):
    """Raised when persisted session state is incomplete or unsupported."""


@dataclass(frozen=True, slots=True)
class GenerationSession:
    """Immutable record of one generation session."""

    run_id: str
    state: SessionState
    configuration_digest: str
    plan_digest: str | None = None
    candidate_digest: str | None = None
    verification_digest: str | None = None
    failed_stage: FailureStage | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.run_id or len(self.run_id) > 128:
            raise ValueError("Run ID must contain between 1 and 128 characters.")
        _validate_digest(self.configuration_digest, field="configuration_digest")
        for field, digest in (
            ("plan_digest", self.plan_digest),
            ("candidate_digest", self.candidate_digest),
            ("verification_digest", self.verification_digest),
        ):
            if digest is not None:
                _validate_digest(digest, field=field)
        if self.revision < 0:
            raise ValueError("Revision cannot be negative.")
        if (self.state is SessionState.FAILED_RETRYABLE) != (self.failed_stage is not None):
            raise ValueError("Failure stage must exist only for failed retryable sessions.")

    @classmethod
    def new(cls, *, run_id: str, configuration_digest: str) -> GenerationSession:
        """Create the initial immutable session record."""
        return cls(
            run_id=run_id,
            state=SessionState.NEW,
            configuration_digest=configuration_digest,
        )


_SIMPLE_TRANSITIONS: Final = {
    (SessionState.PLANNED, SessionEvent.MATERIALIZE_REQUESTED): SessionState.MATERIALIZING,
    (SessionState.MATERIALIZED, SessionEvent.VERIFY_REQUESTED): SessionState.VERIFYING,
    (SessionState.VERIFIED, SessionEvent.FINALIZE_REQUESTED): SessionState.AWAITING_CONFIRMATION,
    (
        SessionState.AWAITING_CONFIRMATION,
        SessionEvent.FINALIZE_CONFIRMED,
    ): SessionState.COMMITTING,
    (SessionState.COMMITTING, SessionEvent.PUBLISH_OBSERVED): SessionState.COMMITTED,
    (
        SessionState.COMMITTING,
        SessionEvent.RECOVER_BEFORE_PUBLISH,
    ): SessionState.AWAITING_CONFIRMATION,
    (SessionState.COMMITTING, SessionEvent.RECOVER_PUBLISHED): SessionState.COMMITTED,
    (SessionState.COMMITTING, SessionEvent.RECOVER_AMBIGUOUS): SessionState.AMBIGUOUS,
    (SessionState.COMMITTED, SessionEvent.CLEANUP_STARTED): SessionState.CLEANUP_PENDING,
    (SessionState.CLEANUP_PENDING, SessionEvent.CLEANUP_SUCCEEDED): SessionState.DONE,
    (SessionState.CLEANUP_PENDING, SessionEvent.CLEANUP_FAILED): SessionState.CLEANUP_PENDING,
}

_FAILURE_TRANSITIONS: Final = {
    SessionEvent.PLAN_FAILED: (SessionState.NEW, FailureStage.PLAN),
    SessionEvent.MATERIALIZE_FAILED: (
        SessionState.MATERIALIZING,
        FailureStage.MATERIALIZE,
    ),
    SessionEvent.VERIFY_FAILED: (SessionState.VERIFYING, FailureStage.VERIFY),
    SessionEvent.FINALIZE_PRECHECK_FAILED: (
        SessionState.AWAITING_CONFIRMATION,
        FailureStage.FINALIZE_PRECHECK,
    ),
}

_RETRY_STATES: Final = {
    FailureStage.PLAN: SessionState.NEW,
    FailureStage.MATERIALIZE: SessionState.PLANNED,
    FailureStage.VERIFY: SessionState.MATERIALIZED,
    FailureStage.FINALIZE_PRECHECK: SessionState.VERIFIED,
}

_RECONFIGURABLE_STATES: Final = frozenset(
    {
        SessionState.NEW,
        SessionState.PLANNED,
        SessionState.MATERIALIZED,
        SessionState.VERIFIED,
        SessionState.FAILED_RETRYABLE,
    }
)

_CANCELLABLE_STATES: Final = frozenset(
    {
        SessionState.NEW,
        SessionState.PLANNED,
        SessionState.MATERIALIZING,
        SessionState.MATERIALIZED,
        SessionState.VERIFYING,
        SessionState.VERIFIED,
        SessionState.AWAITING_CONFIRMATION,
        SessionState.FAILED_RETRYABLE,
    }
)

_LOCK_RECOVERY_STATES: Final = frozenset(
    state
    for state in SessionState
    if state not in {SessionState.AMBIGUOUS, SessionState.DONE, SessionState.CANCELLED}
)


def transition_session(
    session: GenerationSession,
    event: SessionEvent,
    *,
    configuration_digest: str | None = None,
    plan_digest: str | None = None,
    candidate_digest: str | None = None,
    verification_digest: str | None = None,
) -> GenerationSession:
    """Apply one event without performing its associated side effects."""
    updated = _apply_transition(
        session,
        event,
        configuration_digest=configuration_digest,
        plan_digest=plan_digest,
        candidate_digest=candidate_digest,
        verification_digest=verification_digest,
    )
    LOGGER.info(
        "session_transition run_id=%s from=%s event=%s to=%s revision=%d",
        session.run_id,
        session.state.value,
        event.value,
        updated.state.value,
        updated.revision,
    )
    return updated


def _apply_transition(
    session: GenerationSession,
    event: SessionEvent,
    *,
    configuration_digest: str | None,
    plan_digest: str | None,
    candidate_digest: str | None,
    verification_digest: str | None,
) -> GenerationSession:
    if event is SessionEvent.PLAN_SUCCEEDED:
        _require_state(session, event, SessionState.NEW)
        _reject_digest_arguments(
            event,
            configuration_digest,
            candidate_digest,
            verification_digest,
        )
        return _updated_session(session, state=SessionState.PLANNED, plan_digest=plan_digest)

    if event is SessionEvent.MATERIALIZE_SUCCEEDED:
        _require_state(session, event, SessionState.MATERIALIZING)
        _reject_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            verification_digest,
        )
        return _updated_session(
            session,
            state=SessionState.MATERIALIZED,
            candidate_digest=candidate_digest,
        )

    if event is SessionEvent.VERIFY_SUCCEEDED:
        _require_state(session, event, SessionState.VERIFYING)
        _reject_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
        )
        return _updated_session(
            session,
            state=SessionState.VERIFIED,
            verification_digest=verification_digest,
        )

    if event in _FAILURE_TRANSITIONS:
        required_state, failed_stage = _FAILURE_TRANSITIONS[event]
        _require_state(session, event, required_state)
        _reject_all_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
            verification_digest,
        )
        return replace(
            session,
            state=SessionState.FAILED_RETRYABLE,
            failed_stage=failed_stage,
            revision=session.revision + 1,
        )

    if event is SessionEvent.RETRY:
        _require_state(session, event, SessionState.FAILED_RETRYABLE)
        _reject_all_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
            verification_digest,
        )
        assert session.failed_stage is not None
        return replace(
            session,
            state=_RETRY_STATES[session.failed_stage],
            failed_stage=None,
            revision=session.revision + 1,
        )

    if event is SessionEvent.CANDIDATE_CHANGED:
        _require_state(
            session,
            event,
            SessionState.MATERIALIZED,
            SessionState.VERIFIED,
            SessionState.AWAITING_CONFIRMATION,
        )
        _reject_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            verification_digest,
        )
        return _updated_session(
            session,
            state=SessionState.MATERIALIZED,
            candidate_digest=candidate_digest,
            verification_digest=None,
        )

    if event is SessionEvent.RECONFIGURE:
        _require_state(session, event, *_RECONFIGURABLE_STATES)
        if configuration_digest is None:
            raise InvalidSessionTransition("Reconfigure requires a configuration digest.")
        _reject_digest_arguments(event, plan_digest, candidate_digest, verification_digest)
        return GenerationSession(
            run_id=session.run_id,
            state=SessionState.NEW,
            configuration_digest=configuration_digest,
            revision=session.revision + 1,
        )

    if event is SessionEvent.CANCEL:
        _require_state(session, event, *_CANCELLABLE_STATES)
        _reject_all_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
            verification_digest,
        )
        return replace(
            session,
            state=SessionState.CANCELLED,
            failed_stage=None,
            revision=session.revision + 1,
        )

    if event is SessionEvent.STALE_LOCK_RECOVERED:
        _require_state(session, event, *_LOCK_RECOVERY_STATES)
        _reject_all_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
            verification_digest,
        )
        return replace(session, revision=session.revision + 1)

    if event is SessionEvent.STALE_LOCK_AMBIGUOUS:
        _require_state(session, event, *_LOCK_RECOVERY_STATES)
        _reject_all_digest_arguments(
            event,
            configuration_digest,
            plan_digest,
            candidate_digest,
            verification_digest,
        )
        return replace(
            session,
            state=SessionState.AMBIGUOUS,
            failed_stage=None,
            revision=session.revision + 1,
        )

    next_state = _SIMPLE_TRANSITIONS.get((session.state, event))
    if next_state is None:
        raise InvalidSessionTransition(
            f"Event {event.value} is not valid from state {session.state.value}."
        )
    _reject_all_digest_arguments(
        event,
        configuration_digest,
        plan_digest,
        candidate_digest,
        verification_digest,
    )
    return replace(
        session,
        state=next_state,
        failed_stage=None,
        revision=session.revision + 1,
    )


def _updated_session(
    session: GenerationSession,
    *,
    state: SessionState,
    plan_digest: str | None = None,
    candidate_digest: str | None = None,
    verification_digest: str | None = None,
) -> GenerationSession:
    if state is SessionState.PLANNED and plan_digest is None:
        raise InvalidSessionTransition("Plan success requires a plan digest.")
    if state is SessionState.MATERIALIZED and candidate_digest is None:
        raise InvalidSessionTransition("Materialization success requires a candidate digest.")
    if state is SessionState.VERIFIED and verification_digest is None:
        raise InvalidSessionTransition("Verification success requires a verification digest.")

    return replace(
        session,
        state=state,
        plan_digest=plan_digest if plan_digest is not None else session.plan_digest,
        candidate_digest=(
            candidate_digest if candidate_digest is not None else session.candidate_digest
        ),
        verification_digest=verification_digest,
        failed_stage=None,
        revision=session.revision + 1,
    )


def _require_state(
    session: GenerationSession,
    event: SessionEvent,
    *allowed_states: SessionState,
) -> None:
    if session.state not in allowed_states:
        raise InvalidSessionTransition(
            f"Event {event.value} is not valid from state {session.state.value}."
        )


def _reject_all_digest_arguments(
    event: SessionEvent,
    configuration_digest: str | None,
    plan_digest: str | None,
    candidate_digest: str | None,
    verification_digest: str | None,
) -> None:
    _reject_digest_arguments(
        event,
        configuration_digest,
        plan_digest,
        candidate_digest,
        verification_digest,
    )


def _reject_digest_arguments(event: SessionEvent, *digests: str | None) -> None:
    if any(digest is not None for digest in digests):
        raise InvalidSessionTransition(f"Event {event.value} does not accept digest arguments.")


def _validate_digest(digest: str, *, field: str) -> None:
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")


class SessionStateStore:
    """Persist generation state with replace-on-success semantics."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path

    def save(self, session: GenerationSession) -> None:
        """Atomically replace the persisted record after a durable temporary write."""
        temporary_path = self._state_path.with_name(
            f"{self._state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        payload = _serialize_session(session)
        LOGGER.info(
            "session_state_write_started run_id=%s state=%s revision=%d file=%s",
            session.run_id,
            session.state.value,
            session.revision,
            self._state_path.name,
        )
        try:
            with temporary_path.open("x", encoding="utf-8", newline="\n") as temporary_file:
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._state_path)
        except OSError:
            LOGGER.error(
                "session_state_write_failed run_id=%s state=%s revision=%d file=%s",
                session.run_id,
                session.state.value,
                session.revision,
                self._state_path.name,
            )
            temporary_path.unlink(missing_ok=True)
            raise
        LOGGER.info(
            "session_state_write_completed run_id=%s state=%s revision=%d file=%s",
            session.run_id,
            session.state.value,
            session.revision,
            self._state_path.name,
        )

    def load(self) -> GenerationSession:
        """Load a complete supported session record."""
        try:
            raw_record = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(raw_record, dict):
                raise TypeError("Session record must be an object.")
            return _deserialize_session(raw_record)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise SessionStateCorruptionError(
                "Session state is incomplete or unsupported."
            ) from error


def _serialize_session(session: GenerationSession) -> str:
    record = asdict(session)
    record["schema_version"] = SESSION_SCHEMA_VERSION
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def _deserialize_session(raw_record: dict[str, object]) -> GenerationSession:
    expected_fields = {
        "candidate_digest",
        "configuration_digest",
        "failed_stage",
        "plan_digest",
        "revision",
        "run_id",
        "schema_version",
        "state",
        "verification_digest",
    }
    if set(raw_record) != expected_fields:
        raise ValueError("Session record fields do not match the schema.")
    if raw_record["schema_version"] != SESSION_SCHEMA_VERSION:
        raise ValueError("Session record schema is unsupported.")

    run_id = raw_record["run_id"]
    configuration_digest = raw_record["configuration_digest"]
    revision = raw_record["revision"]
    if not isinstance(run_id, str) or not isinstance(configuration_digest, str):
        raise TypeError("Session identifiers must be strings.")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise TypeError("Session revision must be an integer.")

    return GenerationSession(
        run_id=run_id,
        state=SessionState(_required_string(raw_record, "state")),
        configuration_digest=configuration_digest,
        plan_digest=_optional_string(raw_record, "plan_digest"),
        candidate_digest=_optional_string(raw_record, "candidate_digest"),
        verification_digest=_optional_string(raw_record, "verification_digest"),
        failed_stage=(
            FailureStage(failed_stage)
            if (failed_stage := _optional_string(raw_record, "failed_stage")) is not None
            else None
        ),
        revision=revision,
    )


def _required_string(raw_record: dict[str, object], field: str) -> str:
    value = raw_record[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string.")
    return value


def _optional_string(raw_record: dict[str, object], field: str) -> str | None:
    value = raw_record[field]
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} must be a string or null.")
    return value
