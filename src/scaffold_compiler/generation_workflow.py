"""Journal-first non-interactive generation workflow for the fixed V1 matrix."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scaffold_compiler.blueprint_catalog import BlueprintCatalog
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
)
from scaffold_compiler.finalization import (
    cleanup_owned_candidate,
    prepare_commit_snapshot,
)
from scaffold_compiler.finalize_transaction import publish_confirmed_project
from scaffold_compiler.path_safety import resolve_safe_output_path
from scaffold_compiler.project_configuration import ProjectConfiguration
from scaffold_compiler.session_state_store import (
    GenerationSession,
    SessionEvent,
    SessionStateStore,
    transition_session,
)
from scaffold_compiler.target_lock import TargetLockStore
from scaffold_compiler.v1_project_compiler import (
    compile_v1_plan,
    materialize_v1_candidate,
)
from scaffold_compiler.validation import (
    ValidationReport,
    issue_verification_credential,
)
from scaffold_compiler.validation_workspace import (
    cleanup_validation_workspace,
    prepare_validation_workspace,
)

LOGGER = logging.getLogger(__name__)


class GenerationWorkflowError(RuntimeError):
    """Raised when a coordinated V1 run cannot reach a clean terminal state."""


@dataclass(frozen=True, slots=True)
class CompletedGeneration:
    """Stable facts returned after publication and workspace cleanup."""

    target: Path
    files: tuple[CandidateFileRecord, ...]
    digest: str
    session: GenerationSession


CandidateValidator = Callable[
    [CandidateAssemblyResult, str, str, tuple[str, ...], Path],
    ValidationReport,
]


def execute_non_interactive_generation(
    configuration: ProjectConfiguration,
    *,
    run_id: str,
    validator: CandidateValidator,
    catalog_root: Path | None = None,
    mover: Callable[[Path, Path], None] | None = None,
) -> CompletedGeneration:
    """Generate, verify, publish, and clean one fixed V1 project transaction."""
    target = configuration.target_directory
    workspace = target.parent / f".{target.name}.scaffold-{run_id}"
    lock_store = TargetLockStore(target)
    lock = lock_store.acquire(
        run_id=run_id,
        process_start_token=f"{os.getpid()}-{time.monotonic_ns()}",
    )
    lock_released = False
    try:
        workspace.mkdir()
        store = SessionStateStore(workspace / "session.json")
        session = GenerationSession.new(
            run_id=run_id,
            configuration_digest=configuration.configuration_digest,
        )
        store.save(session)

        try:
            catalog, plan = compile_v1_plan(configuration, catalog_root=catalog_root)
            blueprint_digest = _selected_blueprint_digest(catalog, plan.blueprint_ids)
        except Exception:
            session = transition_session(session, SessionEvent.PLAN_FAILED)
            store.save(session)
            raise
        session = transition_session(session, SessionEvent.PLAN_SUCCEEDED, plan_digest=plan.digest)
        store.save(session)

        session = transition_session(session, SessionEvent.MATERIALIZE_REQUESTED)
        store.save(session)
        try:
            candidate = materialize_v1_candidate(
                configuration,
                workspace,
                catalog=catalog,
                plan=plan,
                catalog_root=catalog_root,
            )
            if _selected_blueprint_digest(catalog, plan.blueprint_ids) != blueprint_digest:
                raise GenerationWorkflowError(
                    "Selected blueprint bytes changed during materialization."
                )
        except Exception:
            session = transition_session(session, SessionEvent.MATERIALIZE_FAILED)
            store.save(session)
            raise
        session = transition_session(
            session,
            SessionEvent.MATERIALIZE_SUCCEEDED,
            candidate_digest=candidate.digest,
        )
        store.save(session)

        session = transition_session(session, SessionEvent.VERIFY_REQUESTED)
        store.save(session)
        required_validations = _required_validation_names(catalog, plan.blueprint_ids)
        try:
            validation_workspace = prepare_validation_workspace(workspace, run_id=run_id)
            report = validator(
                candidate,
                configuration.configuration_digest,
                blueprint_digest,
                required_validations,
                validation_workspace.root,
            )
            if (
                report.configuration_digest != configuration.configuration_digest
                or report.blueprint_digest != blueprint_digest
                or report.plan_digest != plan.digest
                or report.candidate_digest != candidate.digest
            ):
                raise GenerationWorkflowError("Validator report does not bind this generation run.")
            checks_by_name = {check.name: check for check in report.checks}
            if any(
                name not in checks_by_name or not checks_by_name[name].required
                for name in required_validations
            ):
                raise GenerationWorkflowError(
                    "Validator report omits a required blueprint validation."
                )
            credential = issue_verification_credential(
                report,
                current_candidate_digest=candidate.digest,
            )
        except Exception:
            session = transition_session(session, SessionEvent.VERIFY_FAILED)
            store.save(session)
            raise
        session = transition_session(
            session,
            SessionEvent.VERIFY_SUCCEEDED,
            verification_digest=credential.verification_digest,
        )
        store.save(session)

        session = transition_session(session, SessionEvent.FINALIZE_REQUESTED)
        store.save(session)
        try:
            snapshot = prepare_commit_snapshot(candidate, workspace, run_id=run_id)
        except Exception:
            session = transition_session(session, SessionEvent.FINALIZE_PRECHECK_FAILED)
            store.save(session)
            raise
        session = publish_confirmed_project(
            session,
            credential,
            snapshot,
            target,
            store=store,
            mover=mover,
        )

        session = transition_session(session, SessionEvent.CLEANUP_STARTED)
        store.save(session)
        validation_cleanup = cleanup_validation_workspace(validation_workspace)
        if not validation_cleanup.completed:
            session = transition_session(session, SessionEvent.CLEANUP_FAILED)
            store.save(session)
            raise GenerationWorkflowError(
                "Validation workspace cleanup is pending and can be retried."
            )
        cleanup = cleanup_owned_candidate(candidate)
        if not cleanup.completed:
            session = transition_session(session, SessionEvent.CLEANUP_FAILED)
            store.save(session)
            raise GenerationWorkflowError("Candidate cleanup is pending and can be retried.")
        session = transition_session(session, SessionEvent.CLEANUP_SUCCEEDED)
        store.save(session)

        lock_store.release(lock)
        lock_released = True
        (workspace / "session.json").unlink()
        workspace.rmdir()
        LOGGER.info(
            "generation_workflow_completed run_id=%s target_name=%s files=%d",
            run_id,
            target.name,
            len(candidate.files),
        )
        return CompletedGeneration(
            target=target,
            files=candidate.files,
            digest=candidate.digest,
            session=session,
        )
    finally:
        if not lock_released:
            try:
                lock_store.release(lock)
            except (OSError, ValueError):
                LOGGER.error("generation_workflow_lock_release_failed run_id=%s", run_id)


def _selected_blueprint_digest(
    catalog: BlueprintCatalog,
    blueprint_ids: tuple[str, ...],
) -> str:
    selected = {manifest.blueprint_id: manifest for manifest in catalog.manifests}
    records: list[dict[str, object]] = []
    for blueprint_id in blueprint_ids:
        manifest = selected[blueprint_id]
        files = []
        for file in manifest.files:
            content = resolve_safe_output_path(manifest.directory, file.source).read_bytes()
            files.append(
                {
                    "kind": file.kind.value,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "source": file.source,
                    "target": file.target,
                }
            )
        records.append(
            {
                "blueprint_id": manifest.blueprint_id,
                "conflicts": manifest.conflicts,
                "contributions": manifest.contributions_json,
                "files": files,
                "provides": manifest.provides,
                "requires": manifest.requires,
                "stage": manifest.stage.value,
                "validations": manifest.validations,
                "variables": manifest.variables_json,
                "version": manifest.version,
            }
        )
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _required_validation_names(
    catalog: BlueprintCatalog,
    blueprint_ids: tuple[str, ...],
) -> tuple[str, ...]:
    selected = {manifest.blueprint_id: manifest for manifest in catalog.manifests}
    return tuple(
        sorted(
            {
                validation
                for blueprint_id in blueprint_ids
                for validation in selected[blueprint_id].validations
            }
        )
    )
