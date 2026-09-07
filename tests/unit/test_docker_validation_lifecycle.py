from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.docker_validation_commands import DockerValidationCommands
from scaffold_compiler.docker_validation_lifecycle import execute_docker_validation_lifecycle
from scaffold_compiler.docker_validation_resources import DockerValidationResources
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationStatus,
)


def result(
    return_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> ControlledProcessResult:
    return ControlledProcessResult(return_code, False, stdout, stderr, False, 1)


class RecordingRunner:
    def __init__(self, results: dict[str, list[ControlledProcessResult]] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    def __call__(self, specification: ControlledProcessSpec) -> ControlledProcessResult:
        self.calls.append(specification.name)
        configured = self.results.get(specification.name)
        if configured:
            return configured.pop(0)
        if specification.name.endswith("ownership"):
            return result(stdout="626f6c677e7d5521\n")
        return result()


def commands(root: Path, *, compose: bool = False) -> DockerValidationCommands:
    candidate = root / "candidate"
    candidate.mkdir()
    docker = root / "docker"
    docker.write_bytes(b"")
    resources = DockerValidationResources.create("run-1", "a" * 64)
    return DockerValidationCommands(
        docker,
        candidate,
        resources,
        compose_password="temporary-secret" if compose else None,
    )


class DockerValidationLifecycleTests(unittest.TestCase):
    def test_image_lifecycle_passes_and_always_verifies_exact_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner(
                {
                    "container-health": [
                        result(stdout='"starting"\n'),
                        result(stdout='"healthy"\n'),
                    ],
                }
            )

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build", "container-non-root", "container-health"),
                process_runner=runner,
                sleep=lambda _seconds: None,
            )

            self.assertTrue(all(check.status is ValidationStatus.PASS for check in checks))
            self.assertEqual(
                runner.calls,
                [
                    "docker-image-preflight",
                    "container-preflight",
                    "docker-build",
                    "docker-image-ownership",
                    "container-non-root",
                    "container-health-start",
                    "container-ownership",
                    "container-health",
                    "container-health",
                    "container-ownership",
                    "container-cleanup",
                    "docker-image-ownership",
                    "docker-image-cleanup",
                    "container-preflight",
                    "docker-image-preflight",
                ],
            )
            self.assertEqual(
                [check.name for check in checks],
                [
                    "docker-build",
                    "container-non-root",
                    "container-health",
                    "docker-cleanup",
                ],
            )

    def test_preexisting_resource_refuses_all_mutating_commands(self) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner({"docker-image-preflight": [result(stdout="image-id\n")]})

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build", "container-non-root", "container-health"),
                process_runner=runner,
            )

            self.assertEqual(runner.calls, ["docker-image-preflight", "container-preflight"])
            statuses = {check.name: check.status for check in checks}
            self.assertIs(statuses["docker-build"], ValidationStatus.FAIL)
            self.assertIs(statuses["container-non-root"], ValidationStatus.SKIPPED)
            self.assertIs(statuses["container-health"], ValidationStatus.SKIPPED)
            self.assertIs(statuses["docker-cleanup"], ValidationStatus.PASS)

    def test_unhealthy_container_is_cleaned_and_cleanup_failure_blocks_success(self) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner(
                {
                    "container-health": [result(stdout='"unhealthy"\n')],
                    "container-cleanup": [result(return_code=1)],
                    "container-preflight": [result(), result(stdout="container-id\n")],
                }
            )

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build", "container-non-root", "container-health"),
                process_runner=runner,
            )

            statuses = {check.name: check.status for check in checks}
            self.assertIs(statuses["container-health"], ValidationStatus.FAIL)
            self.assertIs(statuses["docker-cleanup"], ValidationStatus.FAIL)
            self.assertIn("docker-image-cleanup", runner.calls)

    def test_changed_image_ownership_is_never_deleted(self) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner(
                {
                    "docker-image-ownership": [
                        result(stdout="626f6c677e7d5521\n"),
                        result(stdout="different-owner\n"),
                    ],
                    "docker-image-preflight": [result(), result(stdout="image-id\n")],
                    "container-health": [result(stdout='"healthy"\n')],
                }
            )

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build", "container-non-root", "container-health"),
                process_runner=runner,
            )

            self.assertNotIn("docker-image-cleanup", runner.calls)
            self.assertIs(
                {check.name: check.status for check in checks}["docker-cleanup"],
                ValidationStatus.FAIL,
            )

    def test_compose_up_failure_still_runs_down_and_residue_check(self) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner({"compose-up": [result(return_code=1)]})

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory), compose=True),
                ("compose-config", "compose-up", "compose-health", "compose-cleanup"),
                process_runner=runner,
            )

            self.assertIn("compose-cleanup", runner.calls)
            self.assertEqual(runner.calls[-1], "compose-preflight")
            statuses = {check.name: check.status for check in checks}
            self.assertIs(statuses["compose-up"], ValidationStatus.FAIL)
            self.assertIs(statuses["compose-health"], ValidationStatus.SKIPPED)
            self.assertIs(statuses["compose-cleanup"], ValidationStatus.PASS)

    def test_compose_health_proves_container_health_for_a_dependent_application(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            runner = RecordingRunner(
                {
                    "compose-health": [
                        result(stdout="application\ndatabase\n"),
                    ],
                }
            )

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory), compose=True),
                (
                    "docker-build",
                    "container-non-root",
                    "container-health",
                    "compose-config",
                    "compose-up",
                    "compose-health",
                    "compose-cleanup",
                ),
                process_runner=runner,
            )

        statuses = {check.name: check.status for check in checks}
        self.assertIs(statuses["container-health"], ValidationStatus.PASS)
        self.assertIs(statuses["compose-health"], ValidationStatus.PASS)
        self.assertNotIn("container-health-start", runner.calls)

    def test_process_start_error_becomes_a_failed_gate_and_cleanup_still_runs(self) -> None:
        with TemporaryDirectory() as directory:
            calls: list[str] = []

            def fail_build(specification: ControlledProcessSpec) -> ControlledProcessResult:
                calls.append(specification.name)
                if specification.name == "docker-build":
                    raise OSError("simulated process start failure")
                if specification.name.endswith("ownership"):
                    return result(stdout="626f6c677e7d5521\n")
                return result()

            checks = execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build", "container-non-root", "container-health"),
                process_runner=fail_build,
            )

            statuses = {check.name: check.status for check in checks}
            self.assertIs(statuses["docker-build"], ValidationStatus.FAIL)
            self.assertIs(statuses["docker-cleanup"], ValidationStatus.PASS)
            self.assertIn("docker-image-cleanup", calls)

    def test_build_failure_logs_bounded_redacted_process_evidence(self) -> None:
        with (
            TemporaryDirectory() as directory,
            self.assertLogs("scaffold_compiler.validation", level="ERROR") as logs,
        ):
            execute_docker_validation_lifecycle(
                commands(Path(directory)),
                ("docker-build",),
                process_runner=RecordingRunner(
                    {
                        "docker-build": [
                            result(
                                return_code=1,
                                stderr="[REDACTED] pull access denied",
                            )
                        ]
                    }
                ),
            )

        visible = "\n".join(logs.output)
        self.assertIn("validation_process_failed", visible)
        self.assertIn("name=docker-build", visible)
        self.assertIn("pull access denied", visible)
        self.assertIn("[REDACTED]", visible)


if __name__ == "__main__":
    unittest.main()
