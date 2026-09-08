from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.project_command_application import ProjectCommandApplication
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_VALIDATION_ADAPTER_KEY,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.project_validation_adapter_registry import (
    ProjectValidationAdapterRegistration,
    ProjectValidationRequest,
    build_project_validation_adapter_registry,
)
from scaffold_compiler.recipe_project_configuration import build_builtin_answer_normalizers
from scaffold_compiler.v1_project_compiler import (
    build_fastapi_project_assembly_adapter_registry,
)
from scaffold_compiler.v1_validation_executor import V1_VALIDATION_REPORT_GATES
from scaffold_compiler.validation import ValidationCheck, ValidationReport, ValidationStatus
from tests.golden_projects.project_specifications import GOLDEN_PROJECT_SPECIFICATIONS


def accept_candidate(request: ProjectValidationRequest) -> ValidationReport:
    return ValidationReport(
        configuration_digest=request.configuration_digest,
        blueprint_digest=request.blueprint_digest,
        plan_digest=request.candidate.plan_digest,
        candidate_digest=request.candidate.digest,
        checks=tuple(
            ValidationCheck(name, ValidationStatus.PASS, required=True)
            for name in request.required_validations
        ),
    )


def build_application(root: Path, *, run_id: str) -> ProjectCommandApplication:
    repository = Path(__file__).parents[2]
    return ProjectCommandApplication(
        catalog_root=repository / "blueprints",
        working_directory=root,
        home_directory=root / "home",
        recipe_registry=build_builtin_project_recipe_registry(),
        answer_normalizers=build_builtin_answer_normalizers(),
        assembly_registry=build_fastapi_project_assembly_adapter_registry(),
        validation_registry=build_project_validation_adapter_registry(
            (
                ProjectValidationAdapterRegistration(
                    adapter_key=FASTAPI_VALIDATION_ADAPTER_KEY,
                    validation_gates=V1_VALIDATION_REPORT_GATES,
                    adapter=accept_candidate,
                ),
            )
        ),
        validation_runtime_factory=lambda configuration, recipe, selected_run_id: object(),
        run_id_factory=lambda: run_id,
    )


def snapshot_project(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class FastApiV1V2EquivalenceTests(unittest.TestCase):
    def test_all_four_legacy_and_v2_inputs_preview_and_finalize_identically(self) -> None:
        for index, specification in enumerate(GOLDEN_PROJECT_SPECIFICATIONS, start=1):
            with self.subTest(matrix_id=specification.matrix_id), TemporaryDirectory() as directory:
                root = Path(directory)
                legacy_target = root / "legacy-delivery"
                v2_target = root / "v2-delivery"
                legacy_config = root / "legacy.json"
                v2_config = root / "v2.json"
                shared = {
                    "project_name": f"Equivalent {specification.matrix_id}",
                    "package_name": "equivalent_api",
                    "database": specification.database,
                }
                legacy_config.write_text(
                    json.dumps(
                        {
                            **shared,
                            "container": specification.container,
                            "target_directory": str(legacy_target),
                        }
                    ),
                    encoding="utf-8",
                )
                v2_config.write_text(
                    json.dumps(
                        {
                            "answers": {
                                "database": specification.database,
                                "delivery": specification.container,
                                "package_name": "equivalent_api",
                            },
                            "project_name": shared["project_name"],
                            "recipe": "python-fastapi-service",
                            "schema_version": 2,
                            "target_directory": str(v2_target),
                        }
                    ),
                    encoding="utf-8",
                )
                legacy_application = build_application(root, run_id=f"legacy-{index}")
                v2_application = build_application(root, run_id=f"v2-{index}")

                legacy_preview = json.loads(legacy_application.preview(legacy_config).message)
                v2_preview = json.loads(v2_application.preview(v2_config).message)
                for volatile_key in ("configuration_digest", "target_name"):
                    legacy_preview.pop(volatile_key)
                    v2_preview.pop(volatile_key)
                self.assertEqual(legacy_preview, v2_preview)

                legacy_outcome = legacy_application.run_non_interactive(legacy_config)
                v2_outcome = v2_application.run_non_interactive(v2_config)

                self.assertEqual(legacy_outcome.exit_code, 0, legacy_outcome.message)
                self.assertEqual(v2_outcome.exit_code, 0, v2_outcome.message)
                self.assertEqual(snapshot_project(legacy_target), snapshot_project(v2_target))
                self.assertFalse((root / f".legacy-delivery.scaffold-legacy-{index}").exists())
                self.assertFalse((root / f".v2-delivery.scaffold-v2-{index}").exists())


if __name__ == "__main__":
    unittest.main()
