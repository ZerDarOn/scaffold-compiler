from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.session_state_store import (
    FailureStage,
    GenerationSession,
    InvalidSessionTransition,
    SessionEvent,
    SessionState,
    SessionStateCorruptionError,
    SessionStateStore,
    transition_session,
)

CONFIGURATION_DIGEST = "a" * 64
SECOND_CONFIGURATION_DIGEST = "b" * 64
PLAN_DIGEST = "c" * 64
CANDIDATE_DIGEST = "d" * 64
CHANGED_CANDIDATE_DIGEST = "e" * 64
VERIFICATION_DIGEST = "f" * 64


def new_session() -> GenerationSession:
    return GenerationSession.new(
        run_id="run-001",
        configuration_digest=CONFIGURATION_DIGEST,
        target_name="delivery",
    )


def planned_session() -> GenerationSession:
    return transition_session(
        new_session(),
        SessionEvent.PLAN_SUCCEEDED,
        plan_digest=PLAN_DIGEST,
    )


def materialized_session() -> GenerationSession:
    session = transition_session(planned_session(), SessionEvent.MATERIALIZE_REQUESTED)
    return transition_session(
        session,
        SessionEvent.MATERIALIZE_SUCCEEDED,
        candidate_digest=CANDIDATE_DIGEST,
    )


def verified_session() -> GenerationSession:
    session = transition_session(materialized_session(), SessionEvent.VERIFY_REQUESTED)
    return transition_session(
        session,
        SessionEvent.VERIFY_SUCCEEDED,
        verification_digest=VERIFICATION_DIGEST,
    )


def committing_session() -> GenerationSession:
    session = transition_session(verified_session(), SessionEvent.FINALIZE_REQUESTED)
    return transition_session(session, SessionEvent.FINALIZE_CONFIRMED)


