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

CMAKE_CAPSULE_ID = "87654321-4321-4678-9234-567812345678"


class CMakeDisposableReleaseWorkflowTests(unittest.TestCase):
    def test_cmake_recipe_builds_then_removes_generator_and_validation_artifacts(self) -> None:
        cmake = shutil.which("cmake")
        ctest = shutil.which("ctest")
        ninja = shutil.which("ninja")
        compiler = next(
            (path for name in ("cc", "gcc", "clang", "cl") if (path := shutil.which(name))),
            None,
        )
        if None in {cmake, ctest, ninja, compiler}:
            self.skipTest("CMake, CTest, Ninja, and a C compiler are required")

        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_disposable_release(
                repository,
                root / "capsule",
                capsule_id=CMAKE_CAPSULE_ID,
                compiler_version="1.0.0",
            )
            target = root / "delivery"
            config = root / "project.json"
            config.write_text(
                json.dumps(
                    {
                        "answers": {
                            "strict_warnings": True,
                            "target_name": "release_c_cli",
                        },
                        "project_name": "Release C CLI",
                        "recipe": "c-cmake-cli",
                        "schema_version": 2,
                        "target_directory": str(target),
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SCAFFOLD_COMPILER_CMAKE"] = str(cmake)
            environment["SCAFFOLD_COMPILER_CTEST"] = str(ctest)

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
            self.assertTrue((target / "src" / "main.c").is_file())
            self.assertTrue((target / "cmake" / "StrictWarnings.cmake").is_file())
            self.assertFalse((target / "build").exists())
            cleanup_root = root / f".scaffold-capsule-cleanup-{CMAKE_CAPSULE_ID}"
            deadline = time.monotonic() + 15
            while (capsule.exists() or cleanup_root.exists()) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(capsule.exists(), "disposable generator was not removed")
            self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")
            self.assertFalse(any("scaffold" in path.name for path in target.rglob("*")))


if __name__ == "__main__":
    unittest.main()
