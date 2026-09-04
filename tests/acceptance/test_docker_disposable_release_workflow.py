from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "13572468-2468-4135-8246-135724681357"
OWNERSHIP_LABEL = "io.scaffold-compiler.validation"


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


@unittest.skipUnless(DOCKER_EXECUTABLE is not None, "Docker engine is unavailable")
class DockerDisposableReleaseWorkflowTests(unittest.TestCase):
    def test_m02_runs_from_capsule_and_leaves_no_owned_docker_resources(self) -> None:
        assert DOCKER_EXECUTABLE is not None
        repository = Path(__file__).parents[2]
        uv_executable = Path(sys.executable).with_name(
            "uv.exe" if os.name == "nt" else "uv"
        )
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
                "--filter",
                f"label={OWNERSHIP_LABEL}",
            ),
        ),
        images=_listed_ids(
            docker,
            (
                "image",
                "ls",
                "--filter",
                "reference=scaffold-validation:*",
                "--filter",
                f"label={OWNERSHIP_LABEL}",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ),
        ),
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


def _failure_diagnostics(root: Path, stderr: str) -> str:
    reports = tuple(root.glob(".*.scaffold-*/validation_report.json"))
    report = reports[0].read_text(encoding="utf-8") if len(reports) == 1 else ""
    return f"{stderr}\n{report}"


if __name__ == "__main__":
    unittest.main()
