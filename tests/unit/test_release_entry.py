from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import Mock, patch

from scaffold_compiler.capsule_package import build_capsule_package
from scaffold_compiler.command_line_interface import CommandOutcome, ScaffoldCommandService
from scaffold_compiler.release_entry import main, resolve_uv_executable

CAPSULE_ID = "12345678-1234-4678-9234-567812345678"


class SuccessfulRunApplication:
    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        return CommandOutcome(0, "completed")

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected command: {name}")


class ReleaseEntryTests(unittest.TestCase):
    def test_successful_packaged_run_arms_external_cleanup_before_reporting_success(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule_root = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            entry = capsule_root / "scaffold_compiler.pyz"
            stdout = io.StringIO()
            stderr = io.StringIO()
            supervisor = Mock()

            with patch(
                "scaffold_compiler.release_entry.start_capsule_cleanup_supervisor",
                return_value=supervisor,
            ) as start_supervisor:
                exit_code = main(
                    [
                        "run",
                        "--config",
                        "project.json",
                        "--non-interactive",
                        "--confirm-finalize",
                        "FINALIZE",
                    ],
                    entry_path=entry,
                    application=cast(ScaffoldCommandService, SuccessfulRunApplication()),
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue(), "completed\n")
            self.assertEqual(stderr.getvalue(), "")
            cleanup_root = root / f".scaffold-capsule-cleanup-{CAPSULE_ID}"
            journal = cleanup_root / "capsule_cleanup_journal.json"
            self.assertTrue(journal.is_file())
            self.assertEqual(start_supervisor.call_args.kwargs["journal_path"], journal)

    def test_failed_command_does_not_arm_or_delete_capsule(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule_root = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            application = Mock()
            application.run_non_interactive.return_value = CommandOutcome(1, "failed")

            with patch(
                "scaffold_compiler.release_entry.start_capsule_cleanup_supervisor"
            ) as start_supervisor:
                exit_code = main(
                    [
                        "run",
                        "--config",
                        "project.json",
                        "--non-interactive",
                        "--confirm-finalize",
                        "FINALIZE",
                    ],
                    entry_path=capsule_root / "scaffold_compiler.pyz",
                    application=application,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

            self.assertEqual(exit_code, 1)
            start_supervisor.assert_not_called()
            self.assertTrue(capsule_root.exists())

    def test_uv_resolution_accepts_only_an_existing_ordinary_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv"
            uv.write_bytes(b"uv")

            self.assertEqual(
                resolve_uv_executable({"SCAFFOLD_COMPILER_UV": str(uv)}),
                uv.resolve(),
            )
            self.assertIsNone(
                resolve_uv_executable({"SCAFFOLD_COMPILER_UV": str(root / "missing")})
            )

    def test_tampered_capsule_fails_without_a_traceback_or_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            (capsule / "scaffold_compiler.pyz").write_bytes(b"tampered")
            stderr = io.StringIO()

            exit_code = main(
                ["--help"],
                entry_path=capsule / "scaffold_compiler.pyz",
                application=cast(ScaffoldCommandService, SuccessfulRunApplication()),
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 1)
            self.assertIn("ownership verification failed", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertTrue(capsule.exists())


if __name__ == "__main__":
    unittest.main()
