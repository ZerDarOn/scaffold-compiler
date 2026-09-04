from __future__ import annotations

import re
import unittest

from scaffold_compiler.docker_validation_resources import DockerValidationResources


class DockerValidationResourcesTests(unittest.TestCase):
    def test_names_are_deterministic_bounded_and_hide_the_raw_run_id(self) -> None:
        resources = DockerValidationResources.create(
            run_id="customer-project/run 001",
            candidate_digest="a" * 64,
        )
        repeated = DockerValidationResources.create(
            run_id="customer-project/run 001",
            candidate_digest="a" * 64,
        )

        self.assertEqual(resources, repeated)
        self.assertNotIn("customer", resources.image_tag)
        self.assertRegex(resources.image_tag, r"^scaffold-validation:[0-9a-f]{16}$")
        self.assertRegex(resources.container_name, r"^scaffold-validation-[0-9a-f]{16}$")
        self.assertRegex(resources.compose_project, r"^scaffold-validation-[0-9a-f]{16}$")
        self.assertEqual(resources.ownership_label_name, "io.scaffold-compiler.validation")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", resources.ownership_label_value))

    def test_run_or_candidate_change_produces_a_distinct_resource_identity(self) -> None:
        baseline = DockerValidationResources.create("run-1", "a" * 64)

        self.assertNotEqual(
            baseline.ownership_label_value,
            DockerValidationResources.create("run-2", "a" * 64).ownership_label_value,
        )
        self.assertNotEqual(
            baseline.ownership_label_value,
            DockerValidationResources.create("run-1", "b" * 64).ownership_label_value,
        )

    def test_invalid_identity_is_rejected_before_resource_names_are_created(self) -> None:
        invalid_cases = (
            ("", "a" * 64),
            ("x" * 129, "a" * 64),
            ("run-1", "A" * 64),
            ("run-1", "short"),
        )

        for run_id, candidate_digest in invalid_cases:
            with (
                self.subTest(run_id=run_id, candidate_digest=candidate_digest),
                self.assertRaises(ValueError),
            ):
                DockerValidationResources.create(run_id, candidate_digest)


if __name__ == "__main__":
    unittest.main()
