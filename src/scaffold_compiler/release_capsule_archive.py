"""Deterministic release assets for one verified disposable capsule."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path
from typing import Final

from scaffold_compiler.capsule_package import verify_capsule_from_entry

_ARCHIVE_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
_CHECKSUM_SUFFIX: Final = ".sha256"
_ENTRY_NAME: Final = "scaffold_compiler.pyz"
_MANIFEST_NAME: Final = "capsule_manifest.json"


def build_release_capsule_archive(capsule_root: Path) -> tuple[Path, Path]:
    """Create byte-stable ZIP and SHA-256 assets without overwriting existing files."""
    root = capsule_root.resolve(strict=True)
    entry_path = root / _ENTRY_NAME
    verified = verify_capsule_from_entry(entry_path)
    expected_name = f"scaffold-compiler-{verified.compiler_version}"
    if root.name != expected_name:
        raise ValueError(f"Release capsule directory must be named {expected_name}.")

    archive_path = root.parent / f"{root.name}.zip"
    checksum_path = root.parent / f"{archive_path.name}{_CHECKSUM_SUFFIX}"
    if archive_path.exists() or checksum_path.exists():
        raise FileExistsError("Release assets already exist.")

    archive_created = False
    try:
        with archive_path.open("xb") as output:
            archive_created = True
            with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
                relative_paths = sorted(
                    [record.path for record in verified.files] + [_MANIFEST_NAME]
                )
                for relative_path in relative_paths:
                    content = (root / relative_path).read_bytes()
                    archive.writestr(_archive_entry(root.name, relative_path), content)

        if verify_capsule_from_entry(entry_path) != verified:
            raise ValueError("Release capsule changed while its archive was being built.")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        with checksum_path.open("x", encoding="utf-8", newline="\n") as checksum:
            checksum.write(f"{digest}  {archive_path.name}\n")
    except Exception:
        if archive_created:
            archive_path.unlink(missing_ok=True)
        raise
    return archive_path, checksum_path


def _archive_entry(root_name: str, relative_path: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(
        filename=f"{root_name}/{relative_path}",
        date_time=_ARCHIVE_TIMESTAMP,
    )
    entry.compress_type = zipfile.ZIP_STORED
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o644) << 16
    return entry
