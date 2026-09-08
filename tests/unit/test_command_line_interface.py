from __future__ import annotations

import io
import unittest
from pathlib import Path
from typing import TextIO

from scaffold_compiler.command_line_interface import CommandOutcome, run_command_line


class RecordingCommandService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.outcome = CommandOutcome(exit_code=0, message="completed")

    def preview(self, config_path: Path) -> CommandOutcome:
        return self._record("preview", config_path)

    def initialize_configuration(
        self,
        config_path: Path,
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> CommandOutcome:
        return self._record("init", config_path, input_stream, output_stream)

    def inspect(self, workspace: Path) -> CommandOutcome:
        return self._record("inspect", workspace)

    def discard(self, workspace: Path) -> CommandOutcome:
        return self._record("discard", workspace)

    def cleanup(self, workspace: Path) -> CommandOutcome:
        return self._record("cleanup", workspace)

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
        *,
        stdin_text: str = "",
    ) -> tuple[int, str, str, RecordingCommandService]:
        selected_service = service or RecordingCommandService()
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = run_command_line(
            arguments,
            service=selected_service,
            stdin=io.StringIO(stdin_text),
            stdout=stdout,
            stderr=stderr,
        )
        return exit_code, stdout.getvalue(), stderr.getvalue(), selected_service

    def test_init_routes_the_output_path_and_interactive_streams(self) -> None:
        exit_code, stdout, stderr, service = self.execute(
            ["init", "--output", "project.json"],
            stdin_text="1\nDemo\ndelivery\n\n\n\n",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "completed\n")
        self.assertEqual(stderr, "")
        name, arguments = service.calls[0]
        self.assertEqual(name, "init")
        self.assertEqual(arguments[0], Path("project.json"))
        self.assertIsInstance(arguments[1], io.StringIO)
        self.assertIsInstance(arguments[2], io.StringIO)

    def test_preview_and_inspect_route_only_their_validated_paths(self) -> None:
        cases = (
            ("preview", ["preview", "--config", "project.json"], Path("project.json")),
            ("inspect", ["inspect", "--workspace", "work"], Path("work")),
        )
        for command, arguments, expected_path in cases:
            with self.subTest(command=command):
                exit_code, stdout, stderr, service = self.execute(arguments)
                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout, "completed\n")
                self.assertEqual(stderr, "")
                self.assertEqual(service.calls, [(command, (expected_path,))])

    def test_discard_requires_the_exact_explicit_confirmation(self) -> None:
        for arguments in (
            ["discard", "--workspace", "work"],
            ["discard", "--workspace", "work", "--confirm", "yes"],
        ):
            with self.subTest(arguments=arguments):
                exit_code, _stdout, stderr, service = self.execute(arguments)
                self.assertEqual(exit_code, 2)
                self.assertIn("DISCARD", stderr)
                self.assertEqual(service.calls, [])

        exit_code, _stdout, stderr, service = self.execute(
            ["discard", "--workspace", "work", "--confirm", "DISCARD"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(service.calls, [("discard", (Path("work"),))])

    def test_non_interactive_run_cannot_bypass_finalize_confirmation(self) -> None:
        arguments = ["run", "--config", "project.json", "--non-interactive"]
        exit_code, _stdout, stderr, service = self.execute(arguments)
        self.assertEqual(exit_code, 2)
        self.assertIn("FINALIZE", stderr)
        self.assertEqual(service.calls, [])

        exit_code, _stdout, stderr, service = self.execute(
            [*arguments, "--confirm-finalize", "FINALIZE"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(service.calls, [("run-non-interactive", (Path("project.json"),))])

    def test_cleanup_retry_requires_the_exact_explicit_confirmation(self) -> None:
        for arguments in (
            ["cleanup", "--workspace", "work"],
            ["cleanup", "--workspace", "work", "--confirm", "yes"],
        ):
            with self.subTest(arguments=arguments):
                exit_code, _stdout, stderr, service = self.execute(arguments)
                self.assertEqual(exit_code, 2)
                self.assertIn("CLEANUP", stderr)
                self.assertEqual(service.calls, [])

        exit_code, _stdout, stderr, service = self.execute(
            ["cleanup", "--workspace", "work", "--confirm", "CLEANUP"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(service.calls, [("cleanup", (Path("work"),))])

    def test_retired_lifecycle_commands_are_not_exposed(self) -> None:
        for command in ("generate", "validate", "status", "regenerate", "finalize", "cancel"):
            with self.subTest(command=command):
                exit_code, _stdout, stderr, service = self.execute([command])
                self.assertEqual(exit_code, 2)
                self.assertIn("invalid choice", stderr)
                self.assertEqual(service.calls, [])

    def test_domain_failure_returns_stable_nonzero_exit_and_stderr(self) -> None:
        service = RecordingCommandService()
        service.outcome = CommandOutcome(exit_code=1, message="workspace is unsafe")

        exit_code, stdout, stderr, _service = self.execute(
            ["inspect", "--workspace", "work"], service
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "workspace is unsafe\n")

    def test_arbitrary_source_cleanup_path_is_not_accepted(self) -> None:
        exit_code, _stdout, stderr, service = self.execute(
            [
                "discard",
                "--workspace",
                "work",
                "--confirm",
                "DISCARD",
                "--source",
                "delete-me",
            ]
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("unrecognized arguments", stderr)
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()
