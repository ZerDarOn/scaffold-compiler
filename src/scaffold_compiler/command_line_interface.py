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

    def inspect(self, workspace: Path) -> CommandOutcome: ...

    def discard(self, workspace: Path) -> CommandOutcome: ...

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
        if "--confirm-finalize" in message:
            message = f"{message}; explicit FINALIZE confirmation is required"
        elif "--confirm" in message:
            message = f"{message}; explicit DISCARD confirmation is required"
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

    preview = commands.add_parser("preview")
    preview.add_argument("--config", required=True, type=Path)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--workspace", required=True, type=Path)

    discard = commands.add_parser("discard")
    discard.add_argument("--workspace", required=True, type=Path)
    discard.add_argument("--confirm", required=True, choices=("DISCARD",))

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
    if command == "inspect":
        return service.inspect(parsed.workspace)
    if command == "discard":
        return service.discard(parsed.workspace)
    if command == "run":
        return service.run_non_interactive(parsed.config)
    raise AssertionError("Argument parser returned an unknown command.")
