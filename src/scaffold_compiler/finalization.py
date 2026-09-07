"""Sealed commit snapshots and native no-replace project publication."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import logging
import os
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateChangedError,
    CandidateFileRecord,
    calculate_candidate_digest,
    verify_candidate_digest,
)
from scaffold_compiler.path_safety import resolve_safe_output_path

LOGGER = logging.getLogger(__name__)

_RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_WINDOWS_TARGET_EXISTS: Final = frozenset({80, 183})
_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1


class FinalizationError(RuntimeError):
    """Base class for a safe Finalize refusal or ambiguous outcome."""


class CommitSnapshotChangedError(FinalizationError):
    """Raised when candidate or sealed snapshot bytes do not match verification."""


class TargetAlreadyExistsError(FinalizationError):
    """Raised when no-replace publication loses a target race."""


class CrossVolumeFinalizeError(FinalizationError):
    """Raised when directory rename cannot be atomic across the target boundary."""


class NativeNoReplaceUnavailableError(FinalizationError):
    """Raised instead of falling back to an unsafe check-then-rename sequence."""


class PublishDigestMismatchError(FinalizationError):
    """Raised after publication when target facts are ambiguous; target is preserved."""


class SnapshotStage(StrEnum):
    """Stable fault-injection boundaries for snapshot construction."""

    BEFORE_COPY = "before-copy"
    AFTER_FILE_COPY = "after-file-copy"
    AFTER_COPY = "after-copy"
    AFTER_SEAL = "after-seal"


class PublishRecoveryState(StrEnum):
    """Disk-fact outcomes allowed when recovering a COMMITTING session."""

    BEFORE_PUBLISH = "before-publish"
    PUBLISHED = "published"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class CommitSnapshot:
    """A byte-identical, read-only candidate prepared for one atomic move."""

    root: Path
    files: tuple[CandidateFileRecord, ...]
    digest: str
    plan_digest: str

    def as_candidate_result(self, *, root: Path | None = None) -> CandidateAssemblyResult:
        return CandidateAssemblyResult(
            root=root or self.root,
            files=self.files,
            digest=self.digest,
            plan_digest=self.plan_digest,
        )


@dataclass(frozen=True, slots=True)
class OwnedCleanupResult:
    """Outcome of an exact, retryable candidate cleanup attempt."""

    completed: bool
    failed_paths: tuple[str, ...] = ()


def prepare_commit_snapshot(
    candidate: CandidateAssemblyResult,
    workspace: Path,
    *,
    run_id: str,
    fault_injector: Callable[[SnapshotStage], None] | None = None,
) -> CommitSnapshot:
    """Copy only verified files, recheck both trees, then seal snapshot files."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Run ID is invalid for a commit snapshot.")
    if workspace.resolve(strict=True) != candidate.root.parent.resolve(strict=True):
        raise FinalizationError("Candidate must be owned directly by its workspace.")
    _verify_or_raise(candidate, "Candidate changed before snapshot construction.")
    snapshot_root = workspace / f".commit-{run_id}"
    try:
        snapshot_root.mkdir()
    except FileExistsError as error:
        raise FinalizationError("Commit snapshot already exists.") from error

    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        _inject(fault_injector, SnapshotStage.BEFORE_COPY)
        for record in candidate.files:
            source = resolve_safe_output_path(candidate.root, record.path)
            destination = resolve_safe_output_path(snapshot_root, record.path)
            _create_parents(destination.parent, snapshot_root, created_directories)
            _copy_new_file(source, destination)
            created_files.append(destination)
            _inject(fault_injector, SnapshotStage.AFTER_FILE_COPY)

        _inject(fault_injector, SnapshotStage.AFTER_COPY)
        _verify_or_raise(candidate, "Candidate changed while snapshot was copied.")
        snapshot_digest = calculate_candidate_digest(snapshot_root, candidate.files)
        if snapshot_digest != candidate.digest:
            raise CommitSnapshotChangedError("Commit snapshot differs from the candidate.")
        for path in created_files:
            path.chmod(stat.S_IREAD)
        _inject(fault_injector, SnapshotStage.AFTER_SEAL)
        snapshot = CommitSnapshot(
            root=snapshot_root,
            files=candidate.files,
            digest=snapshot_digest,
            plan_digest=candidate.plan_digest,
        )
        _verify_or_raise(
            snapshot.as_candidate_result(),
            "Commit snapshot changed after it was sealed.",
        )
    except Exception:
        _remove_incomplete_snapshot(snapshot_root, created_files, created_directories)
        raise

    LOGGER.info(
        "commit_snapshot_prepared snapshot_name=%s files=%d digest=%s",
        snapshot.root.name,
        len(snapshot.files),
        snapshot.digest,
    )
    return snapshot


