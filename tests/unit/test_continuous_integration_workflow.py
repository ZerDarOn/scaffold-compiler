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
UPLOAD_ARTIFACT_REVISION = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_REVISION = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"


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
        self.assertIn("- name: Require CMake toolchain", workflow)
        self.assertIn("cmake --version", workflow)
        self.assertIn("ctest --version", workflow)
        self.assertIn("ninja --version", workflow)
        self.assertIn("tests/acceptance/test_cmake_disposable_release_workflow.py", workflow)
        self.assertIn("tests/acceptance/test_fastapi_v1_v2_equivalence.py", workflow)
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

    def test_release_workflow_reuses_quality_and_uploads_a_versioned_capsule(self) -> None:
        repository = Path(__file__).parents[2]
        quality = (repository / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        release = (repository / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_call:", quality)
        self.assertIn("branches: [main]", quality)
        self.assertIn("workflow_dispatch:", release)
        self.assertIn('tags: ["v*"]', release)
        self.assertIn("uses: ./.github/workflows/quality.yml", release)
        self.assertIn("needs: quality", release)
        self.assertIn("python -m scaffold_compiler.release_capsule_command", release)
        self.assertIn("--archive", release)
        self.assertIn("Get-FileHash -Algorithm SHA256", release)
        self.assertIn("Release archive checksum verification failed.", release)
        self.assertIn(f"actions/upload-artifact@{UPLOAD_ARTIFACT_REVISION}", release)
        self.assertIn(f"actions/download-artifact@{DOWNLOAD_ARTIFACT_REVISION}", release)
        self.assertIn("if-no-files-found: error", release)
        self.assertIn("contents: write", release)
        self.assertEqual(release.count("contents: write"), 1)
        self.assertIn("gh release create", release)
        self.assertIn("--verify-tag", release)
        self.assertIn("--generate-notes", release)
        self.assertIn("github.ref_type == 'tag'", release)
        self.assertIn("GH_TOKEN: ${{ github.token }}", release)
        self.assertIn("GH_REPO: ${{ github.repository }}", release)
        self.assertIn("actions: read", release)
        self.assertNotIn("pull_request_target", release)


if __name__ == "__main__":
    unittest.main()
