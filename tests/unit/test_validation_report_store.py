from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.validation import (
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
)
from scaffold_compiler.validation_report_store import (
    ValidationReportCorruptionError,
    ValidationReportStore,
)


def make_report() -> ValidationReport:
    return ValidationReport(
        configuration_digest="a" * 64,
        blueprint_digest="b" * 64,
        plan_digest="c" * 64,
        candidate_digest="d" * 64,
        checks=(
            ValidationCheck(
                "pytest",
                ValidationStatus.FAIL,
                required=True,
                phase=ValidationPhase.QUALITY,
                diagnostics="Validation process exited with code 1.",
            ),
        ),
    )


class ValidationReportStoreTests(unittest.TestCase):
    def test_round_trips_a_failed_report_with_stable_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory) / ".delivery.scaffold-run"
            workspace.mkdir()
            store = ValidationReportStore(workspace / "validation_report.json")
            report = make_report()

            store.save(report, workspace)
            first_bytes = store.path.read_bytes()
            loaded = store.load(workspace)
            store.save(report, workspace)

            self.assertEqual(loaded, report)
            self.assertEqual(store.path.read_bytes(), first_bytes)

    def test_rejects_unknown_fields_invalid_enums_and_duplicate_checks(self) -> None:
        mutations = ("unknown", "enum", "duplicate")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                workspace = Path(directory) / ".delivery.scaffold-run"
                workspace.mkdir()
                store = ValidationReportStore(workspace / "validation_report.json")
                store.save(make_report(), workspace)
                record = json.loads(store.path.read_text(encoding="utf-8"))
                if mutation == "unknown":
                    record["unexpected"] = True
                elif mutation == "enum":
                    record["checks"][0]["status"] = "UNKNOWN"
                else:
                    record["checks"].append(record["checks"][0])
                store.path.write_text(json.dumps(record), encoding="utf-8")

                with self.assertRaises(ValidationReportCorruptionError):
                    store.load(workspace)


if __name__ == "__main__":
    unittest.main()
