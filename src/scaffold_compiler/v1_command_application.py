"""Concrete V1 application boundary for a fully confirmed non-interactive run."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from scaffold_compiler.candidate_project_assembler import CandidateAssemblyResult
from scaffold_compiler.command_line_interface import CommandOutcome
from scaffold_compiler.generation_workflow import (
    CompletedGeneration,
    execute_non_interactive_generation,
)
from scaffold_compiler.project_configuration import (
    DatabaseChoice,
    ProjectConfiguration,
    parse_project_configuration,
)
from scaffold_compiler.v1_validation_executor import execute_v1_validation
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
        uv_executable: Path,
        environment: Mapping[str, str] | None = None,
        generation_runner: GenerationRunner = execute_non_interactive_generation,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._catalog_root = catalog_root.resolve(strict=True)
        self._working_directory = working_directory.resolve(strict=True)
        self._home_directory = home_directory.resolve(strict=False)
        self._uv_executable = uv_executable.resolve(strict=True)
        self._environment = dict(os.environ if environment is None else environment)
        self._generation_runner = generation_runner
        self._run_id_factory = run_id_factory or (lambda: uuid.uuid4().hex)

    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        """Load one config and execute the complete confirmed transaction."""
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

        def validator(
            candidate: CandidateAssemblyResult,
            configuration_digest: str,
            blueprint_digest: str,
            required_validations: tuple[str, ...],
            validation_environment: Path,
        ) -> ValidationReport:
            report = execute_v1_validation(
                candidate,
                configuration_digest,
                blueprint_digest,
                required_validations,
                package_name=configuration.package_name,
                uv_executable=self._uv_executable,
                validation_environment=validation_environment,
                database_url=database_url,
                forbidden_absolute_paths=(
                    self._catalog_root.parent,
                    candidate.root.parent,
                ),
                secrets=(),
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
        except Exception as error:
            LOGGER.error(
                "v1_non_interactive_run_failed run_id=%s error_type=%s",
                run_id,
                type(error).__name__,
            )
            return CommandOutcome(1, "Project generation did not complete.")
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
        return self._interactive_unavailable()

    def generate(self, config_path: Path) -> CommandOutcome:
        return self._interactive_unavailable()

    def validate(self, workspace: Path) -> CommandOutcome:
        return self._interactive_unavailable()

    def status(self, workspace: Path) -> CommandOutcome:
        return self._interactive_unavailable()

    def regenerate(
        self,
        workspace: Path,
        config_path: Path,
        *,
        discard_candidate: bool,
    ) -> CommandOutcome:
        return self._interactive_unavailable()

    def finalize(self, workspace: Path) -> CommandOutcome:
        return self._interactive_unavailable()

    def cancel(self, workspace: Path) -> CommandOutcome:
        return self._interactive_unavailable()

    @staticmethod
    def _interactive_unavailable() -> CommandOutcome:
        return CommandOutcome(1, "Interactive lifecycle commands are not available in V1.")
