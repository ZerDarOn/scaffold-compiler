from __future__ import annotations

import unittest
from collections.abc import Mapping

from scaffold_compiler.version_policy import (
    GENERATED_PROJECT_DEVELOPMENT_DEPENDENCIES,
    GENERATED_PROJECT_POSTGRES_DEPENDENCIES,
    GENERATED_PROJECT_PYTHON,
    GENERATED_PROJECT_RUNTIME_DEPENDENCIES,
    GENERATOR_MINIMUM_PYTHON,
    UV_VERSION,
)


class VersionPolicyTests(unittest.TestCase):
    def test_generator_uses_standard_library_available_in_python_311(self) -> None:
        self.assertEqual(GENERATOR_MINIMUM_PYTHON, (3, 11))

    def test_generated_project_targets_python_314(self) -> None:
        self.assertEqual(GENERATED_PROJECT_PYTHON, "3.14")

    def test_uv_version_is_exact(self) -> None:
        self.assertEqual(UV_VERSION, "0.12.9")

    def test_generated_project_dependencies_are_exact_versions(self) -> None:
        dependency_groups: tuple[Mapping[str, str], ...] = (
            GENERATED_PROJECT_RUNTIME_DEPENDENCIES,
            GENERATED_PROJECT_POSTGRES_DEPENDENCIES,
            GENERATED_PROJECT_DEVELOPMENT_DEPENDENCIES,
        )

        for dependency_group in dependency_groups:
            with self.subTest(dependency_group=dependency_group):
                for package_name, version in dependency_group.items():
                    self.assertTrue(package_name)
                    self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
