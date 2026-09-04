from __future__ import annotations

import os
import sys
import threading
import time
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import scaffold_compiler.capsule_package as capsule_package
from scaffold_compiler.capsule_package import (
    CapsuleOwnershipError,
    DevelopmentSourceError,
    build_capsule_package,
    cleanup_verified_capsule,
    retry_capsule_cleanup,
    start_capsule_cleanup_supervisor,
    verify_capsule_from_entry,
    wait_for_parent_eof,
    write_capsule_cleanup_journal,
)

CAPSULE_ID = "12345678-1234-4234-8234-123456789abc"


def create_source(root: Path) -> Path:
    source = root / "source"
    (source / "blueprints").mkdir(parents=True)
    package_source = Path(__file__).parents[2] / "src" / "scaffold_compiler"
    with zipfile.ZipFile(source / "scaffold_compiler.pyz", "w") as archive:
        for archive_path, content in (
            ("__main__.py", b"raise SystemExit(0)\n"),
            ("scaffold_compiler/__init__.py", (package_source / "__init__.py").read_bytes()),
            (
                "scaffold_compiler/capsule_package.py",
                (package_source / "capsule_package.py").read_bytes(),
            ),
            (
                "scaffold_compiler/path_safety.py",
                (package_source / "path_safety.py").read_bytes(),
            ),
        ):
            info = zipfile.ZipInfo(archive_path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    (source / "blueprints/catalog.json").write_text('{"version":1}\n', encoding="utf-8")
    (source / "LICENSE").write_text("license\n", encoding="utf-8")
    return source


def build_capsule(root: Path, name: str = "capsule") -> Path:
    source = create_source(root)
    return build_capsule_package(
        source,
        root / name,
        included_paths=(
            "LICENSE",
            "blueprints/catalog.json",
            "scaffold_compiler.pyz",
        ),
        capsule_id=CAPSULE_ID,
        compiler_version="0.1.0",
    )


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class CapsuleBuildAndOwnershipTests(unittest.TestCase):
    def test_repeated_build_with_one_release_identity_is_byte_deterministic(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = create_source(root)
            first = build_capsule_package(
                source,
                root / "capsule-a",
                included_paths=(
                    "scaffold_compiler.pyz",
                    "LICENSE",
                    "blueprints/catalog.json",
                ),
                capsule_id=CAPSULE_ID,
                compiler_version="0.1.0",
            )
            second = build_capsule_package(
                source,
                root / "capsule-b",
                included_paths=(
                    "LICENSE",
                    "blueprints/catalog.json",
                    "scaffold_compiler.pyz",
                ),
                capsule_id=CAPSULE_ID,
                compiler_version="0.1.0",
            )

            self.assertEqual(tree_bytes(first), tree_bytes(second))
            verified = verify_capsule_from_entry(first / "scaffold_compiler.pyz")
            self.assertEqual(verified.root, first)
            self.assertEqual(verified.capsule_id, CAPSULE_ID)

    def test_build_failure_removes_only_its_partial_destination_and_preserves_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = create_source(root)
            destination = root / "capsule"
            write_file = capsule_package._write_new_file
            writes = 0

            def fail_after_second_write(path: Path, content: bytes) -> None:
                nonlocal writes
                write_file(path, content)
                writes += 1
                if writes == 2:
                    raise OSError("primary build failure")

            with (
                patch.object(capsule_package, "_write_new_file", fail_after_second_write),
                self.assertRaisesRegex(OSError, "primary build failure"),
            ):
                build_capsule_package(
                    source,
                    destination,
                    included_paths=(
                        "LICENSE",
                        "blueprints/catalog.json",
                        "scaffold_compiler.pyz",
                    ),
                    capsule_id=CAPSULE_ID,
                    compiler_version="0.1.0",
                )

            self.assertFalse(destination.exists())
            self.assertTrue(source.exists())

    def test_development_repository_is_never_accepted_as_a_disposable_capsule(self) -> None:
        with TemporaryDirectory() as directory:
            source = create_source(Path(directory))
            (source / ".git").mkdir()

            with self.assertRaises(DevelopmentSourceError):
                verify_capsule_from_entry(source / "scaffold_compiler.pyz")

            self.assertTrue((source / "scaffold_compiler.pyz").exists())
            self.assertTrue((source / ".git").exists())

    def test_damaged_manifest_extra_entry_or_digest_mismatch_causes_zero_deletions(self) -> None:
        mutations = ("manifest", "extra", "digest")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                root = Path(directory)
                capsule = build_capsule(root)
                if mutation == "manifest":
                    (capsule / "capsule_manifest.json").write_text("{", encoding="utf-8")
                elif mutation == "extra":
                    (capsule / "user-notes.txt").write_text("keep", encoding="utf-8")
                else:
                    (capsule / "LICENSE").write_text("changed", encoding="utf-8")
                before = tree_bytes(capsule)

                with self.assertRaises(CapsuleOwnershipError):
                    verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")

                self.assertEqual(tree_bytes(capsule), before)


class CapsuleCleanupTests(unittest.TestCase):
    def test_cleanup_deletes_only_manifest_entries_and_manifest_last(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            deleted: list[str] = []

            def recording_unlink(path: Path) -> None:
                deleted.append(path.name)
                path.unlink()

            result = cleanup_verified_capsule(verified, unlinker=recording_unlink)

            self.assertTrue(result.completed)
            self.assertEqual(deleted[-1], "capsule_manifest.json")
            self.assertFalse(capsule.exists())

    def test_partial_cleanup_can_retry_but_still_refuses_new_unowned_content(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            failed_once = False

            def fail_once(path: Path) -> None:
                nonlocal failed_once
                if path.name == "LICENSE" and not failed_once:
                    failed_once = True
                    raise PermissionError("simulated lock")
                path.unlink()

            first = cleanup_verified_capsule(verified, unlinker=fail_once)
            self.assertFalse(first.completed)
            self.assertTrue((capsule / "capsule_manifest.json").exists())

            (capsule / "user-notes.txt").write_text("preserve", encoding="utf-8")
            refused = retry_capsule_cleanup(verified)
            self.assertFalse(refused.completed)
            self.assertTrue((capsule / "user-notes.txt").exists())
            (capsule / "user-notes.txt").unlink()

            completed = retry_capsule_cleanup(verified)
            self.assertTrue(completed.completed)
            self.assertFalse(capsule.exists())

    def test_cleanup_preserves_file_changed_after_preflight_verification(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")

            def mutate_after_first_unlink(path: Path) -> None:
                path.unlink()
                if path.name == "LICENSE":
                    (capsule / "blueprints/catalog.json").write_text(
                        "user change\n",
                        encoding="utf-8",
                    )

            result = cleanup_verified_capsule(verified, unlinker=mutate_after_first_unlink)

            self.assertFalse(result.completed)
            self.assertIn("blueprints/catalog.json", result.failed_entries)
            self.assertEqual(
                (capsule / "blueprints/catalog.json").read_text(encoding="utf-8"),
                "user change\n",
            )
            self.assertTrue((capsule / "scaffold_compiler.pyz").exists())
            self.assertTrue((capsule / "capsule_manifest.json").exists())

    def test_supervisor_waits_for_pipe_eof_before_cleanup_callback(self) -> None:
        read_fd, write_fd = os.pipe()
        called = threading.Event()
        thread = threading.Thread(
            target=wait_for_parent_eof,
            args=(read_fd, called.set),
            daemon=True,
        )
        thread.start()
        time.sleep(0.05)
        self.assertFalse(called.is_set())

        os.close(write_fd)
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(called.is_set())

    def test_external_supervisor_cleans_only_after_parent_signal_closes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            workspace = root / "workspace"
            workspace.mkdir()
            journal_path = workspace / "capsule_cleanup_journal.json"
            write_capsule_cleanup_journal(verified, journal_path)
            supervisor = start_capsule_cleanup_supervisor(
                verified,
                journal_path=journal_path,
                interpreter=Path(sys.executable),
                working_directory=root,
            )
            time.sleep(0.1)
            self.assertTrue(capsule.exists())
            self.assertIsNone(supervisor.process.poll())

            supervisor.signal_parent_exit()
            exit_code = supervisor.process.wait(timeout=5)

            self.assertEqual(exit_code, 0)
            self.assertFalse(capsule.exists())
            self.assertFalse(workspace.exists())

    def test_supervisor_refuses_a_tampered_cleanup_journal_without_deleting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            workspace = root / "workspace"
            workspace.mkdir()
            journal_path = workspace / "capsule_cleanup_journal.json"
            write_capsule_cleanup_journal(verified, journal_path)
            journal_path.write_text("{}\n", encoding="utf-8")

            with self.assertRaises(CapsuleOwnershipError):
                start_capsule_cleanup_supervisor(
                    verified,
                    journal_path=journal_path,
                    interpreter=Path(sys.executable),
                    working_directory=root,
                )

            self.assertTrue(capsule.exists())
            self.assertTrue(journal_path.exists())

    def test_supervisor_preserves_journal_when_cleanup_workspace_is_not_empty(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            workspace = root / "workspace"
            workspace.mkdir()
            journal_path = workspace / "capsule_cleanup_journal.json"
            write_capsule_cleanup_journal(verified, journal_path)
            (workspace / "diagnostics.txt").write_text("keep\n", encoding="utf-8")
            supervisor = start_capsule_cleanup_supervisor(
                verified,
                journal_path=journal_path,
                interpreter=Path(sys.executable),
                working_directory=root,
            )

            supervisor.signal_parent_exit()
            exit_code = supervisor.process.wait(timeout=5)

            self.assertEqual(exit_code, 1)
            self.assertFalse(capsule.exists())
            self.assertTrue(journal_path.exists())
            self.assertTrue((workspace / "diagnostics.txt").exists())

    def test_supervisor_refuses_interpreter_or_working_directory_inside_capsule(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_capsule(root)
            verified = verify_capsule_from_entry(capsule / "scaffold_compiler.pyz")
            workspace = root / "workspace"
            workspace.mkdir()
            journal_path = workspace / "capsule_cleanup_journal.json"
            write_capsule_cleanup_journal(verified, journal_path)

            for interpreter, working_directory in (
                (capsule / "scaffold_compiler.pyz", root),
                (Path(sys.executable), capsule),
            ):
                with (
                    self.subTest(
                        interpreter=interpreter,
                        working_directory=working_directory,
                    ),
                    self.assertRaises(ValueError),
                ):
                    start_capsule_cleanup_supervisor(
                        verified,
                        journal_path=journal_path,
                        interpreter=interpreter,
                        working_directory=working_directory,
                    )


if __name__ == "__main__":
    unittest.main()
