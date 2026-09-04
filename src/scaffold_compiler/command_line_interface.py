"""Thin argparse adapter for the scaffold compiler application service."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Never, Protocol, TextIO

LOGGER = logging.getLogger(__name__)

_EXIT_SUCCESS = 0
_EXIT_DOMAIN_FAILURE = 1
_EXIT_USAGE_ERROR = 2


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Stable user-facing result returned by application service commands."""

    exit_code: int
    message: str

    def __post_init__(self) -> None:
        if self.exit_code not in {_EXIT_SUCCESS, _EXIT_DOMAIN_FAILURE}:
            raise ValueError("Command outcome exit code must be 0 or 1.")
        if not self.message.strip() or "\0" in self.message:
            raise ValueError("Command outcome message is invalid.")


class ScaffoldCommandService(Protocol):
    """Application boundary consumed by the command-line adapter."""

    def preview(self, config_path: Path) -> CommandOutcome: ...

    def generate(self, config_path: Path) -> CommandOutcome: ...

    def validate(self, workspace: Path) -> CommandOutcome: ...

    def status(self, workspace: Path) -> CommandOutcome: ...

    def regenerate(
        self,
        workspace: Path,
        config_path: Path,
        *,
        discard_candidate: bool,
    ) -> CommandOutcome: ...

    def finalize(self, workspace: Path) -> CommandOutcome: ...

    def cancel(self, workspace: Path) -> CommandOutcome: ...

    def run_non_interactive(self, config_path: Path) -> CommandOutcome: ...


class _CommandLineUsageError(ValueError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _CommandLineUsageError(message)


def run_command_line(
    arguments: list[str],
    *,
    service: ScaffoldCommandService,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Parse one command and route it without implementing domain side effects."""
    parser = _build_parser()
    try:
        parsed = parser.parse_args(arguments)
    except _CommandLineUsageError as error:
        message = str(error)
        if "--confirm" in message or "--confirm-finalize" in message:
            message = f"{message}; explicit FINALIZE or CANCEL confirmation is required"
        stderr.write(f"usage error: {message}\n")
        return _EXIT_USAGE_ERROR

    command = str(parsed.command)
    LOGGER.info("cli_command_started command=%s", command)
    outcome = _dispatch(parsed, service)
    destination = stdout if outcome.exit_code == _EXIT_SUCCESS else stderr
    destination.write(f"{outcome.message}\n")
    LOGGER.info(
        "cli_command_completed command=%s exit_code=%d",
        command,
        outcome.exit_code,
    )
    return outcome.exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="scaffold-compiler")
    commands = parser.add_subparsers(dest="command", required=True)

    for command in ("preview", "generate"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--config", required=True, type=Path)

    for command in ("validate", "status"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--workspace", required=True, type=Path)

    regenerate = commands.add_parser("regenerate")
    regenerate.add_argument("--workspace", required=True, type=Path)
    regenerate.add_argument("--config", required=True, type=Path)
    regenerate.add_argument("--discard-candidate", action="store_true", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--workspace", required=True, type=Path)
    finalize.add_argument("--confirm", required=True, choices=("FINALIZE",))

    cancel = commands.add_parser("cancel")
    cancel.add_argument("--workspace", required=True, type=Path)
    cancel.add_argument("--confirm", required=True, choices=("CANCEL",))

    non_interactive = commands.add_parser("run")
    non_interactive.add_argument("--config", required=True, type=Path)
    non_interactive.add_argument("--non-interactive", action="store_true", required=True)
    non_interactive.add_argument(
        "--confirm-finalize",
        required=True,
        choices=("FINALIZE",),
    )
    return parser


def _dispatch(
    parsed: argparse.Namespace,
    service: ScaffoldCommandService,
) -> CommandOutcome:
    command = parsed.command
    if command == "preview":
        return service.preview(parsed.config)
    if command == "generate":
        return service.generate(parsed.config)
    if command == "validate":
        return service.validate(parsed.workspace)
    if command == "status":
        return service.status(parsed.workspace)
    if command == "regenerate":
        return service.regenerate(
            parsed.workspace,
            parsed.config,
            discard_candidate=parsed.discard_candidate,
        )
    if command == "finalize":
        return service.finalize(parsed.workspace)
    if command == "cancel":
        return service.cancel(parsed.workspace)
    if command == "run":
        return service.run_non_interactive(parsed.config)
    raise AssertionError("Argument parser returned an unknown command.")
