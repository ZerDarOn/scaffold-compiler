"""Journal-first project generation transaction with a legacy V1 wrapper."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scaffold_compiler.blueprint_catalog import BlueprintCatalog
from scaffold_compiler.blueprint_plan_compiler import GenerationPlan
from scaffold_compiler.candidate_ownership_store import CandidateOwnershipStore
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
)
from scaffold_compiler.finalization import (
    cleanup_owned_candidate,
    prepare_commit_snapshot,
)
from scaffold_compiler.finalize_transaction import publish_confirmed_project
from scaffold_compiler.generation_workspace_identity import (
    CURRENT_WORKSPACE_SCHEME,
    derive_generation_workspace,
)
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
from scaffold_compiler.validation_report_store import ValidationReportStore
from scaffold_compiler.validation_workspace import (
    cleanup_validation_workspace,
    prepare_validation_workspace,
)
from scaffold_compiler.workspace_path_budget import (
    WorkspacePathBudgetError,
    validate_workspace_path_budget,
)

LOGGER = logging.getLogger(__name__)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class GenerationWorkflowError(RuntimeError):
    """Raised when a coordinated V1 run cannot reach a clean terminal state."""


class GenerationPreflightError(GenerationWorkflowError):
    """Raised when generation is rejected before its first side effect."""


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
PlanCompiler = Callable[[], tuple[BlueprintCatalog, GenerationPlan]]
CandidateMaterializer = Callable[[Path, BlueprintCatalog, GenerationPlan], CandidateAssemblyResult]


def execute_non_interactive_generation(
    configuration: ProjectConfiguration,
    *,
    run_id: str,
    validator: CandidateValidator,
    catalog_root: Path | None = None,
    mover: Callable[[Path, Path], None] | None = None,
) -> CompletedGeneration:
    """Preserve the fixed V1 call contract over the language-neutral transaction."""

    def plan_compiler() -> tuple[BlueprintCatalog, GenerationPlan]:
        return compile_v1_plan(configuration, catalog_root=catalog_root)

    def candidate_materializer(
        workspace: Path,
        catalog: BlueprintCatalog,
        plan: GenerationPlan,
    ) -> CandidateAssemblyResult:
        return materialize_v1_candidate(
            configuration,
            workspace,
            catalog=catalog,
            plan=plan,
            catalog_root=catalog_root,
        )

    return execute_generation_transaction(
        target=configuration.target_directory,
        configuration_digest=configuration.configuration_digest,
        run_id=run_id,
        plan_compiler=plan_compiler,
        candidate_materializer=candidate_materializer,
        validator=validator,
        mover=mover,
    )


def execute_generation_transaction(
    *,
    target: Path,
    configuration_digest: str,
    run_id: str,
    plan_compiler: PlanCompiler,
    candidate_materializer: CandidateMaterializer,
    validator: CandidateValidator,
    mover: Callable[[Path, Path], None] | None = None,
    recipe_id: str | None = None,
    recipe_version: str | None = None,
) -> CompletedGeneration:
    """Generate, verify, publish, and clean without reading recipe-specific answers."""
    if not target.is_absolute() or not target.parent.is_dir():
        raise GenerationWorkflowError("Generation target must have an existing absolute parent.")
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise GenerationWorkflowError("Generation run ID is invalid.")
    if (recipe_id is None) != (recipe_version is None):
        raise GenerationWorkflowError("Recipe identity must be complete when provided.")
    initial_session = GenerationSession.new(
        run_id=run_id,
        configuration_digest=configuration_digest,
        target_name=target.name,
    )
    LOGGER.info(
        "generation_workflow_started run_id=%s recipe_id=%s recipe_version=%s",
        run_id,
        recipe_id or "legacy-v1",
        recipe_version or "legacy-v1",
    )
    workspace = derive_generation_workspace(
        target,
        run_id=run_id,
        configuration_digest=configuration_digest,
    )
    LOGGER.info(
        "generation_workspace_selected run_id=%s scheme=%s",
        run_id,
        CURRENT_WORKSPACE_SCHEME,
    )
    try:
        validate_workspace_path_budget(workspace, run_id=run_id)
    except WorkspacePathBudgetError as error:
        raise GenerationPreflightError(str(error)) from error
    lock_store = TargetLockStore(target)
    lock = lock_store.acquire(
        run_id=run_id,
        process_start_token=f"{os.getpid()}-{time.monotonic_ns()}",
    )
    lock_released = False
    try:
        workspace.mkdir()
        store = SessionStateStore(workspace / "session.json")
        candidate_store = CandidateOwnershipStore(workspace / "candidate_ownership.json")
        report_store = ValidationReportStore(workspace / "validation_report.json")
        session = initial_session
        store.save(session)

        try:
            catalog, plan = plan_compiler()
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
            candidate = candidate_materializer(workspace, catalog, plan)
            if _selected_blueprint_digest(catalog, plan.blueprint_ids) != blueprint_digest:
                raise GenerationWorkflowError(
                    "Selected blueprint bytes changed during materialization."
                )
            candidate_store.save(candidate)
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
                configuration_digest,
                blueprint_digest,
                required_validations,
                validation_workspace.root,
            )
            if (
                report.configuration_digest != configuration_digest
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
            report_store.save(report, workspace)
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
        try:
            report_store.path.unlink()
            candidate_store.path.unlink()
        except OSError:
            session = transition_session(session, SessionEvent.CLEANUP_FAILED)
            store.save(session)
            raise GenerationWorkflowError(
                "Candidate ownership cleanup is pending and can be retried."
            ) from None
        session = transition_session(session, SessionEvent.CLEANUP_SUCCEEDED)
        store.save(session)

        lock_store.release(lock)
        lock_released = True
        (workspace / "session.json").unlink()
        workspace.rmdir()
        LOGGER.info(
            "generation_workflow_completed run_id=%s recipe_id=%s recipe_version=%s files=%d",
            run_id,
            recipe_id or "legacy-v1",
            recipe_version or "legacy-v1",
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
                "schema_version": manifest.schema_version,
                "after": manifest.after,
                "conflicts": manifest.conflicts,
                "contributions": manifest.contributions_json,
                "files": files,
                "provides": manifest.provides,
                "requires": manifest.requires,
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
