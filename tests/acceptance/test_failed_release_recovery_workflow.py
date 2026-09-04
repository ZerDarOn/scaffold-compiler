from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "13572468-2468-4135-9135-246813572468"


class FailedReleaseRecoveryWorkflowTests(unittest.TestCase):
    def test_failed_run_can_be_inspected_and_exactly_discarded_without_uv(self) -> None:
        repository = Path(__file__).parents[2]
        suffix = ".exe" if os.name == "nt" else ""
        uv_executable = Path(sys.executable).with_name(f"uv{suffix}")
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
                        "project_name": "Failed Recovery API",
                        "package_name": "failed_recovery",
                        "target_directory": str(target),
                        "database": "postgres",
                        "container": "none",
                    }
                ),
                encoding="utf-8",
            )
            run_environment = os.environ.copy()
            run_environment["SCAFFOLD_COMPILER_UV"] = str(uv_executable)
            run_environment.pop("DATABASE_URL", None)

            failed = subprocess.run(
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
                env=run_environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=360,
            )

            self.assertEqual(failed.returncode, 1)
            self.assertFalse(target.exists())
            self.assertTrue(capsule.exists())
            workspaces = tuple(root.glob(".delivery.scaffold-*"))
            self.assertEqual(len(workspaces), 1)
            workspace = workspaces[0]
            self.assertIn(workspace.name, failed.stderr)
            recovery_environment = os.environ.copy()
            recovery_environment.pop("SCAFFOLD_COMPILER_UV", None)
            recovery_environment.pop("DATABASE_URL", None)

            inspected = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "inspect",
                    "--workspace",
                    str(workspace),
                ),
                cwd=root,
                env=recovery_environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )

            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            inspection = json.loads(inspected.stdout)
            self.assertEqual(inspection["state"], "failed_retryable")
            self.assertEqual(inspection["failed_stage"], "verify")
            self.assertTrue(inspection["evidence_valid"])
            self.assertIn("postgres-connect", inspection["failed_gates"])

            discarded = subprocess.run(
                (
                    sys.executable,
                    str(capsule / "scaffold_compiler.pyz"),
                    "discard",
                    "--workspace",
                    str(workspace),
                    "--confirm",
                    "DISCARD",
                ),
                cwd=root,
                env=recovery_environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
            )

            self.assertEqual(discarded.returncode, 0, discarded.stderr)
            self.assertFalse(workspace.exists())
            self.assertFalse(target.exists())
            self.assertTrue(capsule.exists())


if __name__ == "__main__":
    unittest.main()
