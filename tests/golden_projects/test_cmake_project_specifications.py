from __future__ import annotations

import unittest

from tests.golden_projects.project_specifications import (
    CMAKE_COMMON_PATHS,
    CMAKE_GENERATOR_MARKERS,
    CMAKE_GOLDEN_PROJECT_SPECIFICATIONS,
    CMAKE_STRICT_WARNING_PATHS,
)


class CMakeGoldenProjectSpecificationTests(unittest.TestCase):
    def test_strict_warning_matrix_has_independent_expected_trees(self) -> None:
        self.assertEqual(
            {
                (specification.matrix_id, specification.strict_warnings)
                for specification in CMAKE_GOLDEN_PROJECT_SPECIFICATIONS
            },
            {("C-01", False), ("C-02", True)},
        )
        for specification in CMAKE_GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id):
                self.assertGreaterEqual(specification.required_paths, CMAKE_COMMON_PATHS)
                if specification.strict_warnings:
                    self.assertGreaterEqual(
                        specification.required_paths,
                        CMAKE_STRICT_WARNING_PATHS,
                    )
                    self.assertIn(
                        "include(cmake/StrictWarnings.cmake)",
                        specification.required_cmake_tokens,
                    )
                else:
                    self.assertGreaterEqual(
                        specification.forbidden_paths,
                        CMAKE_STRICT_WARNING_PATHS,
                    )
                    self.assertIn("StrictWarnings", specification.forbidden_cmake_tokens)

    def test_output_contract_contains_library_cli_tests_presets_and_ci(self) -> None:
        for specification in CMAKE_GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id):
                self.assertIn("include/{target_name}/calculator.h", specification.required_paths)
                self.assertIn("src/calculator.c", specification.required_paths)
                self.assertIn("src/main.c", specification.required_paths)
                self.assertIn("tests/test_calculator.c", specification.required_paths)
                self.assertIn("CMakePresets.json", specification.required_paths)
                self.assertIn(".github/workflows/quality.yml", specification.required_paths)
                self.assertEqual(
                    specification.expected_output,
                    "scaffold compiler c example\n",
                )

    def test_final_project_contract_excludes_all_generator_facilities(self) -> None:
        for specification in CMAKE_GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id):
                self.assertFalse(
                    any(
                        marker in path.lower()
                        for path in specification.required_paths
                        for marker in CMAKE_GENERATOR_MARKERS
                    )
                )


if __name__ == "__main__":
    unittest.main()
