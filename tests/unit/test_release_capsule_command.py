from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler import __version__
from scaffold_compiler.capsule_package import verify_capsule_from_entry
from scaffold_compiler.release_capsule_command import (
    build_release_capsule,
    main,
    release_capsule_id,
)


class ReleaseCapsuleCommandTests(unittest.TestCase):
    def test_builds_a_version_bound_verified_capsule_deterministically(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            destination = Path(directory) / f"scaffold-compiler-{__version__}"
            stdout = io.StringIO()
            stderr = io.StringIO()

            exit_code = main(
                ("--destination", str(destination)),
                source_root=repository,
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0, stderr.getvalue())
            verified = verify_capsule_from_entry(destination / "scaffold_compiler.pyz")
            self.assertEqual(verified.compiler_version, __version__)
            self.assertEqual(verified.capsule_id, release_capsule_id(__version__))
            self.assertEqual(stdout.getvalue(), f"{destination.resolve()}\n")
            self.assertEqual(stderr.getvalue(), "")

    def test_refuses_to_overwrite_an_existing_release(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            destination = Path(directory) / f"scaffold-compiler-{__version__}"
            build_release_capsule(repository, destination)
            manifest = destination / "capsule_manifest.json"
            original = manifest.read_bytes()
            stderr = io.StringIO()

            exit_code = main(
                ("--destination", str(destination)),
                source_root=repository,
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 1)
            self.assertEqual(manifest.read_bytes(), original)
            self.assertIn("could not be built", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
