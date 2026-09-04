from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.target_lock import (
    TargetLockAlreadyHeldError,
    TargetLockRecoveryState,
    TargetLockStore,
)


class TargetLockTests(unittest.TestCase):
    def test_same_target_is_exclusive_while_different_targets_are_independent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_store = TargetLockStore(root / "first-project")
            competing_store = TargetLockStore(root / "first-project")
            other_store = TargetLockStore(root / "other-project")
            first = first_store.acquire(run_id="run-001", process_start_token="start-a")

            with self.assertRaises(TargetLockAlreadyHeldError):
                competing_store.acquire(run_id="run-002", process_start_token="start-b")
            other = other_store.acquire(run_id="run-003", process_start_token="start-c")

            self.assertNotEqual(first.lock_path, other.lock_path)
            first_store.release(first)
            other_store.release(other)
            self.assertFalse(first.lock_path.exists())
            self.assertFalse(other.lock_path.exists())

    def test_stale_recovery_requires_explicit_request_and_a_dead_matching_process(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = TargetLockStore(root / "project")
            lock = store.acquire(run_id="run-001", process_start_token="start-a")

            with self.assertRaisesRegex(ValueError, "explicit"):
                store.recover_stale(
                    explicit=False,
                    process_is_alive=lambda _pid, _start: False,
                )
            self.assertTrue(lock.lock_path.exists())
            self.assertIs(
                store.recover_stale(
                    explicit=True,
                    process_is_alive=lambda _pid, _start: True,
                ),
                TargetLockRecoveryState.ACTIVE,
            )
            self.assertTrue(lock.lock_path.exists())
            self.assertIs(
                store.recover_stale(
                    explicit=True,
                    process_is_alive=lambda _pid, _start: False,
                ),
                TargetLockRecoveryState.RECOVERED,
            )
            self.assertFalse(lock.lock_path.exists())

    def test_tampered_lock_or_existing_target_is_ambiguous_and_never_deleted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            store = TargetLockStore(target)
            lock = store.acquire(run_id="run-001", process_start_token="start-a")
            lock.lock_path.write_text("not-json", encoding="utf-8")

            self.assertIs(
                store.recover_stale(
                    explicit=True,
                    process_is_alive=lambda _pid, _start: False,
                ),
                TargetLockRecoveryState.AMBIGUOUS,
            )
            self.assertTrue(lock.lock_path.exists())

        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            store = TargetLockStore(target)
            lock = store.acquire(run_id="run-001", process_start_token="start-a")
            target.mkdir()

            self.assertIs(
                store.recover_stale(
                    explicit=True,
                    process_is_alive=lambda _pid, _start: False,
                ),
                TargetLockRecoveryState.AMBIGUOUS,
            )
            self.assertTrue(lock.lock_path.exists())
            self.assertTrue(target.exists())

    def test_release_refuses_if_lock_bytes_no_longer_match_owner(self) -> None:
        with TemporaryDirectory() as directory:
            store = TargetLockStore(Path(directory) / "project")
            lock = store.acquire(run_id="run-001", process_start_token="start-a")
            original = lock.lock_path.read_text(encoding="utf-8")
            lock.lock_path.write_text(original.replace("run-001", "run-999"), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "ownership"):
                store.release(lock)

            self.assertTrue(lock.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
