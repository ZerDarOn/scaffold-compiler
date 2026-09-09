from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.unit.test_failed_workspace_recovery import prepare_failed_workspace

from scaffold_compiler.cleanup_recovery import retry_cleanup_pending_workspace
from scaffold_compiler.session_state_store import (
    GenerationSession,
    SessionState,
    SessionStateStore,
)
from scaffold_compiler.validation import ValidationCheck, ValidationReport, ValidationStatus
from scaffold_compiler.validation_report_store import ValidationReportStore


def prepare_cleanup_pending_workspace(
    root: Path,
    *,
    current_workspace: bool = False,
) -> tuple[Path, Path]:
    workspace, candidate = prepare_failed_workspace(root, current_workspace=current_workspace)
    target = root / "delivery"
    shutil.copytree(candidate.root, target)
    original = SessionStateStore(workspace / "session.json").load()
    SessionStateStore(workspace / "session.json").save(
        GenerationSession(
            run_id=original.run_id,
            state=SessionState.CLEANUP_PENDING,
            configuration_digest=original.configuration_digest,
            target_name=original.target_name,
            plan_digest=original.plan_digest,
            candidate_digest=original.candidate_digest,
            verification_digest="f" * 64,
            revision=original.revision + 1,
        )
    )
    report_store = ValidationReportStore(workspace / "validation_report.json")
    report = report_store.load(workspace)
    report_store.save(
        ValidationReport(
            configuration_digest=report.configuration_digest,
            blueprint_digest=report.blueprint_digest,
            plan_digest=report.plan_digest,
            candidate_digest=report.candidate_digest,
            checks=(ValidationCheck("pytest", ValidationStatus.PASS, required=True),),
        ),
        workspace,
    )
    return workspace, target


class CleanupRecoveryTests(unittest.TestCase):
    def test_current_short_workspace_cleanup_is_retryable(self) -> None:
        with TemporaryDirectory() as directory:
            workspace, target = prepare_cleanup_pending_workspace(
                Path(directory).resolve(),
                current_workspace=True,
            )

            result = retry_cleanup_pending_workspace(workspace)

            self.assertTrue(result.completed, result.reason)
            self.assertFalse(workspace.exists())
            self.assertEqual((target / "README.md").read_bytes(), b"# Example\n")

    def test_changed_short_workspace_target_binding_causes_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, target = prepare_cleanup_pending_workspace(
                root,
                current_workspace=True,
            )
            store = SessionStateStore(workspace / "session.json")
            original = store.load()
            store.save(
                GenerationSession(
                    run_id=original.run_id,
                    state=original.state,
                    configuration_digest=original.configuration_digest,
                    target_name="other-target",
                    plan_digest=original.plan_digest,
                    candidate_digest=original.candidate_digest,
                    verification_digest=original.verification_digest,
                    revision=original.revision,
                )
            )
            candidate_file = workspace / "candidate" / "README.md"
            validation_file = workspace / "validation-env" / "cache.bin"

            result = retry_cleanup_pending_workspace(workspace)

            self.assertFalse(result.completed)
            self.assertTrue(candidate_file.exists())
            self.assertTrue(validation_file.exists())
            self.assertTrue((target / "README.md").exists())
            self.assertTrue(workspace.exists())

    def test_cleanup_failure_is_retryable_and_never_changes_published_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, target = prepare_cleanup_pending_workspace(root)
            failed_once = False

            def fail_once(path: Path) -> None:
                nonlocal failed_once
                if path.name == "README.md" and not failed_once:
                    failed_once = True
                    raise PermissionError("simulated file lock")
                path.unlink()

            first = retry_cleanup_pending_workspace(
                workspace,
                candidate_unlinker=fail_once,
            )
            second = retry_cleanup_pending_workspace(workspace)
            repeated = retry_cleanup_pending_workspace(workspace)

            self.assertFalse(first.completed)
            self.assertIn("incomplete", first.reason.lower())
            self.assertTrue(second.completed)
            self.assertFalse(repeated.completed)
            self.assertFalse(workspace.exists())
            self.assertEqual((target / "README.md").read_bytes(), b"# Example\n")

    def test_changed_published_target_refuses_cleanup_with_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, target = prepare_cleanup_pending_workspace(root)
            candidate_file = workspace / "candidate" / "README.md"
            validation_file = workspace / "validation-env" / "cache.bin"
            (target / "README.md").write_text("changed\n", encoding="utf-8")

            result = retry_cleanup_pending_workspace(workspace)

            self.assertFalse(result.completed)
            self.assertIn("published target", result.reason.lower())
            self.assertTrue(candidate_file.exists())
            self.assertTrue(validation_file.exists())
            self.assertTrue(workspace.exists())

    def test_unknown_candidate_entry_refuses_cleanup_with_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _target = prepare_cleanup_pending_workspace(root)
            candidate_file = workspace / "candidate" / "README.md"
            unknown_file = workspace / "candidate" / "unknown.txt"
            validation_file = workspace / "validation-env" / "cache.bin"
            unknown_file.write_text("unowned\n", encoding="utf-8")

            result = retry_cleanup_pending_workspace(workspace)

            self.assertFalse(result.completed)
            self.assertIn("candidate ownership", result.reason.lower())
            self.assertTrue(candidate_file.exists())
            self.assertTrue(unknown_file.exists())
            self.assertTrue(validation_file.exists())
            self.assertTrue(workspace.exists())

    def test_non_cleanup_pending_workspace_is_never_recovered(self) -> None:
        with TemporaryDirectory() as directory:
            workspace, _candidate = prepare_failed_workspace(Path(directory))

            result = retry_cleanup_pending_workspace(workspace)

            self.assertFalse(result.completed)
            self.assertIn("state", result.reason.lower())
            self.assertTrue(workspace.exists())


if __name__ == "__main__":
    unittest.main()
