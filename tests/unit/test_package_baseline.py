from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PackageBaselineTests(unittest.TestCase):
    def test_package_can_be_imported(self) -> None:
        import scaffold_compiler

        pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(scaffold_compiler.__version__, "2.0.0")
        self.assertEqual(pyproject["project"]["dynamic"], ["version"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["dynamic"]["version"],
            {"attr": "scaffold_compiler.__version__"},
        )

    def test_runtime_dependency_list_is_empty(self) -> None:
        pyproject_path = REPOSITORY_ROOT / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(pyproject["project"]["dependencies"], [])

    def test_pytest_path_includes_source_and_test_packages(self) -> None:
        pyproject_path = REPOSITORY_ROOT / "pyproject.toml"
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(pyproject["tool"]["pytest"]["ini_options"]["pythonpath"], ["src", "."])

    def test_source_syntax_is_compatible_with_python_311(self) -> None:
        source_root = REPOSITORY_ROOT / "src"

        for source_path in source_root.rglob("*.py"):
            with self.subTest(source_path=source_path):
                ast.parse(
                    source_path.read_text(encoding="utf-8"),
                    filename=str(source_path),
                    feature_version=(3, 11),
                )


if __name__ == "__main__":
    unittest.main()
