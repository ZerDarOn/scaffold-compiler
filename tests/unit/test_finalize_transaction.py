from __future__ import annotations

import errno
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tests.unit.test_finalization import assembled_candidate

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.finalization import (
    CommitSnapshot,
    TargetAlreadyExistsError,
    prepare_commit_snapshot,
)
from scaffold_compiler.finalize_transaction import (
    publish_confirmed_project,
    recover_finalize_transaction,
)
from scaffold_compiler.session_state_store import (
    GenerationSession,
    SessionEvent,
    SessionState,
    SessionStateStore,
    transition_session,
)
from scaffold_compiler.validation import VerificationCredential

CONFIGURATION_DIGEST = "c" * 64
BLUEPRINT_DIGEST = "b" * 64
VERIFICATION_DIGEST = "f" * 64


def awaiting_session(candidate_digest: str) -> GenerationSession:
    session = GenerationSession.new(
        run_id="run-001",
        configuration_digest=CONFIGURATION_DIGEST,
    )
    session = transition_session(
        session,
        SessionEvent.PLAN_SUCCEEDED,
        plan_digest="a" * 64,
    )
    session = transition_session(session, SessionEvent.MATERIALIZE_REQUESTED)
    session = transition_session(
        session,
        SessionEvent.MATERIALIZE_SUCCEEDED,
        candidate_digest=candidate_digest,
    )
    session = transition_session(session, SessionEvent.VERIFY_REQUESTED)
    session = transition_session(
        session,
        SessionEvent.VERIFY_SUCCEEDED,
        verification_digest=VERIFICATION_DIGEST,
    )
    return transition_session(session, SessionEvent.FINALIZE_REQUESTED)


def credential(candidate_digest: str) -> VerificationCredential:
    return VerificationCredential(
        configuration_digest=CONFIGURATION_DIGEST,
        blueprint_digest=BLUEPRINT_DIGEST,
        plan_digest="a" * 64,
        candidate_digest=candidate_digest,
        verification_digest=VERIFICATION_DIGEST,
    )


class FinalizeTransactionTests(unittest.TestCase):
    def prepare(
        self, root: Path
    ) -> tuple[CandidateAssemblyResult, CommitSnapshot, GenerationSession]:
        workspace = root / ".scaffold-run"
        workspace.mkdir()
        candidate = assembled_candidate(workspace)
        snapshot = prepare_commit_snapshot(candidate, workspace, run_id="run-001")
        return candidate, snapshot, awaiting_session(candidate.digest)

    def test_failed_intent_write_prevents_any_publish_side_effect(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = Mock(spec=SessionStateStore)
            store.save.side_effect = OSError("simulated journal failure")
            mover = Mock()

            with self.assertRaises(OSError):
                publish_confirmed_project(
                    session,
                    credential(candidate.digest),
                    snapshot,
                    target,
                    store=store,
                    mover=mover,
                )

            mover.assert_not_called()
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_mismatched_verification_credential_causes_zero_side_effects(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = Mock(spec=SessionStateStore)
            invalid = credential(candidate.digest)
            invalid = VerificationCredential(
                configuration_digest="d" * 64,
                blueprint_digest=invalid.blueprint_digest,
                plan_digest=invalid.plan_digest,
                candidate_digest=invalid.candidate_digest,
                verification_digest=invalid.verification_digest,
            )

            with self.assertRaisesRegex(RuntimeError, "credential"):
                publish_confirmed_project(
                    session,
                    invalid,
                    snapshot,
                    target,
                    store=store,
                )

            store.save.assert_not_called()
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_result_write_failure_recovers_forward_to_committed_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = SessionStateStore(root / "journal.json")
            store.save(session)
            original_save = store.save
            saves = 0

            def fail_second_save(updated: GenerationSession) -> None:
                nonlocal saves
                saves += 1
                if saves == 2:
                    raise OSError("simulated result journal failure")
                original_save(updated)

            with (
                patch.object(store, "save", side_effect=fail_second_save),
                self.assertRaises(OSError),
            ):
                publish_confirmed_project(
                    session,
                    credential(candidate.digest),
                    snapshot,
                    target,
                    store=store,
                )

            persisted = store.load()
            self.assertIs(persisted.state, SessionState.COMMITTING)
            self.assertTrue(target.exists())
            self.assertFalse(snapshot.root.exists())

            recovered = recover_finalize_transaction(
                persisted,
                snapshot,
                target,
                store=store,
            )
            repeated = recover_finalize_transaction(
                recovered,
                snapshot,
                target,
                store=store,
            )

            self.assertIs(recovered.state, SessionState.COMMITTED)
            self.assertEqual(repeated, recovered)
            self.assertTrue(target.exists())

    def test_pre_move_failure_recovers_to_confirmation_without_retrying_move(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = SessionStateStore(root / "journal.json")
            store.save(session)

            def fail_before_move(_source: Path, _target: Path) -> None:
                raise OSError("simulated native failure")

            with self.assertRaises(OSError):
                publish_confirmed_project(
                    session,
                    credential(candidate.digest),
                    snapshot,
                    target,
                    store=store,
                    mover=fail_before_move,
                )

            persisted = store.load()
            recovered = recover_finalize_transaction(
                persisted,
                snapshot,
                target,
                store=store,
            )
            self.assertIs(recovered.state, SessionState.AWAITING_CONFIRMATION)
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_recovery_result_write_failure_is_safe_to_retry_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = SessionStateStore(root / "journal.json")
            committing = transition_session(session, SessionEvent.FINALIZE_CONFIRMED)
            store.save(committing)

            with (
                patch(
                    "scaffold_compiler.finalize_transaction.publish_commit_snapshot",
                    side_effect=AssertionError("recovery must not publish"),
                ) as publish_mock,
            ):
                with (
                    patch.object(store, "save", side_effect=OSError("simulated recovery write")),
                    self.assertRaises(OSError),
                ):
                    recover_finalize_transaction(
                        committing,
                        snapshot,
                        target,
                        store=store,
                    )

                self.assertIs(store.load().state, SessionState.COMMITTING)
                recovered = recover_finalize_transaction(
                    store.load(),
                    snapshot,
                    target,
                    store=store,
                )
                publish_mock.assert_not_called()
            self.assertIs(recovered.state, SessionState.AWAITING_CONFIRMATION)
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_target_race_after_intent_recovers_to_ambiguous_without_deleting_either(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, snapshot, session = self.prepare(root)
            target = root / "delivery"
            store = SessionStateStore(root / "journal.json")
            store.save(session)

            def race(source: Path, destination: Path) -> None:
                destination.mkdir()
                (destination / "competitor.txt").write_text("keep", encoding="utf-8")
                raise FileExistsError(errno.EEXIST, "target exists", destination)

            with self.assertRaises(TargetAlreadyExistsError):
                publish_confirmed_project(
                    session,
                    credential(candidate.digest),
                    snapshot,
                    target,
                    store=store,
                    mover=race,
                )
            recovered = recover_finalize_transaction(
                store.load(),
                snapshot,
                target,
                store=store,
            )

            self.assertIs(recovered.state, SessionState.AMBIGUOUS)
            self.assertTrue(snapshot.root.exists())
            self.assertEqual((target / "competitor.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
