"""Atomic external persistence for bound validation evidence."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Final

from scaffold_compiler.validation import (
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
)

_SCHEMA_VERSION: Final = 1
_REPORT_NAME: Final = "validation_report.json"


class ValidationReportCorruptionError(ValueError):
    """Raised when persisted validation evidence is incomplete or unsupported."""


class ValidationReportStore:
    """Persist one complete validation report outside the candidate project."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, report: ValidationReport, workspace: Path) -> None:
        """Atomically replace the fixed validation evidence file."""
        trusted_workspace = workspace.resolve(strict=True)
        if self.path != trusted_workspace / _REPORT_NAME:
            raise ValueError("Validation report is not the fixed workspace file.")
        payload = {
            "blueprint_digest": report.blueprint_digest,
            "candidate_digest": report.candidate_digest,
            "checks": [
                {
                    "diagnostics": check.diagnostics,
                    "name": check.name,
                    "phase": check.phase.value,
                    "required": check.required,
                    "status": check.status.value,
                }
                for check in report.checks
            ],
            "configuration_digest": report.configuration_digest,
            "plan_digest": report.plan_digest,
            "schema_version": _SCHEMA_VERSION,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
        temporary = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def load(self, workspace: Path) -> ValidationReport:
        """Strictly load the fixed validation evidence file."""
        try:
            trusted_workspace = workspace.resolve(strict=True)
            if self.path != trusted_workspace / _REPORT_NAME:
                raise ValueError("Validation report is not the fixed workspace file.")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "blueprint_digest",
                "candidate_digest",
                "checks",
                "configuration_digest",
                "plan_digest",
                "schema_version",
            }:
                raise ValueError("Validation report fields are invalid.")
            if raw["schema_version"] != _SCHEMA_VERSION:
                raise ValueError("Validation report schema is unsupported.")
            return ValidationReport(
                configuration_digest=_required_string(raw, "configuration_digest"),
                blueprint_digest=_required_string(raw, "blueprint_digest"),
                plan_digest=_required_string(raw, "plan_digest"),
                candidate_digest=_required_string(raw, "candidate_digest"),
                checks=_parse_checks(raw["checks"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValidationReportCorruptionError(
                "Validation report evidence is invalid."
            ) from error


def _parse_checks(raw_checks: object) -> tuple[ValidationCheck, ...]:
    if not isinstance(raw_checks, list):
        raise TypeError("Validation checks must be a list.")
    checks: list[ValidationCheck] = []
    for raw in raw_checks:
        if not isinstance(raw, dict) or set(raw) != {
            "diagnostics",
            "name",
            "phase",
            "required",
            "status",
        }:
            raise ValueError("Validation check fields are invalid.")
        required = raw["required"]
        if not isinstance(required, bool):
            raise TypeError("Validation required flag is invalid.")
        checks.append(
            ValidationCheck(
                name=_required_string(raw, "name"),
                status=ValidationStatus(_required_string(raw, "status")),
                required=required,
                phase=ValidationPhase(_required_string(raw, "phase")),
                diagnostics=_required_string(raw, "diagnostics"),
            )
        )
    return tuple(checks)


def _required_string(record: dict[str, object], field: str) -> str:
    value = record[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string.")
    return value
