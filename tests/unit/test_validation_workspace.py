from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.validation_workspace import (
    ValidationWorkspaceError,
    cleanup_validation_workspace,
    prepare_validation_workspace,
)


class ValidationWorkspaceTests(unittest.TestCase):
    def test_prepares_a_fixed_marked_child_and_cleans_owned_contents(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run-1"
            workspace.mkdir()
            owned = prepare_validation_workspace(workspace, run_id="run-1")
            (owned.root / "venv" / "site-packages").mkdir(parents=True)
            (owned.root / "venv" / "site-packages" / "dependency.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )

            result = cleanup_validation_workspace(owned)

            self.assertEqual(owned.root, workspace / "validation-env")
            self.assertTrue(result.completed)
            self.assertFalse(owned.root.exists())
            self.assertTrue(workspace.exists())

    def test_wrong_or_changed_marker_causes_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run-1"
            workspace.mkdir()
            owned = prepare_validation_workspace(workspace, run_id="run-1")
            dependency = owned.root / "dependency.bin"
            dependency.write_bytes(b"preserve")
            marker = owned.root / ".validation-workspace.json"
            record = json.loads(marker.read_text(encoding="utf-8"))
            record["run_id"] = "another-run"
            marker.write_text(json.dumps(record), encoding="utf-8")

            result = cleanup_validation_workspace(owned)

            self.assertFalse(result.completed)
            self.assertTrue(dependency.exists())
            self.assertTrue(marker.exists())

    def test_linked_descendant_causes_zero_deletions(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run-1"
            workspace.mkdir()
            owned = prepare_validation_workspace(workspace, run_id="run-1")
            dependency = owned.root / "dependency.bin"
            dependency.write_bytes(b"preserve")

            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path.name == "dependency.bin",
            ):
                result = cleanup_validation_workspace(owned)

            self.assertFalse(result.completed)
            self.assertEqual(dependency.read_bytes(), b"preserve")

    def test_cleanup_is_retryable_and_deletes_marker_last(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run-1"
            workspace.mkdir()
            owned = prepare_validation_workspace(workspace, run_id="run-1")
            first_file = owned.root / "cache" / "first.bin"
            second_file = owned.root / "cache" / "second.bin"
            first_file.parent.mkdir()
            first_file.write_bytes(b"first")
            second_file.write_bytes(b"second")
            failed_once = False
            deletion_order: list[str] = []

            def fail_once(path: Path) -> None:
                nonlocal failed_once
                deletion_order.append(path.name)
                if path.name == "second.bin" and not failed_once:
                    failed_once = True
                    raise PermissionError("simulated lock")
                path.unlink()

            first = cleanup_validation_workspace(owned, unlinker=fail_once)
            self.assertFalse(first.completed)
            self.assertTrue((owned.root / ".validation-workspace.json").exists())
            self.assertNotIn(".validation-workspace.json", deletion_order)

            second = cleanup_validation_workspace(owned)
            self.assertTrue(second.completed)
            self.assertFalse(owned.root.exists())

    def test_refuses_a_workspace_root_outside_the_fixed_owned_child(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run-1"
            workspace.mkdir()
            owned = prepare_validation_workspace(workspace, run_id="run-1")
            object.__setattr__(owned, "root", workspace.parent)

            with self.assertRaises(ValidationWorkspaceError):
                cleanup_validation_workspace(owned)


if __name__ == "__main__":
    unittest.main()