def publish_commit_snapshot(
    snapshot: CommitSnapshot,
    target: Path,
    *,
    mover: Callable[[Path, Path], None] | None = None,
    post_move_hook: Callable[[Path], None] | None = None,
) -> CommitSnapshot:
    """Move with a native no-replace primitive and verify without modifying target."""
    if not target.is_absolute():
        raise FinalizationError("Finalize target must be absolute.")
    trusted_parent = target.parent.resolve(strict=True)
    if snapshot.root.parent.parent.resolve(strict=True) != trusted_parent:
        raise FinalizationError("Workspace must be a direct child of the target parent.")
    resolved_target = resolve_safe_output_path(trusted_parent, target.name)
    if resolved_target != target:
        raise FinalizationError("Finalize target spelling is not canonical.")
    if target.exists() or target.is_symlink():
        raise TargetAlreadyExistsError("Finalize target already exists.")
    _require_same_volume(snapshot.root, trusted_parent)
    _verify_or_raise(snapshot.as_candidate_result(), "Sealed commit snapshot changed.")

    LOGGER.info(
        "project_publish_started snapshot_name=%s target_name=%s digest=%s",
        snapshot.root.name,
        target.name,
        snapshot.digest,
    )
    try:
        (mover or _native_move_no_replace)(snapshot.root, target)
    except OSError as error:
        if _is_target_exists_error(error):
            raise TargetAlreadyExistsError("Finalize target won a publication race.") from error
        if error.errno == errno.EXDEV:
            raise CrossVolumeFinalizeError(
                "Finalize source and target are cross-volume."
            ) from error
        raise

    if post_move_hook is not None:
        post_move_hook(target)
    published = CommitSnapshot(
        root=target,
        files=snapshot.files,
        digest=snapshot.digest,
        plan_digest=snapshot.plan_digest,
    )
    try:
        verify_candidate_digest(published.as_candidate_result())
    except CandidateChangedError as error:
        LOGGER.error(
            "project_publish_digest_mismatch target_name=%s expected_digest=%s",
            target.name,
            snapshot.digest,
        )
        raise PublishDigestMismatchError(
            "Published target differs from the sealed snapshot; outcome is ambiguous."
        ) from error
    LOGGER.info(
        "project_publish_completed target_name=%s digest=%s",
        target.name,
        snapshot.digest,
    )
    return published


def recover_publish_state(snapshot: CommitSnapshot, target: Path) -> PublishRecoveryState:
    """Classify disk facts without moving, replacing, or deleting either directory."""
    snapshot_exists = snapshot.root.is_dir()
    target_exists = target.is_dir()
    if snapshot_exists == target_exists:
        return PublishRecoveryState.AMBIGUOUS
    existing_root = snapshot.root if snapshot_exists else target
    try:
        verify_candidate_digest(snapshot.as_candidate_result(root=existing_root))
    except CandidateChangedError:
        return PublishRecoveryState.AMBIGUOUS
    if snapshot_exists:
        return PublishRecoveryState.BEFORE_PUBLISH
    return PublishRecoveryState.PUBLISHED


def cleanup_owned_candidate(
    candidate: CandidateAssemblyResult,
    *,
    unlinker: Callable[[Path], None] | None = None,
) -> OwnedCleanupResult:
    """Delete only unchanged manifest-owned candidate entries, allowing safe retry."""
    if not candidate.root.exists():
        return OwnedCleanupResult(completed=True)
    if not _owned_candidate_subset_is_safe(candidate):
        return OwnedCleanupResult(
            completed=False,
            failed_paths=("candidate-ownership-check",),
        )

    delete_file = unlinker or _unlink_owned_file
    failures: list[str] = []
    for record in candidate.files:
        path = candidate.root / record.path
        if not path.exists():
            continue
        try:
            delete_file(path)
        except OSError:
            failures.append(record.path)

    directories = _owned_directories(candidate)
    for relative_path in sorted(directories, key=lambda value: value.count("/"), reverse=True):
        path = candidate.root / relative_path
        if not path.exists():
            continue
        try:
            path.rmdir()
        except OSError:
            failures.append(relative_path)
    try:
        candidate.root.rmdir()
    except OSError:
        failures.append(".")
    completed = not candidate.root.exists()
    if failures:
        LOGGER.warning(
            "owned_candidate_cleanup_incomplete candidate_name=%s failures=%d",
            candidate.root.name,
            len(failures),
        )
    else:
        LOGGER.info("owned_candidate_cleanup_completed candidate_name=%s", candidate.root.name)
    return OwnedCleanupResult(completed=completed, failed_paths=tuple(failures))


