from __future__ import annotations

import unittest

from tests.golden_projects.project_specifications import (
    GO_GENERATOR_MARKERS,
    GO_GOLDEN_PROJECT_SPECIFICATION,
)


class GoGoldenProjectSpecificationTests(unittest.TestCase):
    def test_contract_contains_module_cli_tests_quality_and_ci(self) -> None:
        specification = GO_GOLDEN_PROJECT_SPECIFICATION

        self.assertEqual(specification.matrix_id, "GO-01")
        self.assertEqual(
            specification.required_paths,
            {
                ".editorconfig",
                ".github/workflows/quality.yml",
                ".gitignore",
                "README.md",
                "calculator.go",
                "calculator_test.go",
                "go.mod",
                "main.go",
            },
        )
        self.assertEqual(specification.module_path, "example.com/example-tool")
        self.assertEqual(specification.binary_name, "example-tool")
        self.assertEqual(specification.expected_output, "scaffold compiler go example\n")

    def test_contract_excludes_generator_facilities(self) -> None:
        self.assertFalse(
            any(
                marker in path.lower()
                for path in GO_GOLDEN_PROJECT_SPECIFICATION.required_paths
                for marker in GO_GENERATOR_MARKERS
            )
        )


if __name__ == "__main__":
    unittest.main()
