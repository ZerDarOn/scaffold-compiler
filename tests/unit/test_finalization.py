from __future__ import annotations

import errno
import hashlib
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.finalization import (
    CommitSnapshot,
    CommitSnapshotChangedError,
    CrossVolumeFinalizeError,
    NativeNoReplaceUnavailableError,
    PublishDigestMismatchError,
    PublishRecoveryState,
    SnapshotStage,
    TargetAlreadyExistsError,
    cleanup_owned_candidate,
    prepare_commit_snapshot,
    publish_commit_snapshot,
    recover_publish_state,
)


def assembled_candidate(workspace: Path) -> CandidateAssemblyResult:
    candidate = workspace / "candidate"
    (candidate / "src").mkdir(parents=True)
    contents = {
        "README.md": b"# Example\n",
        "src/application.py": b"VALUE = 1\n",
    }
    records = []
    for relative_path, content in contents.items():
        path = candidate / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append(
            CandidateFileRecord(
                path=relative_path,
                owner="test",
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    frozen_records = tuple(sorted(records, key=lambda record: record.path))
    return CandidateAssemblyResult(
        root=candidate,
        files=frozen_records,
        digest=calculate_candidate_digest(candidate, frozen_records),
        plan_digest="a" * 64,
    )


class CommitSnapshotTests(unittest.TestCase):
    def test_copies_exact_bytes_then_seals_a_hidden_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".scaffold-run"
            workspace.mkdir()
            candidate = assembled_candidate(workspace)

            snapshot = prepare_commit_snapshot(candidate, workspace, run_id="run-001")

            self.assertEqual(snapshot.digest, candidate.digest)
            self.assertEqual(snapshot.root.name, ".commit-run-001")
            self.assertEqual(
                (snapshot.root / "README.md").read_bytes(),
                (candidate.root / "README.md").read_bytes(),
            )
            self.assertFalse((snapshot.root / "README.md").stat().st_mode & stat.S_IWRITE)

    def test_candidate_changes_before_during_or_after_copy_abort_and_clean_snapshot(self) -> None:
        mutation_stages = (
            SnapshotStage.BEFORE_COPY,
            SnapshotStage.AFTER_FILE_COPY,
            SnapshotStage.AFTER_COPY,
        )
        for mutation_stage in mutation_stages:
            with self.subTest(stage=mutation_stage), TemporaryDirectory() as directory:
                workspace = Path(directory) / ".scaffold-run"
                workspace.mkdir()
                candidate = assembled_candidate(workspace)
                mutated = False

                def mutate(
                    stage: SnapshotStage,
                    *,
                    expected_stage: SnapshotStage = mutation_stage,
                    candidate_result: CandidateAssemblyResult = candidate,
                ) -> None:
                    nonlocal mutated
                    if stage is expected_stage and not mutated:
                        (candidate_result.root / "README.md").write_text(
                            "changed", encoding="utf-8"
                        )
                        mutated = True

                with self.assertRaises(CommitSnapshotChangedError):
                    prepare_commit_snapshot(
                        candidate,
                        workspace,
                        run_id="run-001",
                        fault_injector=mutate,
                    )

                self.assertFalse((workspace / ".commit-run-001").exists())

    def test_tampering_after_seal_aborts_and_precisely_removes_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".scaffold-run"
            workspace.mkdir()
            candidate = assembled_candidate(workspace)

            def tamper(stage: SnapshotStage) -> None:
                if stage is SnapshotStage.AFTER_SEAL:
                    readme = workspace / ".commit-run-001" / "README.md"
                    readme.chmod(stat.S_IREAD | stat.S_IWRITE)
                    readme.write_text("tampered", encoding="utf-8")

            with self.assertRaises(CommitSnapshotChangedError):
                prepare_commit_snapshot(
                    candidate,
                    workspace,
                    run_id="run-001",
                    fault_injector=tamper,
                )

            self.assertFalse((workspace / ".commit-run-001").exists())


class AtomicPublishTests(unittest.TestCase):
    def prepare(self, root: Path) -> CommitSnapshot:
        workspace = root / ".scaffold-run"
        workspace.mkdir()
        candidate = assembled_candidate(workspace)
        return prepare_commit_snapshot(candidate, workspace, run_id="run-001")

    def test_native_publish_moves_without_replacement_and_verifies_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"

            published = publish_commit_snapshot(snapshot, target)

            self.assertEqual(published.root, target)
            self.assertFalse(snapshot.root.exists())
            self.assertEqual(published.digest, snapshot.digest)
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "# Example\n")

    def test_target_race_never_replaces_competitor_and_preserves_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"

            def racing_mover(source: Path, destination: Path) -> None:
                destination.mkdir()
                (destination / "owned-by-user.txt").write_text("keep", encoding="utf-8")
                raise FileExistsError(errno.EEXIST, "target exists", destination)

            with self.assertRaises(TargetAlreadyExistsError):
                publish_commit_snapshot(snapshot, target, mover=racing_mover)

            self.assertTrue(snapshot.root.exists())
            self.assertEqual((target / "owned-by-user.txt").read_text(encoding="utf-8"), "keep")

    def test_cross_volume_or_missing_native_primitive_refuses_without_moving(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"
            with (
                patch(
                    "scaffold_compiler.finalization._require_same_volume",
                    side_effect=CrossVolumeFinalizeError("different volume"),
                ),
                self.assertRaises(CrossVolumeFinalizeError),
            ):
                publish_commit_snapshot(snapshot, target)
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"

            def unavailable(_source: Path, _target: Path) -> None:
                raise NativeNoReplaceUnavailableError("unsupported")

            with self.assertRaises(NativeNoReplaceUnavailableError):
                publish_commit_snapshot(snapshot, target, mover=unavailable)
            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_tampered_sealed_snapshot_is_rejected_before_publish(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            readme = snapshot.root / "README.md"
            readme.chmod(stat.S_IREAD | stat.S_IWRITE)
            readme.write_text("tampered", encoding="utf-8")
            target = root / "delivery"

            with self.assertRaises(CommitSnapshotChangedError):
                publish_commit_snapshot(snapshot, target)

            self.assertTrue(snapshot.root.exists())
            self.assertFalse(target.exists())

    def test_post_move_digest_mismatch_is_ambiguous_and_never_deletes_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"

            def tamper_after_move(published_root: Path) -> None:
                readme = published_root / "README.md"
                readme.chmod(stat.S_IREAD | stat.S_IWRITE)
                readme.write_text("tampered", encoding="utf-8")

            with self.assertRaises(PublishDigestMismatchError):
                publish_commit_snapshot(snapshot, target, post_move_hook=tamper_after_move)

            self.assertTrue(target.exists())
            self.assertFalse(snapshot.root.exists())

    def test_repeated_finalize_never_changes_an_already_published_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.prepare(root)
            target = root / "delivery"
            publish_commit_snapshot(first, target)
            original = (target / "README.md").read_bytes()

            workspace = root / ".scaffold-run-2"
            workspace.mkdir()
            second_candidate = assembled_candidate(workspace)
            second = prepare_commit_snapshot(second_candidate, workspace, run_id="run-002")
            with self.assertRaises(TargetAlreadyExistsError):
                publish_commit_snapshot(second, target)

            self.assertEqual((target / "README.md").read_bytes(), original)
            self.assertTrue(second.root.exists())

    def test_recovery_classifies_before_after_and_ambiguous_disk_facts_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"
            for _ in range(2):
                self.assertIs(
                    recover_publish_state(snapshot, target),
                    PublishRecoveryState.BEFORE_PUBLISH,
                )

            published = publish_commit_snapshot(snapshot, target)
            for _ in range(2):
                self.assertIs(
                    recover_publish_state(snapshot, target),
                    PublishRecoveryState.PUBLISHED,
                )
            self.assertTrue(published.root.exists())

        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = self.prepare(root)
            target = root / "delivery"
            target.mkdir()

            for _ in range(2):
                self.assertIs(
                    recover_publish_state(snapshot, target),
                    PublishRecoveryState.AMBIGUOUS,
                )
            self.assertTrue(snapshot.root.exists())
            self.assertTrue(target.exists())


class OwnedCandidateCleanupTests(unittest.TestCase):
    def test_modified_or_extra_candidate_content_causes_zero_deletions(self) -> None:
        for unsafe_change in ("modified", "extra"):
            with self.subTest(change=unsafe_change), TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / ".scaffold-run"
                workspace.mkdir()
                candidate = assembled_candidate(workspace)
                if unsafe_change == "modified":
                    (candidate.root / "README.md").write_text("changed", encoding="utf-8")
                else:
                    (candidate.root / "user-file.txt").write_text("keep", encoding="utf-8")

                result = cleanup_owned_candidate(candidate)

                self.assertFalse(result.completed)
                self.assertTrue((candidate.root / "README.md").exists())
                self.assertTrue((candidate.root / "src/application.py").exists())

    def test_linked_candidate_root_causes_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".scaffold-run"
            workspace.mkdir()
            candidate = assembled_candidate(workspace)

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == candidate.root,
            ):
                result = cleanup_owned_candidate(candidate)

            self.assertFalse(result.completed)
            self.assertTrue((candidate.root / "README.md").exists())

    def test_partial_cleanup_is_retryable_and_never_touches_published_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / ".scaffold-run"
            workspace.mkdir()
            candidate = assembled_candidate(workspace)
            target = root / "delivery"
            target.mkdir()
            (target / "project.txt").write_text("preserve", encoding="utf-8")
            failed_once = False

            def fail_once(path: Path) -> None:
                nonlocal failed_once
                if path.name == "application.py" and not failed_once:
                    failed_once = True
                    raise PermissionError("simulated lock")
                path.unlink()

            first = cleanup_owned_candidate(candidate, unlinker=fail_once)
            second = cleanup_owned_candidate(candidate)

            self.assertFalse(first.completed)
            self.assertTrue(second.completed)
            self.assertFalse(candidate.root.exists())
            self.assertEqual((target / "project.txt").read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
