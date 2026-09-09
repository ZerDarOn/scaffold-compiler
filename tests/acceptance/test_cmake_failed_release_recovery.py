from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.disposable_release_builder import build_disposable_release

CMAKE_RECOVERY_CAPSULE_ID = "24681357-1357-4246-9135-246813572468"


class CMakeFailedReleaseRecoveryTests(unittest.TestCase):
    def test_missing_toolchain_preserves_evidence_for_exact_idempotent_recovery(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_disposable_release(
                repository,
                root / "capsule",
                capsule_id=CMAKE_RECOVERY_CAPSULE_ID,
                compiler_version="1.0.0",
            )
            target = root / "delivery"
            sibling = root / "preserve"
            sibling.mkdir()
            (sibling / "keep.txt").write_text("keep\n", encoding="utf-8")
            config = root / "project.json"
            config.write_text(
                json.dumps(
                    {
                        "answers": {
                            "strict_warnings": True,
                            "target_name": "recovery_cli",
                        },
                        "project_name": "Recovery CLI",
                        "recipe": "c-cmake-cli",
                        "schema_version": 2,
                        "target_directory": str(target),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SCAFFOLD_COMPILER_CMAKE"] = str(root / "missing-cmake")
            environment["SCAFFOLD_COMPILER_CTEST"] = str(root / "missing-ctest")

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
                env=environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
            )

            self.assertEqual(failed.returncode, 1)
            self.assertFalse(target.exists())
            self.assertTrue(capsule.exists())
            workspaces = tuple(root.glob(".scw-*"))
            self.assertEqual(len(workspaces), 1)
            workspace = workspaces[0]
            self.assertIn(workspace.name, failed.stderr)

            inspected = self.run_recovery_command(
                capsule,
                root,
                environment,
                "inspect",
                "--workspace",
                str(workspace),
            )

            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            inspection = json.loads(inspected.stdout)
            self.assertEqual(inspection["state"], "failed_retryable")
            self.assertEqual(inspection["failed_stage"], "verify")
            self.assertTrue(inspection["evidence_valid"])
            self.assertEqual(
                set(inspection["failed_gates"]),
                {"cmake-configure", "cmake-build", "ctest", "executable-run"},
            )

            discarded = self.run_recovery_command(
                capsule,
                root,
                environment,
                "discard",
                "--workspace",
                str(workspace),
                "--confirm",
                "DISCARD",
            )
            repeated = self.run_recovery_command(
                capsule,
                root,
                environment,
                "discard",
                "--workspace",
                str(workspace),
                "--confirm",
                "DISCARD",
            )

            self.assertEqual(discarded.returncode, 0, discarded.stderr)
            self.assertEqual(repeated.returncode, 1)
            self.assertFalse(workspace.exists())
            self.assertFalse(target.exists())
            self.assertEqual((sibling / "keep.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertTrue(capsule.exists())

    @staticmethod
    def run_recovery_command(
        capsule: Path,
        root: Path,
        environment: dict[str, str],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (sys.executable, str(capsule / "scaffold_compiler.pyz"), *arguments),
            cwd=root,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )


if __name__ == "__main__":
    unittest.main()
