from __future__ import annotations

import io
import unittest
from pathlib import Path

from scaffold_compiler.command_line_interface import (
    CommandOutcome,
    run_command_line,
)


class RecordingCommandService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.outcome = CommandOutcome(exit_code=0, message="completed")

    def preview(self, config_path: Path) -> CommandOutcome:
        return self._record("preview", config_path)

    def generate(self, config_path: Path) -> CommandOutcome:
        return self._record("generate", config_path)

    def validate(self, workspace: Path) -> CommandOutcome:
        return self._record("validate", workspace)

    def status(self, workspace: Path) -> CommandOutcome:
        return self._record("status", workspace)

    def regenerate(
        self,
        workspace: Path,
        config_path: Path,
        *,
        discard_candidate: bool,
    ) -> CommandOutcome:
        return self._record("regenerate", workspace, config_path, discard_candidate)

    def finalize(self, workspace: Path) -> CommandOutcome:
        return self._record("finalize", workspace)

    def cancel(self, workspace: Path) -> CommandOutcome:
        return self._record("cancel", workspace)

    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        return self._record("run-non-interactive", config_path)

    def _record(self, name: str, *arguments: object) -> CommandOutcome:
        self.calls.append((name, arguments))
        return self.outcome


class CommandLineInterfaceTests(unittest.TestCase):
    def execute(
        self,
        arguments: list[str],
        service: RecordingCommandService | None = None,
    ) -> tuple[int, str, str, RecordingCommandService]:
        selected_service = service or RecordingCommandService()
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = run_command_line(
            arguments,
            service=selected_service,
            stdout=stdout,
            stderr=stderr,
        )
        return exit_code, stdout.getvalue(), stderr.getvalue(), selected_service

    def test_preview_and_generate_accept_configuration_files(self) -> None:
        for command in ("preview", "generate"):
            with self.subTest(command=command):
                exit_code, stdout, stderr, service = self.execute(
                    [command, "--config", "project.json"]
                )

                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout, "completed\n")
                self.assertEqual(stderr, "")
                self.assertEqual(service.calls, [(command, (Path("project.json"),))])

    def test_workspace_commands_route_only_validated_arguments(self) -> None:
        command_arguments = (
            ("validate", ["validate", "--workspace", "work"], (Path("work"),)),
            ("status", ["status", "--workspace", "work"], (Path("work"),)),
            (
                "regenerate",
                [
                    "regenerate",
                    "--workspace",
                    "work",
                    "--config",
                    "next.json",
                    "--discard-candidate",
                ],
                (Path("work"), Path("next.json"), True),
            ),
            (
                "cancel",
                ["cancel", "--workspace", "work", "--confirm", "CANCEL"],
                (Path("work"),),
            ),
        )

        for expected_command, arguments, expected_arguments in command_arguments:
            with self.subTest(command=expected_command):
                exit_code, _stdout, stderr, service = self.execute(arguments)
                self.assertEqual(exit_code, 0)
                self.assertEqual(stderr, "")
                self.assertEqual(service.calls, [(expected_command, expected_arguments)])

    def test_finalize_requires_the_exact_explicit_confirmation(self) -> None:
        invalid_arguments = (
            ["finalize", "--workspace", "work"],
            ["finalize", "--workspace", "work", "--confirm", "yes"],
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                exit_code, _stdout, stderr, service = self.execute(arguments)
                self.assertEqual(exit_code, 2)
                self.assertIn("FINALIZE", stderr)
                self.assertEqual(service.calls, [])

        exit_code, _stdout, stderr, service = self.execute(
            ["finalize", "--workspace", "work", "--confirm", "FINALIZE"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(service.calls, [("finalize", (Path("work"),))])

    def test_non_interactive_run_cannot_bypass_finalize_confirmation(self) -> None:
        missing_confirmation = [
            "run",
            "--config",
            "project.json",
            "--non-interactive",
        ]
        exit_code, _stdout, stderr, service = self.execute(missing_confirmation)
        self.assertEqual(exit_code, 2)
        self.assertIn("FINALIZE", stderr)
        self.assertEqual(service.calls, [])

        exit_code, _stdout, stderr, service = self.execute(
            [*missing_confirmation, "--confirm-finalize", "FINALIZE"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            service.calls,
            [("run-non-interactive", (Path("project.json"),))],
        )

    def test_domain_failure_returns_stable_nonzero_exit_and_stderr(self) -> None:
        service = RecordingCommandService()
        service.outcome = CommandOutcome(exit_code=1, message="validation failed")

        exit_code, stdout, stderr, _service = self.execute(
            ["validate", "--workspace", "work"],
            service,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "validation failed\n")

    def test_source_cleanup_path_is_not_part_of_the_command_surface(self) -> None:
        exit_code, _stdout, stderr, service = self.execute(
            [
                "cancel",
                "--workspace",
                "work",
                "--confirm",
                "CANCEL",
                "--source",
                "delete-me",
            ]
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("unrecognized arguments", stderr)
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()
