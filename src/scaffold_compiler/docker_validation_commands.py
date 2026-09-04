"""Shell-free Docker CLI specifications for one owned validation lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from scaffold_compiler.docker_validation_resources import DockerValidationResources
from scaffold_compiler.validation import ControlledProcessSpec

_COMMAND_TIMEOUT_SECONDS: Final = 180.0
_BUILD_TIMEOUT_SECONDS: Final = 600.0
_OUTPUT_LIMIT_BYTES: Final = 65_536
_NON_ROOT_CHECK: Final = (
    "import os; raise SystemExit(0 if hasattr(os, 'geteuid') and os.geteuid() != 0 else 1)"
)


class DockerValidationCommands:
    """Build fixed process specifications without executing Docker commands."""

    def __init__(
        self,
        docker_executable: Path,
        candidate_root: Path,
        resources: DockerValidationResources,
        *,
        compose_password: str | None = None,
    ) -> None:
        try:
            resolved_docker = docker_executable.resolve(strict=True)
            resolved_candidate = candidate_root.resolve(strict=True)
        except OSError as error:
            raise ValueError("Docker executable and candidate root must exist.") from error
        if not resolved_docker.is_file():
            raise ValueError("Docker executable must be an existing ordinary file.")
        if not resolved_candidate.is_dir():
            raise ValueError("Candidate root must be an existing directory.")
        if compose_password is not None and (not compose_password or "\0" in compose_password):
            raise ValueError("Compose password must be a non-empty non-NUL string.")
        self._docker = str(resolved_docker)
        self._candidate = resolved_candidate
        self._resources = resources
        self._compose_password = compose_password

    def build_image(self) -> ControlledProcessSpec:
        return self._specification(
            "docker-build",
            (
                "build",
                "--tag",
                self._resources.image_tag,
                "--label",
                self._ownership_label,
                ".",
            ),
            timeout_seconds=_BUILD_TIMEOUT_SECONDS,
        )

    def check_non_root(self) -> ControlledProcessSpec:
        return self._specification(
            "container-non-root",
            (
                "run",
                "--rm",
                "--label",
                self._ownership_label,
                "--entrypoint",
                "python",
                self._resources.image_tag,
                "-c",
                _NON_ROOT_CHECK,
            ),
        )

    def start_health_container(self) -> ControlledProcessSpec:
        return self._specification(
            "container-health-start",
            (
                "run",
                "--detach",
                "--name",
                self._resources.container_name,
                "--label",
                self._ownership_label,
                self._resources.image_tag,
            ),
        )

    def inspect_container_health(self) -> ControlledProcessSpec:
        return self._specification(
            "container-health",
            (
                "inspect",
                "--format",
                "{{json .State.Health.Status}}",
                self._resources.container_name,
            ),
        )

    def remove_container(self) -> ControlledProcessSpec:
        return self._specification(
            "container-cleanup",
            ("rm", "--force", self._resources.container_name),
        )

    def remove_image(self) -> ControlledProcessSpec:
        return self._specification(
            "docker-image-cleanup",
            ("image", "rm", "--force", self._resources.image_tag),
        )

    def compose_config(self) -> ControlledProcessSpec:
        return self._compose_specification("compose-config", ("config",))

    def compose_up(self) -> ControlledProcessSpec:
        return self._compose_specification(
            "compose-up",
            ("up", "--detach", "--build", "--wait", "--wait-timeout", "180"),
            timeout_seconds=_BUILD_TIMEOUT_SECONDS,
        )

    def compose_health(self) -> ControlledProcessSpec:
        return self._compose_specification(
            "compose-health",
            ("ps", "--status", "running", "--services"),
        )

    def compose_down(self) -> ControlledProcessSpec:
        return self._compose_specification(
            "compose-cleanup",
            ("down", "--volumes", "--remove-orphans", "--rmi", "local"),
            timeout_seconds=_BUILD_TIMEOUT_SECONDS,
        )

    @property
    def _ownership_label(self) -> str:
        return (
            f"{self._resources.ownership_label_name}="
            f"{self._resources.ownership_label_value}"
        )

    def _compose_specification(
        self,
        name: str,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
    ) -> ControlledProcessSpec:
        if self._compose_password is None:
            raise ValueError("Compose validation requires a temporary database password.")
        return self._specification(
            name,
            (
                "compose",
                "--project-name",
                self._resources.compose_project,
                "--file",
                "compose.yaml",
                *arguments,
            ),
            timeout_seconds=timeout_seconds,
            secrets=(self._compose_password,),
            environment=(("POSTGRES_PASSWORD", self._compose_password),),
        )

    def _specification(
        self,
        name: str,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
        secrets: tuple[str, ...] = (),
        environment: tuple[tuple[str, str], ...] = (),
    ) -> ControlledProcessSpec:
        return ControlledProcessSpec(
            name=name,
            argv=(self._docker, *arguments),
            cwd=self._candidate,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=_OUTPUT_LIMIT_BYTES,
            secrets=secrets,
            environment=environment,
        )
