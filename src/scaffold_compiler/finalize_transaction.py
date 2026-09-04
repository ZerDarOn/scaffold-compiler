"""Journal-first coordination for irreversible project publication."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from scaffold_compiler.finalization import (
    CommitSnapshot,
    PublishRecoveryState,
    publish_commit_snapshot,
    recover_publish_state,
)
from scaffold_compiler.session_state_store import (
    GenerationSession,
    SessionEvent,
    SessionState,
    SessionStateStore,
    transition_session,
)
from scaffold_compiler.validation import VerificationCredential

LOGGER = logging.getLogger(__name__)


class FinalizeTransactionError(RuntimeError):
    """Raised when journal state or digest bindings cannot authorize Finalize."""


def publish_confirmed_project(
    session: GenerationSession,
    credential: VerificationCredential,
    snapshot: CommitSnapshot,
    target: Path,
    *,
    store: SessionStateStore,
    mover: Callable[[Path, Path], None] | None = None,
    post_move_hook: Callable[[Path], None] | None = None,
) -> GenerationSession:
    """Persist COMMITTING, publish exactly once, then persist COMMITTED."""
    if session.state is not SessionState.AWAITING_CONFIRMATION:
        raise FinalizeTransactionError("Session is not awaiting Finalize confirmation.")
    _require_verified_binding(session, credential, snapshot)

    committing = transition_session(session, SessionEvent.FINALIZE_CONFIRMED)
    store.save(committing)
    publish_commit_snapshot(
        snapshot,
        target,
        mover=mover,
        post_move_hook=post_move_hook,
    )
    committed = transition_session(committing, SessionEvent.PUBLISH_OBSERVED)
    store.save(committed)
    LOGGER.info(
        "finalize_transaction_committed run_id=%s target_name=%s digest=%s",
        session.run_id,
        target.name,
        snapshot.digest,
    )
    return committed


def recover_finalize_transaction(
    session: GenerationSession,
    snapshot: CommitSnapshot,
    target: Path,
    *,
    store: SessionStateStore,
) -> GenerationSession:
    """Persist the single state implied by disk facts without retrying publication."""
    if session.state is SessionState.COMMITTED:
        if recover_publish_state(snapshot, target) is not PublishRecoveryState.PUBLISHED:
            raise FinalizeTransactionError("Committed journal conflicts with disk facts.")
        return session
    if session.state is SessionState.AMBIGUOUS:
        return session
    if session.state is not SessionState.COMMITTING:
        raise FinalizeTransactionError("Only a COMMITTING session can be recovered.")

    outcome = recover_publish_state(snapshot, target)
    event = {
        PublishRecoveryState.BEFORE_PUBLISH: SessionEvent.RECOVER_BEFORE_PUBLISH,
        PublishRecoveryState.PUBLISHED: SessionEvent.RECOVER_PUBLISHED,
        PublishRecoveryState.AMBIGUOUS: SessionEvent.RECOVER_AMBIGUOUS,
    }[outcome]
    recovered = transition_session(session, event)
    store.save(recovered)
    LOGGER.info(
        "finalize_transaction_recovered run_id=%s outcome=%s state=%s",
        session.run_id,
        outcome.value,
        recovered.state.value,
    )
    return recovered


def _require_verified_binding(
    session: GenerationSession,
    credential: VerificationCredential,
    snapshot: CommitSnapshot,
) -> None:
    expected = (
        session.configuration_digest,
        session.plan_digest,
        session.candidate_digest,
        session.verification_digest,
    )
    supplied = (
        credential.configuration_digest,
        credential.plan_digest,
        credential.candidate_digest,
        credential.verification_digest,
    )
    if expected != supplied:
        raise FinalizeTransactionError("Verification credential does not bind this session.")
    if snapshot.digest != credential.candidate_digest:
        raise FinalizeTransactionError("Commit snapshot does not bind the verified candidate.")
    if snapshot.plan_digest != credential.plan_digest:
        raise FinalizeTransactionError("Commit snapshot does not bind the verified plan.")
