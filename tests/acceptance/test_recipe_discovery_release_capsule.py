from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.release_capsule_command import build_release_capsule


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class RecipeDiscoveryReleaseCapsuleTests(unittest.TestCase):
    def test_source_and_capsule_report_identical_schema_without_side_effects(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capsule = build_release_capsule(repository, root / "capsule")
            environment = os.environ | {
                "DATABASE_URL": "private-database-value",
                "SCAFFOLD_COMPILER_UV": str(root / "private-uv"),
                "SCAFFOLD_COMPILER_GO": str(root / "private-go"),
            }
            before = _snapshot_files(root)

            source = subprocess.run(
                (sys.executable, "-m", "scaffold_compiler", "recipes"),
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            packaged = subprocess.run(
                (sys.executable, str(capsule / "scaffold_compiler.pyz"), "recipes"),
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(packaged.stdout, source.stdout)
            self.assertEqual(packaged.stderr, source.stderr)
            catalog = json.loads(packaged.stdout)
            self.assertEqual(catalog["schema_version"], 2)
            self.assertEqual(
                [[item["key"] for item in recipe["inputs"]] for recipe in catalog["recipes"]],
                [
                    ["package_name", "database", "delivery"],
                    ["target_name", "strict_warnings"],
                    ["binary_name", "module_path"],
                ],
            )
            self.assertNotIn("private", packaged.stdout)
            self.assertEqual(_snapshot_files(root), before)


if __name__ == "__main__":
    unittest.main()