class SessionStateMachineTests(unittest.TestCase):
    def test_happy_path_reaches_done_in_the_approved_order(self) -> None:
        session = new_session()
        transitions: tuple[tuple[SessionEvent, dict[str, str], SessionState], ...] = (
            (SessionEvent.PLAN_SUCCEEDED, {"plan_digest": PLAN_DIGEST}, SessionState.PLANNED),
            (SessionEvent.MATERIALIZE_REQUESTED, {}, SessionState.MATERIALIZING),
            (
                SessionEvent.MATERIALIZE_SUCCEEDED,
                {"candidate_digest": CANDIDATE_DIGEST},
                SessionState.MATERIALIZED,
            ),
            (SessionEvent.VERIFY_REQUESTED, {}, SessionState.VERIFYING),
            (
                SessionEvent.VERIFY_SUCCEEDED,
                {"verification_digest": VERIFICATION_DIGEST},
                SessionState.VERIFIED,
            ),
            (SessionEvent.FINALIZE_REQUESTED, {}, SessionState.AWAITING_CONFIRMATION),
            (SessionEvent.FINALIZE_CONFIRMED, {}, SessionState.COMMITTING),
            (SessionEvent.PUBLISH_OBSERVED, {}, SessionState.COMMITTED),
            (SessionEvent.CLEANUP_STARTED, {}, SessionState.CLEANUP_PENDING),
            (SessionEvent.CLEANUP_SUCCEEDED, {}, SessionState.DONE),
        )

        for event, arguments, expected_state in transitions:
            with self.subTest(event=event):
                session = transition_session(session, event, **arguments)
                self.assertIs(session.state, expected_state)

        self.assertEqual(session.revision, len(transitions))

    def test_rejects_illegal_jump_and_duplicate_event(self) -> None:
        with self.assertRaises(InvalidSessionTransition):
            transition_session(new_session(), SessionEvent.VERIFY_REQUESTED)

        session = planned_session()
        with self.assertRaises(InvalidSessionTransition):
            transition_session(
                session,
                SessionEvent.PLAN_SUCCEEDED,
                plan_digest=PLAN_DIGEST,
            )

    def test_requires_digest_for_success_events(self) -> None:
        missing_digest_cases = (
            (new_session(), SessionEvent.PLAN_SUCCEEDED),
            (
                transition_session(planned_session(), SessionEvent.MATERIALIZE_REQUESTED),
                SessionEvent.MATERIALIZE_SUCCEEDED,
            ),
            (
                transition_session(materialized_session(), SessionEvent.VERIFY_REQUESTED),
                SessionEvent.VERIFY_SUCCEEDED,
            ),
        )

        for session, event in missing_digest_cases:
            with self.subTest(event=event), self.assertRaises(InvalidSessionTransition):
                transition_session(session, event)

    def test_retry_returns_to_the_fixed_safe_state_for_each_failure_stage(self) -> None:
        failure_cases = (
            (new_session(), SessionEvent.PLAN_FAILED, FailureStage.PLAN, SessionState.NEW),
            (
                transition_session(planned_session(), SessionEvent.MATERIALIZE_REQUESTED),
                SessionEvent.MATERIALIZE_FAILED,
                FailureStage.MATERIALIZE,
                SessionState.PLANNED,
            ),
            (
                transition_session(materialized_session(), SessionEvent.VERIFY_REQUESTED),
                SessionEvent.VERIFY_FAILED,
                FailureStage.VERIFY,
                SessionState.MATERIALIZED,
            ),
            (
                transition_session(verified_session(), SessionEvent.FINALIZE_REQUESTED),
                SessionEvent.FINALIZE_PRECHECK_FAILED,
                FailureStage.FINALIZE_PRECHECK,
                SessionState.VERIFIED,
            ),
        )

        for session, failure_event, expected_stage, expected_retry_state in failure_cases:
            with self.subTest(failure_event=failure_event):
                failed = transition_session(session, failure_event)
                self.assertIs(failed.state, SessionState.FAILED_RETRYABLE)
                self.assertIs(failed.failed_stage, expected_stage)

                retried = transition_session(failed, SessionEvent.RETRY)
                self.assertIs(retried.state, expected_retry_state)
                self.assertIsNone(retried.failed_stage)

    def test_candidate_change_invalidates_verification(self) -> None:
        session = transition_session(verified_session(), SessionEvent.FINALIZE_REQUESTED)

        changed = transition_session(
            session,
            SessionEvent.CANDIDATE_CHANGED,
            candidate_digest=CHANGED_CANDIDATE_DIGEST,
        )

        self.assertIs(changed.state, SessionState.MATERIALIZED)
        self.assertEqual(changed.candidate_digest, CHANGED_CANDIDATE_DIGEST)
        self.assertIsNone(changed.verification_digest)

    def test_reconfigure_clears_derived_state(self) -> None:
        reconfigured = transition_session(
            verified_session(),
            SessionEvent.RECONFIGURE,
            configuration_digest=SECOND_CONFIGURATION_DIGEST,
        )

        self.assertIs(reconfigured.state, SessionState.NEW)
        self.assertEqual(reconfigured.configuration_digest, SECOND_CONFIGURATION_DIGEST)
        self.assertEqual(reconfigured.target_name, "delivery")
        self.assertIsNone(reconfigured.plan_digest)
        self.assertIsNone(reconfigured.candidate_digest)
        self.assertIsNone(reconfigured.verification_digest)

    def test_committing_recovery_uses_only_the_three_approved_outcomes(self) -> None:
        session = committing_session()
        expected_outcomes = (
            (SessionEvent.RECOVER_BEFORE_PUBLISH, SessionState.AWAITING_CONFIRMATION),
            (SessionEvent.RECOVER_PUBLISHED, SessionState.COMMITTED),
            (SessionEvent.RECOVER_AMBIGUOUS, SessionState.AMBIGUOUS),
        )

        for event, expected_state in expected_outcomes:
            with self.subTest(event=event):
                recovered = transition_session(session, event)
                self.assertIs(recovered.state, expected_state)

    def test_ambiguous_done_and_cancelled_states_reject_automatic_progress(self) -> None:
        ambiguous = transition_session(committing_session(), SessionEvent.RECOVER_AMBIGUOUS)
        done = transition_session(
            transition_session(
                transition_session(committing_session(), SessionEvent.PUBLISH_OBSERVED),
                SessionEvent.CLEANUP_STARTED,
            ),
            SessionEvent.CLEANUP_SUCCEEDED,
        )
        cancelled = transition_session(new_session(), SessionEvent.CANCEL)

        for session in (ambiguous, done, cancelled):
            for event in (SessionEvent.PLAN_SUCCEEDED, SessionEvent.STALE_LOCK_RECOVERED):
                with (
                    self.subTest(state=session.state, event=event),
                    self.assertRaises(InvalidSessionTransition),
                ):
                    transition_session(session, event, plan_digest=PLAN_DIGEST)

    def test_success_events_reject_unrelated_digest_arguments(self) -> None:
        with self.assertRaises(InvalidSessionTransition):
            transition_session(
                new_session(),
                SessionEvent.PLAN_SUCCEEDED,
                plan_digest=PLAN_DIGEST,
                candidate_digest=CANDIDATE_DIGEST,
            )

    def test_cancel_is_rejected_once_committing_has_started(self) -> None:
        self.assertIs(
            transition_session(verified_session(), SessionEvent.CANCEL).state,
            SessionState.CANCELLED,
        )

        with self.assertRaises(InvalidSessionTransition):
            transition_session(committing_session(), SessionEvent.CANCEL)

    def test_cleanup_failure_remains_retryable_without_reverting_commit(self) -> None:
        cleanup_pending = transition_session(
            transition_session(committing_session(), SessionEvent.PUBLISH_OBSERVED),
            SessionEvent.CLEANUP_STARTED,
        )

        failed_cleanup = transition_session(cleanup_pending, SessionEvent.CLEANUP_FAILED)

        self.assertIs(failed_cleanup.state, SessionState.CLEANUP_PENDING)
        self.assertEqual(failed_cleanup.revision, cleanup_pending.revision + 1)

    def test_transition_logs_only_stable_session_identifiers(self) -> None:
        with self.assertLogs("scaffold_compiler.session_state_store", level="INFO") as logs:
            transition_session(
                new_session(),
                SessionEvent.PLAN_SUCCEEDED,
                plan_digest=PLAN_DIGEST,
            )

        output = "\n".join(logs.output)
        self.assertIn("run-001", output)
        self.assertIn("new", output)
        self.assertIn("planned", output)
        self.assertNotIn(CONFIGURATION_DIGEST, output)


