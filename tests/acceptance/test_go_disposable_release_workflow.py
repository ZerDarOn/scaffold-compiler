from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.disposable_release_builder import build_disposable_release

GO_CAPSULE_ID = "23456781-2345-4678-9234-567812345678"


class GoDisposableReleaseWorkflowTests(unittest.TestCase):
    def test_go_recipe_runs_from_init_to_finalize_then_removes_generator(self) -> None:
        go = shutil.which("go")
        gofmt = shutil.which("gofmt")
        if go is None or gofmt is None:
            self.skipTest("Go and gofmt are required")

        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_disposable_release(
                repository,
                root / "capsule",
                capsule_id=GO_CAPSULE_ID,
                compiler_version="2.2.0-rc.1",
            )
            target = root / ("delivery-" + "x" * 80)
            config = root / "project.json"
            initialized = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "init",
                    "--output",
                    str(config),
                ),
                cwd=root,
                input=f"3\nRelease Go CLI\n{target}\nrelease-go\nexample.com/release-go\n",
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertFalse(target.exists())

            previewed = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "preview",
                    "--config",
                    str(config),
                ),
                cwd=root,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            self.assertEqual(previewed.returncode, 0, previewed.stderr)
            preview = json.loads(previewed.stdout)
            self.assertEqual(preview["recipe"], "go-cli")
            self.assertEqual(
                preview["validations"],
                ["go-build", "go-executable-run", "go-format", "go-test"],
            )
            environment = os.environ.copy()
            environment["SCAFFOLD_COMPILER_GO"] = go
            environment["SCAFFOLD_COMPILER_GOFMT"] = gofmt
            completed = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "run",
                    "--config",
                    str(config),
                    "--non-interactive",
                    "--confirm-finalize",
                    "FINALIZE",
                ),
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=240,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((target / "main.go").is_file())
            self.assertTrue((target / "calculator_test.go").is_file())
            self.assertFalse((target / "build").exists())
            self.assertFalse((target / "bin").exists())
            cleanup_root = root / f".scaffold-capsule-cleanup-{GO_CAPSULE_ID}"
            deadline = time.monotonic() + 15
            while (capsule.exists() or cleanup_root.exists()) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(capsule.exists(), "disposable generator was not removed")
            self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")
            self.assertEqual(tuple(root.glob(".scw-*")), ())
            self.assertFalse(any("scaffold" in path.name for path in target.rglob("*")))


if __name__ == "__main__":
    unittest.main()
