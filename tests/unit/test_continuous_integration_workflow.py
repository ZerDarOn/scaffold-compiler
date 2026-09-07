from __future__ import annotations

import re
import unittest
from pathlib import Path

ACTION_REFERENCE = re.compile(
    r"uses:\s+(actions/(?:checkout|setup-python)|astral-sh/setup-uv)@([0-9a-f]{40})"
)
EXPECTED_ACTION_REVISIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "astral-sh/setup-uv": "c771a70e6277c0a99b617c7a806ffedaca235ff9",
}


class ContinuousIntegrationWorkflowTests(unittest.TestCase):
    def test_quality_workflow_is_cross_platform_locked_and_fail_closed(self) -> None:
        repository = Path(__file__).parents[2]
        workflow = (repository / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("os: [ubuntu-latest, windows-latest]", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("--junitxml=core-test-results.xml", workflow)
        self.assertIn("::error title=pytest failure::", workflow)

        references = ACTION_REFERENCE.findall(workflow)
        self.assertEqual(len(references), 6)
        for action, revision in EXPECTED_ACTION_REVISIONS.items():
            self.assertEqual(references.count((action, revision)), 2)

    def test_release_job_requires_all_real_v1_acceptance_environments(self) -> None:
        repository = Path(__file__).parents[2]
        workflow = (repository / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("image: postgres:18.1-bookworm", workflow)
        self.assertIn("SCAFFOLD_TEST_DATABASE_URL:", workflow)
        self.assertIn("docker version --format", workflow)
        self.assertIn("docker compose version", workflow)
        self.assertIn("tests/acceptance/test_disposable_release_workflow.py", workflow)
        self.assertIn("tests/acceptance/test_postgres_disposable_release_workflow.py", workflow)
        self.assertIn("tests/acceptance/test_docker_disposable_release_workflow.py", workflow)
        self.assertIn("--junitxml=release-test-results.xml", workflow)


if __name__ == "__main__":
    unittest.main()