class SessionStateStoreTests(unittest.TestCase):
    def test_round_trips_a_session_record(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "session.json"
            store = SessionStateStore(state_path)
            expected = verified_session()

            store.save(expected)

            self.assertEqual(store.load(), expected)
            record = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 2)
            self.assertEqual(record["target_name"], "delivery")

    def test_loads_legacy_schema_one_without_target_binding(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "session.json"
            state_path.write_text(
                json.dumps(
                    {
                        "candidate_digest": None,
                        "configuration_digest": CONFIGURATION_DIGEST,
                        "failed_stage": None,
                        "plan_digest": None,
                        "revision": 0,
                        "run_id": "legacy-run",
                        "schema_version": 1,
                        "state": "new",
                        "verification_digest": None,
                    }
                ),
                encoding="utf-8",
            )

            loaded = SessionStateStore(state_path).load()

            self.assertEqual(loaded.run_id, "legacy-run")
            self.assertIsNone(loaded.target_name)

    def test_failed_atomic_replace_preserves_previous_complete_record(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "session.json"
            store = SessionStateStore(state_path)
            initial = new_session()
            store.save(initial)
            updated = planned_session()

            with (
                patch(
                    "scaffold_compiler.session_state_store.os.replace",
                    side_effect=OSError("simulated replace failure"),
                ),
                self.assertLogs("scaffold_compiler.session_state_store", level="ERROR"),
                self.assertRaises(OSError),
            ):
                store.save(updated)

            self.assertEqual(store.load(), initial)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_rejects_truncated_or_unknown_schema_records(self) -> None:
        invalid_records = ('{"schema_version":', json.dumps({"schema_version": 99}))

        for invalid_record in invalid_records:
            with self.subTest(invalid_record=invalid_record), TemporaryDirectory() as directory:
                state_path = Path(directory) / "session.json"
                state_path.write_text(invalid_record, encoding="utf-8")

                with self.assertRaises(SessionStateCorruptionError):
                    SessionStateStore(state_path).load()


if __name__ == "__main__":
    unittest.main()
