from __future__ import annotations

import hashlib
import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler import __version__
from scaffold_compiler.capsule_package import verify_capsule_from_entry
from scaffold_compiler.release_capsule_archive import build_release_capsule_archive
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

    def test_builds_a_deterministic_archive_and_checksum(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as first_directory, TemporaryDirectory() as second_directory:
            first_capsule = build_release_capsule(
                repository,
                Path(first_directory) / f"scaffold-compiler-{__version__}",
            )
            second_capsule = build_release_capsule(
                repository,
                Path(second_directory) / f"scaffold-compiler-{__version__}",
            )

            first_archive, first_checksum = build_release_capsule_archive(first_capsule)
            second_archive, second_checksum = build_release_capsule_archive(second_capsule)

            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_checksum.read_text(), second_checksum.read_text())
            digest = hashlib.sha256(first_archive.read_bytes()).hexdigest()
            self.assertEqual(
                first_checksum.read_text(encoding="utf-8"),
                f"{digest}  {first_archive.name}\n",
            )
            with zipfile.ZipFile(first_archive) as archive:
                names = archive.namelist()
                self.assertEqual(names, sorted(names))
                self.assertTrue(
                    all(name.startswith(f"scaffold-compiler-{__version__}/") for name in names)
                )
                self.assertIn(
                    f"scaffold-compiler-{__version__}/capsule_manifest.json",
                    names,
                )

    def test_archive_refuses_to_overwrite_existing_release_assets(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            capsule = build_release_capsule(
                repository,
                Path(directory) / f"scaffold-compiler-{__version__}",
            )
            archive, checksum = build_release_capsule_archive(capsule)

            with self.assertRaises(FileExistsError):
                build_release_capsule_archive(capsule)

            self.assertTrue(archive.is_file())
            self.assertTrue(checksum.is_file())

    def test_command_can_build_release_assets_for_publication(self) -> None:
        repository = Path(__file__).parents[2]
        with TemporaryDirectory() as directory:
            destination = Path(directory) / f"scaffold-compiler-{__version__}"
            archive = destination.parent / f"{destination.name}.zip"
            checksum = destination.parent / f"{destination.name}.zip.sha256"
            stdout = io.StringIO()

            exit_code = main(
                ("--destination", str(destination), "--archive"),
                source_root=repository,
                stdout=stdout,
                stderr=io.StringIO(),
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                stdout.getvalue().splitlines(),
                [
                    str(destination.resolve()),
                    str(archive.resolve()),
                    str(checksum.resolve()),
                ],
            )
            self.assertTrue(archive.is_file())
            self.assertTrue(checksum.is_file())


if __name__ == "__main__":
    unittest.main()
