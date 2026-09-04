from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_ownership_store import CandidateOwnershipStore
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.failed_workspace_recovery import (
    discard_failed_workspace,
    inspect_failed_workspace,
)
from scaffold_compiler.session_state_store import (
    FailureStage,
    GenerationSession,
    SessionEvent,
    SessionState,
    SessionStateStore,
    transition_session,
)
from scaffold_compiler.validation import (
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
)
from scaffold_compiler.validation_report_store import ValidationReportStore
from scaffold_compiler.validation_workspace import prepare_validation_workspace


def prepare_failed_workspace(root: Path) -> tuple[Path, CandidateAssemblyResult]:
    workspace = root / ".delivery.scaffold-run-1"
    workspace.mkdir()
    candidate_root = workspace / "candidate"
    candidate_root.mkdir()
    content = b"# Example\n"
    (candidate_root / "README.md").write_bytes(content)
    files = (
        CandidateFileRecord(
            "README.md",
            "project-quality",
            len(content),
            hashlib.sha256(content).hexdigest(),
        ),
    )
    candidate = CandidateAssemblyResult(
        candidate_root,
        files,
        calculate_candidate_digest(candidate_root, files),
        "b" * 64,
    )
    CandidateOwnershipStore(workspace / "candidate_ownership.json").save(candidate)
    session = GenerationSession(
        run_id="run-1",
        state=SessionState.FAILED_RETRYABLE,
        configuration_digest="a" * 64,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        failed_stage=FailureStage.VERIFY,
        revision=5,
    )
    SessionStateStore(workspace / "session.json").save(session)
    validation_workspace = prepare_validation_workspace(workspace, run_id="run-1")
    (validation_workspace.root / "cache.bin").write_bytes(b"cache")
    report = ValidationReport(
        configuration_digest=session.configuration_digest,
        blueprint_digest="c" * 64,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        checks=(
            ValidationCheck(
                "pytest",
                ValidationStatus.FAIL,
                required=True,
                phase=ValidationPhase.QUALITY,
                diagnostics="Validation process exited with code 1.",
            ),
        ),
    )
    ValidationReportStore(workspace / "validation_report.json").save(report, workspace)
    return workspace, candidate


class FailedWorkspaceRecoveryTests(unittest.TestCase):
    def test_inspect_reports_bound_state_candidate_and_failed_gates_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            workspace, candidate = prepare_failed_workspace(Path(directory))

            inspection = inspect_failed_workspace(workspace)

            self.assertEqual(inspection.run_id, "run-1")
            self.assertEqual(inspection.state, SessionState.FAILED_RETRYABLE)
            self.assertEqual(inspection.failed_stage, FailureStage.VERIFY)
            self.assertEqual(inspection.candidate_digest, candidate.digest)
            self.assertEqual(inspection.failed_gates, ("pytest",))
            self.assertTrue(inspection.evidence_valid)
            self.assertTrue(workspace.exists())

    def test_discard_removes_only_the_exact_failed_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _candidate = prepare_failed_workspace(root)
            sibling = root / "delivery"
            sibling.mkdir()
            (sibling / "keep.txt").write_text("keep", encoding="utf-8")

            result = discard_failed_workspace(workspace)

            self.assertTrue(result.completed)
            self.assertFalse(workspace.exists())
            self.assertEqual((sibling / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_changed_or_extra_evidence_causes_zero_deletions(self) -> None:
        for mutation in (
            "extra-root",
            "extra-candidate",
            "changed-candidate",
            "changed-report",
        ):
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                workspace, candidate = prepare_failed_workspace(Path(directory))
                if mutation == "extra-root":
                    (workspace / "unknown.txt").write_text("keep", encoding="utf-8")
                elif mutation == "extra-candidate":
                    (candidate.root / "unknown.txt").write_text("keep", encoding="utf-8")
                elif mutation == "changed-candidate":
                    (candidate.root / "README.md").write_text("changed", encoding="utf-8")
                else:
                    report_path = workspace / "validation_report.json"
                    report_path.write_text(
                        report_path.read_text(encoding="utf-8").replace(
                            '"configuration_digest":"' + "a" * 64 + '"',
                            '"configuration_digest":"' + "d" * 64 + '"',
                        ),
                        encoding="utf-8",
                    )
                validation_cache = workspace / "validation-env" / "cache.bin"

                result = discard_failed_workspace(workspace)

                self.assertFalse(result.completed)
                self.assertTrue(workspace.exists())
                self.assertTrue(validation_cache.exists())
                self.assertTrue((candidate.root / "README.md").exists())

    def test_missing_verify_report_is_reported_as_incomplete_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            workspace, _candidate = prepare_failed_workspace(Path(directory))
            (workspace / "validation_report.json").unlink()

            inspection = inspect_failed_workspace(workspace)

            self.assertFalse(inspection.evidence_valid)
            self.assertEqual(inspection.failed_gates, ())

    def test_committed_or_ambiguous_state_is_never_discarded(self) -> None:
        for state in (SessionState.COMMITTED, SessionState.CLEANUP_PENDING, SessionState.AMBIGUOUS):
            with self.subTest(state=state), TemporaryDirectory() as directory:
                workspace, _candidate = prepare_failed_workspace(Path(directory))
                original = SessionStateStore(workspace / "session.json").load()
                unsafe = GenerationSession(
                    run_id=original.run_id,
                    state=state,
                    configuration_digest=original.configuration_digest,
                    plan_digest=original.plan_digest,
                    candidate_digest=original.candidate_digest,
                    revision=original.revision,
                )
                SessionStateStore(workspace / "session.json").save(unsafe)

                result = discard_failed_workspace(workspace)

                self.assertFalse(result.completed)
                self.assertTrue(workspace.exists())

    def test_cancelled_cleanup_continues_after_candidate_was_already_removed(self) -> None:
        with TemporaryDirectory() as directory:
            workspace, candidate = prepare_failed_workspace(Path(directory))
            for record in candidate.files:
                (candidate.root / record.path).unlink()
            candidate.root.rmdir()
            store = SessionStateStore(workspace / "session.json")
            store.save(transition_session(store.load(), SessionEvent.CANCEL))

            result = discard_failed_workspace(workspace)

            self.assertTrue(result.completed)
            self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
