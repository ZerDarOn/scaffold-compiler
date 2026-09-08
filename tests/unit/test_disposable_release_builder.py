from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.capsule_package import verify_capsule_from_entry
from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "12345678-1234-4678-9234-567812345678"


class DisposableReleaseBuilderTests(unittest.TestCase):
    def test_build_is_byte_stable_and_contains_only_runtime_and_blueprints(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_disposable_release(
                repository,
                root / "first",
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            second = build_disposable_release(
                repository,
                root / "second",
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )

            first_entry = first / "scaffold_compiler.pyz"
            second_entry = second / "scaffold_compiler.pyz"
            self.assertEqual(
                hashlib.sha256(first_entry.read_bytes()).digest(),
                hashlib.sha256(second_entry.read_bytes()).digest(),
            )
            verified = verify_capsule_from_entry(first_entry)
            paths = {record.path for record in verified.files}
            self.assertIn("blueprints/fastapi_http_api/blueprint.json", paths)
            self.assertNotIn("pyproject.toml", paths)
            self.assertFalse(any("tests/" in path for path in paths))

    def test_fresh_release_entry_can_display_help(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            capsule = build_disposable_release(
                repository,
                Path(directory) / "capsule",
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )

            completed = subprocess.run(
                (sys.executable, str(capsule / "scaffold_compiler.pyz"), "--help"),
                cwd=Path(directory),
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("scaffold-compiler", completed.stdout)
            self.assertTrue(capsule.exists())

    def test_fresh_release_entry_can_initialize_configuration_without_self_cleanup(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_disposable_release(
                repository,
                root / "capsule",
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            target = root / "delivery"
            config = root / "project.json"

            completed = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "init",
                    "--output",
                    str(config),
                ),
                cwd=root,
                input=f"2\nExample CLI\n{target}\nexample_cli\ny\n",
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["recipe"], "c-cmake-cli"
            )
            self.assertFalse(target.exists())
            self.assertTrue(capsule.exists())


if __name__ == "__main__":
    unittest.main()