def _verify_or_raise(candidate: CandidateAssemblyResult, message: str) -> None:
    if _is_link_or_reparse(candidate.root):
        raise CommitSnapshotChangedError(message)
    try:
        verify_candidate_digest(candidate)
    except CandidateChangedError as error:
        raise CommitSnapshotChangedError(message) from error


def _owned_candidate_subset_is_safe(candidate: CandidateAssemblyResult) -> bool:
    if _is_link_or_reparse(candidate.root):
        return False
    expected = {record.path: record for record in candidate.files}
    expected_directories = _owned_directories(candidate)
    try:
        for path in candidate.root.rglob("*"):
            if path.is_symlink():
                return False
            relative_path = path.relative_to(candidate.root).as_posix()
            if path.is_dir():
                if relative_path not in expected_directories:
                    return False
                continue
            if not path.is_file():
                return False
            record = expected.get(relative_path)
            if record is None:
                return False
            content = path.read_bytes()
            if len(content) != record.size:
                return False
            if hashlib.sha256(content).hexdigest() != record.sha256:
                return False
    except OSError:
        return False
    return True


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _owned_directories(candidate: CandidateAssemblyResult) -> set[str]:
    directories: set[str] = set()
    for record in candidate.files:
        parent = Path(record.path).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _unlink_owned_file(path: Path) -> None:
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def _copy_new_file(source: Path, destination: Path) -> None:
    with source.open("rb") as source_file, destination.open("xb") as destination_file:
        while chunk := source_file.read(1024 * 1024):
            destination_file.write(chunk)
        destination_file.flush()
        os.fsync(destination_file.fileno())


def _create_parents(parent: Path, root: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = parent
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _remove_incomplete_snapshot(
    root: Path,
    files: list[Path],
    directories: list[Path],
) -> None:
    for path in reversed(files):
        try:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.error("commit_snapshot_file_cleanup_failed file_name=%s", path.name)
    for directory in reversed(directories):
        try:
            directory.rmdir()
        except OSError:
            LOGGER.error(
                "commit_snapshot_directory_cleanup_failed directory_name=%s",
                directory.name,
            )
    try:
        root.rmdir()
    except OSError:
        LOGGER.error("commit_snapshot_root_cleanup_failed snapshot_name=%s", root.name)


def _inject(
    fault_injector: Callable[[SnapshotStage], None] | None,
    stage: SnapshotStage,
) -> None:
    if fault_injector is not None:
        fault_injector(stage)


def _require_same_volume(source: Path, target_parent: Path) -> None:
    if source.stat().st_dev != target_parent.stat().st_dev:
        raise CrossVolumeFinalizeError("Finalize requires source and target on one volume.")


def _native_move_no_replace(source: Path, target: Path) -> None:
    if sys.platform == "win32":
        _windows_move_no_replace(source, target)
        return
    if sys.platform.startswith("linux"):
        _linux_move_no_replace(source, target)
        return
    raise NativeNoReplaceUnavailableError(
        "This platform has no verified native no-replace directory primitive."
    )


def _windows_move_no_replace(source: Path, target: Path) -> None:
    win_dll = vars(ctypes).get("WinDLL")
    set_last_error = vars(ctypes).get("set_last_error")
    get_last_error = vars(ctypes).get("get_last_error")
    if win_dll is None or set_last_error is None or get_last_error is None:
        raise NativeNoReplaceUnavailableError("MoveFileExW is unavailable.")
    kernel32 = win_dll("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    move_file.restype = ctypes.c_int
    set_last_error(0)
    if move_file(str(source), str(target), 0):
        return
    error_code = int(get_last_error())
    if error_code in _WINDOWS_TARGET_EXISTS:
        raise FileExistsError(error_code, "Finalize target already exists.", str(target))
    raise OSError(error_code, "MoveFileExW failed.", str(source), str(target))


def _linux_move_no_replace(source: Path, target: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NativeNoReplaceUnavailableError("renameat2 is unavailable.")
    rename.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    rename.restype = ctypes.c_int
    if (
        rename(
            _AT_FDCWD,
            os.fsencode(source),
            _AT_FDCWD,
            os.fsencode(target),
            _RENAME_NOREPLACE,
        )
        == 0
    ):
        return
    error_code = ctypes.get_errno()
    if error_code == errno.EEXIST:
        raise FileExistsError(error_code, "Finalize target already exists.", str(target))
    if error_code in {errno.ENOSYS, errno.EINVAL}:
        raise NativeNoReplaceUnavailableError("renameat2 no-replace is unsupported.")
    raise OSError(error_code, os.strerror(error_code), str(source), str(target))


def _is_target_exists_error(error: OSError) -> bool:
    return error.errno == errno.EEXIST or getattr(error, "winerror", None) in _WINDOWS_TARGET_EXISTS
