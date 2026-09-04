from __future__ import annotations

import unittest

from tests.golden_projects.project_specifications import (
    COMPOSE_PATHS,
    DOCKER_PATHS,
    GOLDEN_PROJECT_SPECIFICATIONS,
    POSTGRES_DEPENDENCIES,
    POSTGRES_PATHS,
)


class GoldenProjectSpecificationTests(unittest.TestCase):
    def test_all_four_v1_combinations_have_independent_expectations(self) -> None:
        actual = {
            (specification.database, specification.container)
            for specification in GOLDEN_PROJECT_SPECIFICATIONS
        }

        self.assertEqual(
            actual,
            {
                ("none", "none"),
                ("none", "docker"),
                ("postgres", "none"),
                ("postgres", "docker"),
            },
        )
        self.assertEqual(
            {specification.matrix_id for specification in GOLDEN_PROJECT_SPECIFICATIONS},
            {"M-01", "M-02", "M-03", "M-04"},
        )

    def test_unselected_capabilities_have_no_files_or_dependencies(self) -> None:
        for specification in GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id):
                if specification.database == "none":
                    self.assertTrue(specification.forbidden_paths >= POSTGRES_PATHS)
                    self.assertTrue(
                        POSTGRES_DEPENDENCIES.isdisjoint(specification.runtime_dependencies)
                    )
                else:
                    self.assertTrue(specification.required_paths >= POSTGRES_PATHS)
                    self.assertTrue(specification.runtime_dependencies >= POSTGRES_DEPENDENCIES)

                if specification.container == "none":
                    self.assertTrue(specification.forbidden_paths >= DOCKER_PATHS)
                else:
                    self.assertTrue(specification.required_paths >= DOCKER_PATHS)

                if (specification.database, specification.container) == (
                    "postgres",
                    "docker",
                ):
                    self.assertTrue(specification.required_paths >= COMPOSE_PATHS)
                else:
                    self.assertTrue(specification.forbidden_paths >= COMPOSE_PATHS)

    def test_every_specification_requires_the_fixed_application_contract(self) -> None:
        for specification in GOLDEN_PROJECT_SPECIFICATIONS:
            with self.subTest(matrix_id=specification.matrix_id):
                self.assertEqual(
                    specification.required_endpoints,
                    ("/api/v1", "/health/live", "/health/ready", "/openapi.json"),
                )
                self.assertEqual(
                    specification.asgi_entrypoint,
                    "{package_name}.asgi:application",
                )
                self.assertFalse(
                    any(
                        marker in path
                        for path in specification.required_paths
                        for marker in ("generator", "blueprint", "journal", "run_id")
                    )
                )


if __name__ == "__main__":
    unittest.main()
