"""Concrete V1 application boundary for a fully confirmed non-interactive run."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.cleanup_recovery import retry_cleanup_pending_workspace
from scaffold_compiler.command_line_interface import CommandOutcome
from scaffold_compiler.failed_workspace_recovery import (
    discard_failed_workspace,
    inspect_failed_workspace,
)
from scaffold_compiler.fastapi_project_validation_adapter import (
    FastApiValidationRuntime,
    build_fastapi_project_validation_adapter_registration,
)
from scaffold_compiler.generation_workflow import (
    CompletedGeneration,
    GenerationPreflightError,
    execute_non_interactive_generation,
)
from scaffold_compiler.project_configuration import (
    DatabaseChoice,
    ProjectConfiguration,
    parse_project_configuration,
)
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationRequest,
    build_project_validation_adapter_registry,
    execute_project_validation,
)
from scaffold_compiler.recipe_project_configuration import (
    convert_legacy_project_configuration,
)
from scaffold_compiler.v1_project_compiler import compile_v1_plan
from scaffold_compiler.validation import ValidationReport, ValidationStatus

LOGGER = logging.getLogger(__name__)

GenerationRunner = Callable[..., CompletedGeneration]


class V1CommandApplication:
    """Bind runtime resources to the existing V1 generation transaction."""

    def __init__(
        self,
        *,
        catalog_root: Path,
        working_directory: Path,
        home_directory: Path,
        uv_executable: Path | None,
        docker_executable: Path | None = None,
        environment: Mapping[str, str] | None = None,
        generation_runner: GenerationRunner = execute_non_interactive_generation,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._catalog_root = catalog_root.resolve(strict=True)
        self._working_directory = working_directory.resolve(strict=True)
        self._home_directory = home_directory.resolve(strict=False)
        self._uv_executable = (
            uv_executable.resolve(strict=True) if uv_executable is not None else None
        )
        self._docker_executable = (
            docker_executable.resolve(strict=True) if docker_executable is not None else None
        )
        self._environment = dict(os.environ if environment is None else environment)
        self._generation_runner = generation_runner
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)

    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        """Load one config and execute the complete confirmed transaction."""
        if self._uv_executable is None:
            return CommandOutcome(1, "uv is required for project generation.")
        uv_executable = self._uv_executable
        try:
            configuration = self._load_configuration(config_path)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            LOGGER.error("v1_configuration_load_failed")
            return CommandOutcome(1, "Configuration could not be loaded.")

        database_url = (
            self._environment.get("DATABASE_URL")
            if configuration.database is DatabaseChoice.POSTGRES
            else None
        )
        recipe_registry = build_builtin_project_recipe_registry()
        recipe = recipe_registry.get(FASTAPI_RECIPE_ID)
        recipe_configuration = convert_legacy_project_configuration(
            configuration,
            registry=recipe_registry,
        )
        validation_registry = build_project_validation_adapter_registry(
            (build_fastapi_project_validation_adapter_registration(),)
        )

        def validator(
            candidate: CandidateAssemblyResult,
            configuration_digest: str,
            blueprint_digest: str,
            required_validations: tuple[str, ...],
            validation_environment: Path,
        ) -> ValidationReport:
            report = execute_project_validation(
                ProjectValidationRequest(
                    configuration=recipe_configuration,
                    recipe=recipe,
                    candidate=candidate,
                    configuration_digest=configuration_digest,
                    blueprint_digest=blueprint_digest,
                    required_validations=required_validations,
                    validation_environment=validation_environment,
                    runtime_context=FastApiValidationRuntime(
                        package_name=configuration.package_name,
                        uv_executable=uv_executable,
                        database_url=database_url,
                        run_id=run_id,
                        docker_executable=self._docker_executable,
                        forbidden_absolute_paths=(
                            self._catalog_root.parent,
                            candidate.root.parent,
                        ),
                    ),
                ),
                validation_registry,
            )
            incomplete = tuple(
                check.name
                for check in report.checks
                if check.required and check.status is not ValidationStatus.PASS
            )
            if incomplete:
                LOGGER.warning(
                    "v1_validation_incomplete run_id=%s gates=%s",
                    run_id,
                    ",".join(incomplete),
                )
            return report

        run_id = self._run_id_factory()
        try:
            completed = self._generation_runner(
                configuration,
                run_id=run_id,
                validator=validator,
                catalog_root=self._catalog_root,
            )
        except GenerationPreflightError as error:
            LOGGER.warning("v1_generation_preflight_rejected run_id=%s", run_id)
            return CommandOutcome(1, str(error))
        except Exception as error:
            LOGGER.error(
                "v1_non_interactive_run_failed run_id=%s error_type=%s",
                run_id,
                type(error).__name__,
            )
            workspace_name = f".{configuration.target_directory.name}.scaffold-{run_id}"
            return CommandOutcome(
                1,
                f"Project generation did not complete; failed workspace: {workspace_name}",
            )
        return CommandOutcome(
            0,
            f"Project finalized successfully: {completed.target.name}",
        )

    def _load_configuration(self, config_path: Path) -> ProjectConfiguration:
        path = config_path if config_path.is_absolute() else self._working_directory / config_path
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise TypeError("Configuration must be a JSON object with string keys.")
        return parse_project_configuration(
            raw,
            working_directory=self._working_directory,
            home_directory=self._home_directory,
        )

    def preview(self, config_path: Path) -> CommandOutcome:
        """Return the deterministic V1 plan without creating a workspace."""
        try:
            configuration = self._load_configuration(config_path)
            catalog, plan = compile_v1_plan(
                configuration,
                catalog_root=self._catalog_root,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            LOGGER.error("v1_preview_failed")
            return CommandOutcome(1, "Project plan could not be created.")
        selected = {manifest.blueprint_id: manifest for manifest in catalog.manifests}
        validations = sorted(
            {
                validation
                for blueprint_id in plan.blueprint_ids
                for validation in selected[blueprint_id].validations
            }
        )
        summary = {
            "blueprints": list(plan.blueprint_ids),
            "configuration_digest": configuration.configuration_digest,
            "target_name": configuration.target_directory.name,
            "validations": validations,
        }
        LOGGER.info(
            "v1_preview_completed blueprints=%d validations=%d",
            len(plan.blueprint_ids),
            len(validations),
        )
        return CommandOutcome(
            0,
            json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

    def inspect(self, workspace: Path) -> CommandOutcome:
        """Return safe persisted facts for one failed generation workspace."""
        try:
            inspection = inspect_failed_workspace(self._absolute_path(workspace))
        except (OSError, ValueError):
            LOGGER.error("v1_workspace_inspection_failed")
            return CommandOutcome(1, "Failed workspace could not be inspected.")
        summary = {
            "candidate_digest": inspection.candidate_digest,
            "evidence_valid": inspection.evidence_valid,
            "failed_gates": list(inspection.failed_gates),
            "failed_stage": (
                inspection.failed_stage.value if inspection.failed_stage is not None else None
            ),
            "run_id": inspection.run_id,
            "state": inspection.state.value,
        }
        return CommandOutcome(
            0,
            json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

    def discard(self, workspace: Path) -> CommandOutcome:
        """Discard one explicitly confirmed failed workspace through exact ownership checks."""
        result = discard_failed_workspace(self._absolute_path(workspace))
        if not result.completed:
            return CommandOutcome(1, result.reason or "Failed workspace was not discarded.")
        return CommandOutcome(0, "Failed workspace discarded successfully.")

    def cleanup(self, workspace: Path) -> CommandOutcome:
        """Retry cleanup only for an exactly owned, already-published workspace."""
        result = retry_cleanup_pending_workspace(self._absolute_path(workspace))
        if not result.completed:
            return CommandOutcome(1, result.reason or "Pending cleanup did not complete.")
        return CommandOutcome(0, "Pending cleanup completed successfully.")

    def _absolute_path(self, path: Path) -> Path:
        return path if path.is_absolute() else (self._working_directory / path).absolute()
