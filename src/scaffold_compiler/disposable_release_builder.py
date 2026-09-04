"""Deterministic zipapp and disposable release-capsule construction."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Final

from scaffold_compiler.capsule_package import build_capsule_package
from scaffold_compiler.path_safety import resolve_safe_output_path

_ZIP_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
_ROOT_ENTRY = (
    b"from scaffold_compiler.release_entry import main\n"
    b"raise SystemExit(main())\n"
)


def build_disposable_release(
    source_root: Path,
    destination_root: Path,
    *,
    capsule_id: str,
    compiler_version: str,
) -> Path:
    """Build a deterministic runtime archive and externally readable blueprints."""
    source = source_root.resolve(strict=True)
    package_root = source / "src" / "scaffold_compiler"
    blueprint_root = source / "blueprints"
    if not package_root.is_dir() or not blueprint_root.is_dir():
        raise ValueError("Release source does not contain runtime and blueprint roots.")
    destination = destination_root.absolute()
    if not destination.parent.is_dir():
        raise ValueError("Release destination parent must already exist.")

    with tempfile.TemporaryDirectory(
        prefix=".scaffold-release-stage-",
        dir=destination.parent,
    ) as temporary_directory:
        staging = Path(temporary_directory)
        entry = staging / "scaffold_compiler.pyz"
        _build_runtime_zipapp(package_root, entry)
        included_paths = ["scaffold_compiler.pyz"]
        for source_path in sorted(blueprint_root.rglob("*")):
            if source_path.is_dir():
                continue
            relative = source_path.relative_to(source).as_posix()
            safe_source = resolve_safe_output_path(source, relative)
            if not safe_source.is_file():
                raise ValueError("Blueprint release inputs must be ordinary files.")
            destination_path = staging / relative
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            destination_path.write_bytes(safe_source.read_bytes())
            included_paths.append(relative)
        return build_capsule_package(
            staging,
            destination,
            included_paths=tuple(included_paths),
            capsule_id=capsule_id,
            compiler_version=compiler_version,
        )


def _build_runtime_zipapp(package_root: Path, destination: Path) -> None:
    source_files = tuple(sorted(package_root.glob("*.py")))
    if not source_files:
        raise ValueError("Release runtime contains no Python modules.")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        _write_zip_entry(archive, "__main__.py", _ROOT_ENTRY)
        for source_file in source_files:
            if not source_file.is_file() or source_file.is_symlink():
                raise ValueError("Runtime release inputs must be ordinary files.")
            _write_zip_entry(
                archive,
                f"scaffold_compiler/{source_file.name}",
                source_file.read_bytes(),
            )


def _write_zip_entry(archive: zipfile.ZipFile, path: str, content: bytes) -> None:
    information = zipfile.ZipInfo(path, date_time=_ZIP_TIMESTAMP)
    information.compress_type = zipfile.ZIP_STORED
    information.create_system = 3
    information.external_attr = 0o100644 << 16
    archive.writestr(information, content)
