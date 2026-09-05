from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "12345678-1234-4678-9234-567812345678"


class DisposableReleaseWorkflowTests(unittest.TestCase):
    def test_m01_runs_from_capsule_then_removes_generator_and_journal(self) -> None:
        repository = Path(__file__).parents[2]
        uv_executable = Path(sys.executable).with_name("uv.exe" if os.name == "nt" else "uv")
        self.assertTrue(uv_executable.is_file(), "project environment must contain uv")
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
            config.write_text(
                json.dumps(
                    {
                        "project_name": "Release Acceptance API",
                        "package_name": "release_acceptance",
                        "target_directory": str(target),
                        "database": "none",
                        "container": "none",
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SCAFFOLD_COMPILER_UV"] = str(uv_executable)

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
                timeout=360,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Project finalized successfully", completed.stdout)
            self.assertTrue((target / "src" / "release_acceptance" / "asgi.py").is_file())
            cleanup_root = root / f".scaffold-capsule-cleanup-{CAPSULE_ID}"
            deadline = time.monotonic() + 15
            while (capsule.exists() or cleanup_root.exists()) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(capsule.exists(), "disposable generator was not removed")
            self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")
            self.assertFalse(any("scaffold" in path.name for path in target.rglob("*")))


if __name__ == "__main__":
    unittest.main()
