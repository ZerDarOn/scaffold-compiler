from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    compile_recipe_blueprint_plan,
    resolve_recipe_capabilities,
)
from scaffold_compiler.cmake_project_assembly_adapter import (
    build_cmake_project_assembly_adapter_registry,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyRequest,
    assemble_project_candidate,
)
from scaffold_compiler.project_recipe_registry import (
    CMAKE_RECIPE_ID,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.recipe_project_configuration import (
    parse_builtin_recipe_project_configuration,
)
from scaffold_compiler.validation import ValidationStatus, scan_candidate_static_safety
from tests.golden_projects.project_specifications import (
    CMAKE_GENERATOR_MARKERS,
    CMAKE_GOLDEN_PROJECT_SPECIFICATIONS,
)


class CMakeGeneratedProjectTests(unittest.TestCase):
    def test_both_warning_modes_match_their_independent_golden_contract(self) -> None:
        catalog_root = Path(__file__).parents[2] / "blueprints"
        catalog = load_blueprint_catalog(catalog_root)
        recipe = build_builtin_project_recipe_registry().get(CMAKE_RECIPE_ID)
        registry = build_cmake_project_assembly_adapter_registry()

        for specification in CMAKE_GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id), TemporaryDirectory() as directory:
                root = Path(directory)
                configuration = parse_builtin_recipe_project_configuration(
                    {
                        "schema_version": 2,
                        "recipe": CMAKE_RECIPE_ID,
                        "project_name": "Scaffold Compiler C Example",
                        "target_directory": "delivery",
                        "answers": {
                            "strict_warnings": specification.strict_warnings,
                            "target_name": "example_cli",
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

                paths = {record.path for record in candidate.files}
                required = {
                    path.format(target_name="example_cli") for path in specification.required_paths
                }
                forbidden = {
                    path.format(target_name="example_cli") for path in specification.forbidden_paths
                }
                self.assertEqual(paths, required)
                self.assertTrue(paths.isdisjoint(forbidden))
                cmake = (candidate.root / "CMakeLists.txt").read_text(encoding="utf-8")
                for token in specification.required_cmake_tokens:
                    self.assertIn(token, cmake)
                for token in specification.forbidden_cmake_tokens:
                    self.assertNotIn(token, cmake)
                all_text = "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in candidate.root.rglob("*")
                    if path.is_file()
                ).lower()
                self.assertFalse(any(marker in all_text for marker in CMAKE_GENERATOR_MARKERS))
                self.assertIn("EXAMPLE_CLI_CALCULATOR_H", all_text.upper())
                self.assertIn("ubuntu-latest", all_text)
                self.assertIn("windows-latest", all_text)
                safety = scan_candidate_static_safety(
                    candidate,
                    forbidden_absolute_paths=(catalog_root.parent.resolve(), workspace.resolve()),
                    secrets=(),
                )
                self.assertIs(safety.status, ValidationStatus.PASS)


if __name__ == "__main__":
    unittest.main()
