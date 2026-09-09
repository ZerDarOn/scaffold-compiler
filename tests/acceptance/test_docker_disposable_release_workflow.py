from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from uuid import uuid4

from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "13572468-2468-4135-8246-135724681357"
POSTGRES_COMPOSE_CAPSULE_ID = "86421357-1357-4864-9135-864213578642"
OWNERSHIP_LABEL = "io.scaffold-compiler.validation"
ACCEPTANCE_DATABASE_LABEL = "io.scaffold-compiler.acceptance-database"


def _docker_executable_with_running_engine() -> Path | None:
    configured = os.environ.get("SCAFFOLD_TEST_DOCKER")
    discovered = configured or shutil.which("docker")
    if not discovered:
        return None
    executable = Path(discovered).resolve()
    try:
        completed = subprocess.run(
            (str(executable), "version", "--format", "{{.Server.Version}}"),
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return executable if completed.returncode == 0 and completed.stdout.strip() else None


DOCKER_EXECUTABLE = _docker_executable_with_running_engine()


@dataclass(frozen=True, slots=True)
class DockerResourceSnapshot:
    containers: frozenset[str]
    images: frozenset[str]
    volumes: frozenset[str]
    compose_projects: frozenset[str]


@unittest.skipUnless(DOCKER_EXECUTABLE is not None, "Docker engine is unavailable")
class DockerDisposableReleaseWorkflowTests(unittest.TestCase):
    def test_m02_runs_from_capsule_and_leaves_no_owned_docker_resources(self) -> None:
        assert DOCKER_EXECUTABLE is not None
        repository = Path(__file__).parents[2]
        uv_executable = Path(sys.executable).with_name("uv.exe" if os.name == "nt" else "uv")
        self.assertTrue(uv_executable.is_file(), "project environment must contain uv")
        before = _snapshot(DOCKER_EXECUTABLE)

        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                capsule = build_disposable_release(
                    repository,
                    root / "capsule",
                    capsule_id=CAPSULE_ID,
                    compiler_version="1.0.0",
                )
                target = root / "delivery"
                config = root / "project.json"
                config.write_text(
                    json.dumps(
                        {
                            "project_name": "Docker Release Acceptance API",
                            "package_name": "docker_release_acceptance",
                            "target_directory": str(target),
                            "database": "none",
                            "container": "docker",
                        }
                    ),
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment["SCAFFOLD_COMPILER_UV"] = str(uv_executable)
                environment["SCAFFOLD_COMPILER_DOCKER"] = str(DOCKER_EXECUTABLE)

                completed = subprocess.run(
                    (
                        sys.executable,
                        str(capsule / "scaffold_compiler.pyz"),
                        "run",
                        "--config",
                        str(config),
                        "--non-interactive",
                        "--confirm-finalize",
                        "FINALIZE",
                    ),
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=900,
                )

                self.assertEqual(
                    completed.returncode,
                    0,
                    _failure_diagnostics(root, completed.stderr),
                )
                self.assertTrue((target / "Dockerfile").is_file())
                self.assertIn("USER application", (target / "Dockerfile").read_text("utf-8"))
                cleanup_root = root / f".scaffold-capsule-cleanup-{CAPSULE_ID}"
                deadline = time.monotonic() + 15
                while (capsule.exists() or cleanup_root.exists()) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(capsule.exists(), "disposable generator was not removed")
                self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")

            self.assertEqual(_snapshot(DOCKER_EXECUTABLE), before)
        finally:
            _remove_snapshot_delta(DOCKER_EXECUTABLE, before)

    def test_m04_runs_postgres_compose_from_capsule_with_zero_resource_delta(self) -> None:
        assert DOCKER_EXECUTABLE is not None
        repository = Path(__file__).parents[2]
        uv_executable = Path(sys.executable).with_name("uv.exe" if os.name == "nt" else "uv")
        self.assertTrue(uv_executable.is_file(), "project environment must contain uv")

        with _auxiliary_postgres(DOCKER_EXECUTABLE) as database_url:
            before = _snapshot(DOCKER_EXECUTABLE)
            try:
                with TemporaryDirectory() as directory:
                    root = Path(directory)
                    capsule = build_disposable_release(
                        repository,
                        root / "capsule",
                        capsule_id=POSTGRES_COMPOSE_CAPSULE_ID,
                        compiler_version="1.0.0",
                    )
                    target = root / "delivery"
                    config = root / "project.json"
                    config.write_text(
                        json.dumps(
                            {
                                "project_name": "Postgres Compose Acceptance API",
                                "package_name": "postgres_compose_acceptance",
                                "target_directory": str(target),
                                "database": "postgres",
                                "container": "docker",
                            }
                        ),
                        encoding="utf-8",
                    )
                    environment = os.environ.copy()
                    environment["SCAFFOLD_COMPILER_UV"] = str(uv_executable)
                    environment["SCAFFOLD_COMPILER_DOCKER"] = str(DOCKER_EXECUTABLE)
                    environment["DATABASE_URL"] = database_url

                    completed = subprocess.run(
                        (
                            sys.executable,
                            str(capsule / "scaffold_compiler.pyz"),
                            "run",
                            "--config",
                            str(config),
                            "--non-interactive",
                            "--confirm-finalize",
                            "FINALIZE",
                        ),
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=1200,
                    )

                    self.assertEqual(
                        completed.returncode,
                        0,
                        _failure_diagnostics(root, completed.stderr),
                    )
                    self.assertTrue((target / "compose.yaml").is_file())
                    self.assertTrue((target / "migrations" / "env.py").is_file())
                    cleanup_root = root / f".scaffold-capsule-cleanup-{POSTGRES_COMPOSE_CAPSULE_ID}"
                    deadline = time.monotonic() + 15
                    while (
                        capsule.exists() or cleanup_root.exists()
                    ) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse(capsule.exists(), "disposable generator was not removed")
                    self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")

                self.assertEqual(_snapshot(DOCKER_EXECUTABLE), before)
            finally:
                _remove_snapshot_delta(DOCKER_EXECUTABLE, before)


def _snapshot(docker: Path) -> DockerResourceSnapshot:
    return DockerResourceSnapshot(
        containers=_listed_ids(
            docker,
            (
                "container",
                "ls",
                "--all",
                "--quiet",
                "--filter",
                "name=scaffold-validation-",
            ),
        ),
        images=_listed_ids(
            docker,
            (
                "image",
                "ls",
                "--filter",
                "reference=scaffold-validation*",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ),
        ),
        volumes=_listed_ids(
            docker,
            ("volume", "ls", "--quiet", "--filter", "name=scaffold-validation-"),
        ),
        compose_projects=_compose_projects(docker),
    )


def _listed_ids(docker: Path, arguments: tuple[str, ...]) -> frozenset[str]:
    completed = subprocess.run(
        (str(docker), *arguments),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError("Docker resource inventory failed.")
    return frozenset(completed.stdout.split())


def _remove_snapshot_delta(docker: Path, before: DockerResourceSnapshot) -> None:
    after = _snapshot(docker)
    for container_id in sorted(after.containers - before.containers):
        subprocess.run(
            (str(docker), "container", "rm", "--force", container_id),
            capture_output=True,
            check=False,
            timeout=60,
        )
    for image_reference in sorted(after.images - before.images):
        subprocess.run(
            (str(docker), "image", "rm", image_reference),
            capture_output=True,
            check=False,
            timeout=60,
        )
    for volume_name in sorted(after.volumes - before.volumes):
        subprocess.run(
            (str(docker), "volume", "rm", volume_name),
            capture_output=True,
            check=False,
            timeout=60,
        )


def _compose_projects(docker: Path) -> frozenset[str]:
    completed = subprocess.run(
        (str(docker), "compose", "ls", "--all", "--format", "json"),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError("Docker Compose project inventory failed.")
    try:
        projects = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:
        raise AssertionError("Docker Compose project inventory is invalid.") from error
    if not isinstance(projects, list):
        raise AssertionError("Docker Compose project inventory is invalid.")
    return frozenset(
        name
        for project in projects
        if isinstance(project, dict)
        if isinstance(name := project.get("Name", project.get("name")), str)
        if name.startswith("scaffold-validation-")
    )


@contextmanager
def _auxiliary_postgres(docker: Path) -> Iterator[str]:
    identity = uuid4().hex
    name = f"scaffold-acceptance-postgres-{identity}"
    password = token_hex(24)
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_DB": "application",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_USER": "application",
        }
    )
    try:
        started = subprocess.run(
            (
                str(docker),
                "run",
                "--detach",
                "--name",
                name,
                "--label",
                f"{ACCEPTANCE_DATABASE_LABEL}={identity}",
                "--env",
                "POSTGRES_DB",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_USER",
                "--publish",
                "127.0.0.1::5432",
                "postgres:18.1-bookworm",
            ),
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _remove_auxiliary_postgres(docker, name, identity)
        raise AssertionError("Auxiliary PostgreSQL process could not be started.") from error
    if started.returncode != 0:
        _remove_auxiliary_postgres(docker, name, identity)
        raise AssertionError("Auxiliary PostgreSQL container could not be started.")
    try:
        port = _published_postgres_port(docker, name)
        _wait_for_postgres(docker, name)
        yield (f"postgresql+asyncpg://application:{password}@127.0.0.1:{port}/application")
    finally:
        _remove_auxiliary_postgres(docker, name, identity)


def _remove_auxiliary_postgres(docker: Path, name: str, identity: str) -> None:
    try:
        ownership = subprocess.run(
            (
                str(docker),
                "container",
                "inspect",
                "--format",
                f'{{{{ index .Config.Labels "{ACCEPTANCE_DATABASE_LABEL}" }}}}',
                name,
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if ownership.returncode == 0 and ownership.stdout.strip() == identity:
        subprocess.run(
            (str(docker), "container", "rm", "--force", name),
            capture_output=True,
            check=False,
            timeout=60,
        )


def _published_postgres_port(docker: Path, name: str) -> int:
    completed = subprocess.run(
        (str(docker), "port", name, "5432/tcp"),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise AssertionError("Auxiliary PostgreSQL port could not be resolved.")
    try:
        return int(completed.stdout.strip().rsplit(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise AssertionError("Auxiliary PostgreSQL port is invalid.") from error


def _wait_for_postgres(docker: Path, name: str) -> None:
    for _attempt in range(90):
        ready = subprocess.run(
            (str(docker), "exec", name, "pg_isready", "-U", "application", "-d", "application"),
            capture_output=True,
            check=False,
            timeout=15,
        )
        if ready.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("Auxiliary PostgreSQL did not become ready.")


def _failure_diagnostics(root: Path, stderr: str) -> str:
    reports = tuple(root.glob(".scw-*/validation_report.json"))
    report = reports[0].read_text(encoding="utf-8") if len(reports) == 1 else ""
    return f"{report}\n{stderr}"


if __name__ == "__main__":
    unittest.main()
