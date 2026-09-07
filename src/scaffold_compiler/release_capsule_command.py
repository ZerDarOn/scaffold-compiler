"""Maintainer command for building one versioned disposable release capsule."""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from scaffold_compiler import __version__
from scaffold_compiler.capsule_package import verify_capsule_from_entry
from scaffold_compiler.disposable_release_builder import build_disposable_release
from scaffold_compiler.release_capsule_archive import build_release_capsule_archive


def release_capsule_id(version: str) -> str:
    """Derive a stable UUIDv4-form identifier from one compiler version."""
    digest = hashlib.sha256(f"scaffold-compiler:{version}".encode()).hexdigest()
    return str(uuid.UUID(digest[:32], version=4))


def build_release_capsule(source_root: Path, destination: Path) -> Path:
    """Build and immediately verify one immutable versioned capsule."""
    built = build_disposable_release(
        source_root,
        destination,
        capsule_id=release_capsule_id(__version__),
        compiler_version=__version__,
    )
    verified = verify_capsule_from_entry(built / "scaffold_compiler.pyz")
    if verified.compiler_version != __version__ or verified.capsule_id != release_capsule_id(
        __version__
    ):
        raise ValueError("Built release capsule identity verification failed.")
    return built


def main(
    arguments: Sequence[str] | None = None,
    *,
    source_root: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Build one release capsule without overwriting an existing destination."""
    parser = argparse.ArgumentParser(prog="scaffold-compiler-release")
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument(
        "--archive",
        action="store_true",
        help="also create deterministic ZIP and SHA-256 release assets",
    )
    parsed = parser.parse_args(arguments)
    selected_source = Path(__file__).resolve().parents[2] if source_root is None else source_root
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    try:
        built = build_release_capsule(selected_source, parsed.destination)
        assets = build_release_capsule_archive(built) if parsed.archive else ()
    except (OSError, ValueError) as error:
        selected_stderr.write(f"Release capsule could not be built: {error}\n")
        return 1
    selected_stdout.write(f"{built.resolve()}\n")
    for asset in assets:
        selected_stdout.write(f"{asset.resolve()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
