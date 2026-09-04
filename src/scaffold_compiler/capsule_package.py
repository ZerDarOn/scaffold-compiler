"""Deterministic disposable capsule construction and exact ownership cleanup."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scaffold_compiler.path_safety import (
    resolve_safe_output_path,
    validate_unique_target_paths,
)

LOGGER = logging.getLogger(__name__)

_MANIFEST_NAME: Final = "capsule_manifest.json"
_ENTRY_NAME: Final = "scaffold_compiler.pyz"
_CLEANUP_JOURNAL_NAME: Final = "capsule_cleanup_journal.json"
_SCHEMA_VERSION: Final = 1
_VERSION_PATTERN: Final = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[a-z0-9.-]*)?$")


class CapsuleOwnershipError(ValueError):
    """Raised before deletion when capsule ownership cannot be proven exactly."""


class DevelopmentSourceError(CapsuleOwnershipError):
    """Raised when a source tree has development-repository markers."""


@dataclass(frozen=True, slots=True)
class CapsuleFileRecord:
    """Immutable ownership facts for one ordinary capsule file."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedCapsule:
    """A capsule whose root was derived from its actual running entry path."""

    root: Path
    entry_path: Path
    capsule_id: str
    compiler_version: str
    files: tuple[CapsuleFileRecord, ...]
    directories: tuple[str, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class CapsuleCleanupResult:
    """A retryable exact-cleanup result."""

    completed: bool
    failed_entries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapsuleCleanupJournal:
    """Frozen external facts authorizing one capsule cleanup attempt."""

    path: Path
    entry_path: Path
    capsule_id: str
    manifest_sha256: str


@dataclass(slots=True)
class CapsuleCleanupSupervisor:
    """External cleanup process and its parent-lifetime signal pipe."""

    process: subprocess.Popen[bytes]

    def signal_parent_exit(self) -> None:
        """Close the exclusive pipe explicitly; process exit closes it automatically."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()


def build_capsule_package(
    source_root: Path,
    destination_root: Path,
    *,
    included_paths: tuple[str, ...],
    capsule_id: str,
    compiler_version: str,
) -> Path:
    """Copy an explicit release set and write a byte-stable ownership manifest."""
    source = source_root.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Capsule source must be a directory.")
    destination = destination_root.absolute()
    if not destination.parent.is_dir():
        raise ValueError("Capsule destination parent must already exist.")
    if _paths_overlap(source, destination):
        raise ValueError("Capsule source and destination must not overlap.")
    _validate_capsule_id(capsule_id)
    if not _VERSION_PATTERN.fullmatch(compiler_version):
        raise ValueError("Compiler version is invalid.")
    validated_paths = tuple(sorted(validate_unique_target_paths(included_paths)))
    if validated_paths.count(_ENTRY_NAME) != 1:
        raise ValueError(f"Capsule must contain exactly one {_ENTRY_NAME} entry.")

    destination.mkdir()
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        records: list[CapsuleFileRecord] = []
        for relative_path in validated_paths:
            source_path = resolve_safe_output_path(source, relative_path)
            if not source_path.is_file():
                raise ValueError("Capsule inputs must be ordinary files.")
            destination_path = resolve_safe_output_path(destination, relative_path)
            _create_directories(destination_path.parent, destination, created_directories)
            content = source_path.read_bytes()
            created_files.append(destination_path)
            _write_new_file(destination_path, content)
            records.append(
                CapsuleFileRecord(
                    path=relative_path,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        directories = tuple(
            sorted(
                {
                    parent.as_posix()
                    for record in records
                    for parent in _relative_parents(Path(record.path))
                }
            )
        )
        manifest = {
            "capsule_id": capsule_id,
            "compiler_version": compiler_version,
            "directories": list(directories),
            "entry_path": _ENTRY_NAME,
            "files": [
                {"path": record.path, "sha256": record.sha256, "size": record.size}
                for record in records
            ],
            "schema_version": _SCHEMA_VERSION,
        }
        manifest_content = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        manifest_path = destination / _MANIFEST_NAME
        created_files.append(manifest_path)
        _write_new_file(manifest_path, manifest_content)
    except Exception:
        _remove_partial_capsule(destination, created_files, created_directories)
        raise

    LOGGER.info(
        "capsule_package_built capsule_id=%s files=%d",
        capsule_id,
        len(validated_paths),
    )
    return destination


def verify_capsule_from_entry(entry_path: Path) -> VerifiedCapsule:
    """Derive capsule root from the entry file and prove an exact owned tree."""
    if not entry_path.is_absolute() or entry_path.name != _ENTRY_NAME:
        raise CapsuleOwnershipError("Capsule entry path is invalid.")
    root = entry_path.parent
    if (root / ".git").exists() or (root / ".git").is_symlink():
        raise DevelopmentSourceError("Development repositories are never disposable capsules.")
    if _is_link_or_reparse(root):
        raise CapsuleOwnershipError("Capsule root cannot be linked or reparsed.")
    manifest_path = root / _MANIFEST_NAME
    try:
        manifest_content = manifest_path.read_bytes()
        manifest = json.loads(manifest_content)
        verified = _parse_manifest(root, entry_path, manifest, manifest_content)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError) as error:
        raise CapsuleOwnershipError("Capsule manifest is invalid.") from error
    _require_exact_capsule_tree(verified)
    LOGGER.info(
        "capsule_ownership_verified capsule_id=%s files=%d",
        verified.capsule_id,
        len(verified.files),
    )
    return verified


def cleanup_verified_capsule(
    capsule: VerifiedCapsule,
    *,
    unlinker: Callable[[Path], None] | None = None,
) -> CapsuleCleanupResult:
    """Start cleanup only after the complete capsule still matches exactly."""
    current = verify_capsule_from_entry(capsule.entry_path)
    if current != capsule:
        raise CapsuleOwnershipError("Capsule ownership changed after verification.")
    return _cleanup_capsule(capsule, unlinker=unlinker, allow_missing=False)


def retry_capsule_cleanup(
    capsule: VerifiedCapsule,
    *,
    unlinker: Callable[[Path], None] | None = None,
) -> CapsuleCleanupResult:
    """Continue a prior exact cleanup while refusing any newly added entry."""
    if not capsule.root.exists():
        return CapsuleCleanupResult(completed=True)
    if not _owned_capsule_subset_is_safe(capsule):
        return CapsuleCleanupResult(False, ("capsule-ownership-check",))
    return _cleanup_capsule(capsule, unlinker=unlinker, allow_missing=True)


def wait_for_parent_eof(read_file_descriptor: int, on_eof: Callable[[], None]) -> None:
    """Block on a parent-owned pipe and invoke cleanup only after true EOF."""
    with os.fdopen(read_file_descriptor, "rb", closefd=True) as pipe:
        while pipe.read(8192):
            pass
    on_eof()


def write_capsule_cleanup_journal(
    capsule: VerifiedCapsule,
    journal_path: Path,
) -> CapsuleCleanupJournal:
    """Persist cleanup authority outside the disposable capsule before supervision."""
    current = verify_capsule_from_entry(capsule.entry_path)
    if current != capsule:
        raise CapsuleOwnershipError("Capsule changed before cleanup journal creation.")
    if not journal_path.is_absolute() or journal_path.name != _CLEANUP_JOURNAL_NAME:
        raise ValueError("Capsule cleanup journal path is invalid.")
    if not journal_path.parent.is_dir() or _path_is_within(journal_path, capsule.root):
        raise ValueError("Capsule cleanup journal must be outside the disposable capsule.")
    payload = {
        "capsule_id": capsule.capsule_id,
        "entry_path": str(capsule.entry_path),
        "manifest_sha256": capsule.manifest_sha256,
        "schema_version": _SCHEMA_VERSION,
    }
    _write_new_file(
        journal_path,
        (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode(),
    )
    LOGGER.info("capsule_cleanup_journal_written capsule_id=%s", capsule.capsule_id)
    return _load_capsule_cleanup_journal(journal_path)


def start_capsule_cleanup_supervisor(
    capsule: VerifiedCapsule,
    *,
    journal_path: Path,
    interpreter: Path,
    working_directory: Path,
) -> CapsuleCleanupSupervisor:
    """Launch fixed cleanup logic outside the capsule and signal it only by pipe EOF."""
    current = verify_capsule_from_entry(capsule.entry_path)
    if current != capsule:
        raise CapsuleOwnershipError("Capsule changed before cleanup supervision.")
    journal = _load_capsule_cleanup_journal(journal_path)
    if (
        journal.entry_path != capsule.entry_path
        or journal.capsule_id != capsule.capsule_id
        or journal.manifest_sha256 != capsule.manifest_sha256
    ):
        raise CapsuleOwnershipError("Cleanup journal does not bind this capsule.")
    resolved_interpreter = interpreter.resolve(strict=True)
    resolved_working_directory = working_directory.resolve(strict=True)
    if not resolved_interpreter.is_file():
        raise ValueError("Cleanup interpreter must be an existing ordinary file.")
    if not resolved_working_directory.is_dir():
        raise ValueError("Cleanup working directory must exist.")
    if _path_is_within(resolved_interpreter, capsule.root):
        raise ValueError("Cleanup interpreter must be outside the disposable capsule.")
    if _path_is_within(resolved_working_directory, capsule.root):
        raise ValueError("Cleanup working directory must be outside the disposable capsule.")

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(capsule.entry_path)
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    supervisor_code = (
        "from scaffold_compiler.capsule_package import _capsule_supervisor_main; "
        "_capsule_supervisor_main()"
    )
    process = subprocess.Popen(
        (str(resolved_interpreter), "-S", "-c", supervisor_code, str(journal.path)),
        cwd=resolved_working_directory,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=None,
        stderr=None,
        shell=False,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    LOGGER.info(
        "capsule_cleanup_supervisor_started capsule_id=%s pid=%d",
        capsule.capsule_id,
        process.pid,
    )
    return CapsuleCleanupSupervisor(process=process)


def _capsule_supervisor_main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    while sys.stdin.buffer.read(8192):
        pass
    try:
        journal = _load_capsule_cleanup_journal(Path(sys.argv[1]))
        if not journal.entry_path.parent.exists():
            _finish_cleanup_journal(journal.path)
            raise SystemExit(0)
        capsule = verify_capsule_from_entry(journal.entry_path)
        if (
            capsule.capsule_id != journal.capsule_id
            or capsule.manifest_sha256 != journal.manifest_sha256
        ):
            raise CapsuleOwnershipError("Cleanup journal capsule binding is invalid.")
        result = cleanup_verified_capsule(capsule)
    except (CapsuleOwnershipError, OSError, ValueError):
        raise SystemExit(1) from None
    if result.completed:
        try:
            _finish_cleanup_journal(journal.path)
        except OSError:
            raise SystemExit(1) from None
    raise SystemExit(0 if result.completed else 1)


def _load_capsule_cleanup_journal(journal_path: Path) -> CapsuleCleanupJournal:
    if not journal_path.is_absolute() or journal_path.name != _CLEANUP_JOURNAL_NAME:
        raise CapsuleOwnershipError("Capsule cleanup journal path is invalid.")
    try:
        record = json.loads(journal_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or set(record) != {
            "capsule_id",
            "entry_path",
            "manifest_sha256",
            "schema_version",
        }:
            raise TypeError("Cleanup journal fields are invalid.")
        if record["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("Cleanup journal schema is invalid.")
        capsule_id = record["capsule_id"]
        entry_value = record["entry_path"]
        manifest_sha256 = record["manifest_sha256"]
        if not all(isinstance(value, str) for value in (capsule_id, entry_value, manifest_sha256)):
            raise TypeError("Cleanup journal values are invalid.")
        _validate_capsule_id(capsule_id)
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ValueError("Cleanup journal digest is invalid.")
        entry_path = Path(entry_value)
        if not entry_path.is_absolute() or entry_path.name != _ENTRY_NAME:
            raise ValueError("Cleanup journal entry path is invalid.")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise CapsuleOwnershipError("Capsule cleanup journal is invalid.") from error
    return CapsuleCleanupJournal(
        path=journal_path,
        entry_path=entry_path,
        capsule_id=capsule_id,
        manifest_sha256=manifest_sha256,
    )


def _finish_cleanup_journal(journal_path: Path) -> None:
    journal_content = journal_path.read_bytes()
    if any(path != journal_path for path in journal_path.parent.iterdir()):
        raise OSError("Cleanup workspace still contains other entries.")
    _unlink_file(journal_path)
    try:
        journal_path.parent.rmdir()
    except OSError:
        try:
            _write_new_file(journal_path, journal_content)
        except OSError:
            LOGGER.error("capsule_cleanup_journal_restore_failed")
        raise


def _parse_manifest(
    root: Path,
    entry_path: Path,
    manifest: object,
    manifest_content: bytes,
) -> VerifiedCapsule:
    expected_fields = {
        "capsule_id",
        "compiler_version",
        "directories",
        "entry_path",
        "files",
        "schema_version",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise CapsuleOwnershipError("Capsule manifest fields are invalid.")
    if manifest["schema_version"] != _SCHEMA_VERSION or manifest["entry_path"] != _ENTRY_NAME:
        raise CapsuleOwnershipError("Capsule manifest schema or entry is invalid.")
    capsule_id = manifest["capsule_id"]
    compiler_version = manifest["compiler_version"]
    raw_files = manifest["files"]
    raw_directories = manifest["directories"]
    if not isinstance(capsule_id, str):
        raise CapsuleOwnershipError("Capsule ID is invalid.")
    _validate_capsule_id(capsule_id)
    if not isinstance(compiler_version, str) or not _VERSION_PATTERN.fullmatch(compiler_version):
        raise CapsuleOwnershipError("Capsule compiler version is invalid.")
    if not isinstance(raw_files, list) or not isinstance(raw_directories, list):
        raise CapsuleOwnershipError("Capsule ownership lists are invalid.")
    records = tuple(_parse_file_record(record) for record in raw_files)
    paths = validate_unique_target_paths(record.path for record in records)
    if tuple(sorted(paths)) != paths or _ENTRY_NAME not in paths:
        raise CapsuleOwnershipError("Capsule file order or entry ownership is invalid.")
    if any(not isinstance(directory, str) for directory in raw_directories):
        raise CapsuleOwnershipError("Capsule directory entries are invalid.")
    directories = validate_unique_target_paths(raw_directories)
    if tuple(sorted(directories)) != directories:
        raise CapsuleOwnershipError("Capsule directory order is invalid.")
    expected_directories = tuple(
        sorted(
            {
                parent.as_posix()
                for record in records
                for parent in _relative_parents(Path(record.path))
            }
        )
    )
    if directories != expected_directories:
        raise CapsuleOwnershipError("Capsule directory ownership is incomplete.")
    return VerifiedCapsule(
        root=root,
        entry_path=entry_path,
        capsule_id=capsule_id,
        compiler_version=compiler_version,
        files=records,
        directories=directories,
        manifest_sha256=hashlib.sha256(manifest_content).hexdigest(),
    )


def _parse_file_record(record: object) -> CapsuleFileRecord:
    if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
        raise CapsuleOwnershipError("Capsule file record fields are invalid.")
    path = record["path"]
    size = record["size"]
    sha256 = record["sha256"]
    if not isinstance(path, str) or not isinstance(sha256, str):
        raise CapsuleOwnershipError("Capsule file record types are invalid.")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise CapsuleOwnershipError("Capsule file size is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise CapsuleOwnershipError("Capsule file digest is invalid.")
    return CapsuleFileRecord(path=path, size=size, sha256=sha256)


def _require_exact_capsule_tree(capsule: VerifiedCapsule) -> None:
    if not _owned_capsule_subset_is_safe(capsule):
        raise CapsuleOwnershipError("Capsule tree contains unowned or changed entries.")
    actual_files, actual_directories = _actual_capsule_entries(capsule.root)
    expected_files = {record.path for record in capsule.files} | {_MANIFEST_NAME}
    if actual_files != expected_files or actual_directories != set(capsule.directories):
        raise CapsuleOwnershipError("Capsule tree does not exactly match its manifest.")


def _owned_capsule_subset_is_safe(capsule: VerifiedCapsule) -> bool:
    if _is_link_or_reparse(capsule.root) or (capsule.root / ".git").exists():
        return False
    manifest_path = capsule.root / _MANIFEST_NAME
    try:
        if (
            manifest_path.exists()
            and hashlib.sha256(manifest_path.read_bytes()).hexdigest() != capsule.manifest_sha256
        ):
            return False
        actual_files, actual_directories = _actual_capsule_entries(capsule.root)
        allowed_files = {record.path for record in capsule.files} | {_MANIFEST_NAME}
        if not actual_files <= allowed_files:
            return False
        if not actual_directories <= set(capsule.directories):
            return False
        records = {record.path: record for record in capsule.files}
        for relative_path in actual_files - {_MANIFEST_NAME}:
            if not _file_matches_record(capsule.root / relative_path, records[relative_path]):
                return False
    except (OSError, KeyError):
        return False
    return True


def _actual_capsule_entries(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for path in root.rglob("*"):
        if _is_link_or_reparse(path):
            raise CapsuleOwnershipError("Capsule contains a link or reparse point.")
        relative_path = path.relative_to(root).as_posix()
        if path.is_file():
            files.add(relative_path)
        elif path.is_dir():
            directories.add(relative_path)
        else:
            raise CapsuleOwnershipError("Capsule contains a non-ordinary entry.")
    return files, directories


def _cleanup_capsule(
    capsule: VerifiedCapsule,
    *,
    unlinker: Callable[[Path], None] | None,
    allow_missing: bool,
) -> CapsuleCleanupResult:
    delete_file = unlinker or _unlink_file
    failures: list[str] = []
    for record in capsule.files:
        path = capsule.root / record.path
        if not path.exists():
            if not allow_missing:
                failures.append(record.path)
            continue
        try:
            if not _file_matches_record(path, record):
                failures.append(record.path)
                break
            delete_file(path)
        except OSError:
            failures.append(record.path)
    for directory in sorted(capsule.directories, key=lambda value: value.count("/"), reverse=True):
        path = capsule.root / directory
        if not path.exists():
            continue
        try:
            path.rmdir()
        except OSError:
            failures.append(directory)
    manifest_path = capsule.root / _MANIFEST_NAME
    if not failures and manifest_path.exists():
        try:
            delete_file(manifest_path)
        except OSError:
            failures.append(_MANIFEST_NAME)
    if not failures:
        try:
            capsule.root.rmdir()
        except OSError:
            failures.append(".")
    completed = not capsule.root.exists()
    LOGGER.log(
        logging.INFO if completed else logging.WARNING,
        "capsule_cleanup_finished capsule_id=%s completed=%s failures=%d",
        capsule.capsule_id,
        completed,
        len(failures),
    )
    return CapsuleCleanupResult(completed=completed, failed_entries=tuple(failures))


def _create_directories(parent: Path, root: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = parent
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _write_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _remove_partial_capsule(root: Path, files: list[Path], directories: list[Path]) -> None:
    failures = 0
    for path in reversed(files):
        try:
            if path.exists():
                _unlink_file(path)
        except OSError:
            failures += 1
    for directory in reversed(directories):
        try:
            directory.rmdir()
        except OSError:
            failures += 1
    try:
        root.rmdir()
    except OSError:
        failures += 1
    if failures:
        LOGGER.error("capsule_partial_cleanup_incomplete failures=%d", failures)


def _file_matches_record(path: Path, record: CapsuleFileRecord) -> bool:
    content = path.read_bytes()
    return len(content) == record.size and hashlib.sha256(content).hexdigest() == record.sha256


def _unlink_file(path: Path) -> None:
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def _relative_parents(path: Path) -> tuple[Path, ...]:
    parents: list[Path] = []
    parent = path.parent
    while parent != Path("."):
        parents.append(parent)
        parent = parent.parent
    return tuple(parents)


def _paths_overlap(first: Path, second: Path) -> bool:
    first_normalized = os.path.normcase(str(first.absolute()))
    second_normalized = os.path.normcase(str(second.absolute()))
    try:
        return os.path.commonpath((first_normalized, second_normalized)) in {
            first_normalized,
            second_normalized,
        }
    except ValueError:
        return False


def _path_is_within(path: Path, parent: Path) -> bool:
    normalized_path = os.path.normcase(str(path.absolute()))
    normalized_parent = os.path.normcase(str(parent.absolute()))
    try:
        return os.path.commonpath((normalized_path, normalized_parent)) == normalized_parent
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _validate_capsule_id(capsule_id: str) -> None:
    try:
        parsed = uuid.UUID(capsule_id)
    except (ValueError, AttributeError) as error:
        raise ValueError("Capsule ID must be a UUID.") from error
    if str(parsed) != capsule_id or parsed.version != 4:
        raise ValueError("Capsule ID must be a canonical UUID4.")
