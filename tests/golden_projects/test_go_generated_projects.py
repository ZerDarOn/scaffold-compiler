from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    compile_recipe_blueprint_plan,
    resolve_recipe_capabilities,
)
from scaffold_compiler.go_project_assembly_adapter import (
    build_go_project_assembly_adapter_registry,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyRequest,
    assemble_project_candidate,
)
from scaffold_compiler.project_recipe_registry import (
    GO_RECIPE_ID,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    parse_builtin_recipe_project_configuration,
)
from scaffold_compiler.validation import ValidationStatus, scan_candidate_static_safety
from tests.golden_projects.project_specifications import (
    GO_GENERATOR_MARKERS,
    GO_GOLDEN_PROJECT_SPECIFICATION,
)


class GoGeneratedProjectTests(unittest.TestCase):
    def test_project_matches_its_independent_golden_contract(self) -> None:
        catalog_root = Path(__file__).parents[2] / "blueprints"
        catalog = load_blueprint_catalog(catalog_root)
        recipe = build_builtin_project_recipe_registry().get(GO_RECIPE_ID)
        registry = build_go_project_assembly_adapter_registry()
        specification = GO_GOLDEN_PROJECT_SPECIFICATION

        with TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = parse_builtin_recipe_project_configuration(
                {
                    "schema_version": 2,
                    "recipe": GO_RECIPE_ID,
                    "project_name": "Example Tool",
                    "target_directory": "delivery",
                    "answers": {
                        "binary_name": specification.binary_name,
                        "module_path": specification.module_path,
                    },
                },
                working_directory=root,
                home_directory=root / "home",
            )
            plan = compile_recipe_blueprint_plan(
                catalog,
                recipe,
                resolve_recipe_capabilities(recipe, configuration.answers),
            )
            workspace = root / "workspace"
            workspace.mkdir()

            candidate = assemble_project_candidate(
                ProjectAssemblyRequest(
                    configuration=configuration,
                    recipe=recipe,
                    catalog=catalog,
                    plan=plan,
                    workspace=workspace,
                    catalog_root=catalog_root,
                ),
                registry,
            )

            self.assertEqual(
                {record.path for record in candidate.files}, specification.required_paths
            )
            self.assertEqual(plan.blueprint_ids, ("go-project-quality", "go-cli-application"))
            self.assertIn(
                f"module {specification.module_path}\n", (candidate.root / "go.mod").read_text()
            )
            readme = (candidate.root / "README.md").read_text(encoding="utf-8")
            self.assertIn(specification.binary_name, readme)
            all_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in candidate.root.rglob("*")
                if path.is_file()
            ).lower()
            self.assertFalse(any(marker in all_text for marker in GO_GENERATOR_MARKERS))
            self.assertIn("ubuntu-latest", all_text)
            self.assertIn("windows-latest", all_text)
            safety = scan_candidate_static_safety(
                candidate,
                forbidden_absolute_paths=(catalog_root.parent.resolve(), workspace.resolve()),
                secrets=(),
            )
            self.assertIs(safety.status, ValidationStatus.PASS, safety.diagnostics)


if __name__ == "__main__":
    unittest.main()
