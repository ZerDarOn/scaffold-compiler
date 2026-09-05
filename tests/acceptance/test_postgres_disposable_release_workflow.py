from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.disposable_release_builder import build_disposable_release

CAPSULE_ID = "87654321-4321-4876-9876-123456789abc"


def _postgres_binary_directory() -> Path | None:
    configured = os.environ.get("SCAFFOLD_TEST_POSTGRES_BIN")
    candidates = [Path(configured)] if configured else []
    if os.name == "nt":
        candidates.append(Path("D:/PGSQL/bin"))
    if discovered := shutil.which("initdb"):
        candidates.append(Path(discovered).parent)
    executable_name = "initdb.exe" if os.name == "nt" else "initdb"
    return next(
        (
            candidate.resolve()
            for candidate in candidates
            if (candidate / executable_name).is_file()
        ),
        None,
    )


POSTGRES_BIN = _postgres_binary_directory()
EXTERNAL_DATABASE_URL = os.environ.get("SCAFFOLD_TEST_DATABASE_URL")
POSTGRES_ENVIRONMENT_AVAILABLE = POSTGRES_BIN is not None or EXTERNAL_DATABASE_URL is not None


@unittest.skipUnless(
    POSTGRES_ENVIRONMENT_AVAILABLE,
    "isolated PostgreSQL binaries or an explicit test database are unavailable",
)
class PostgresDisposableReleaseWorkflowTests(unittest.TestCase):
    def test_m03_runs_from_capsule_against_an_isolated_postgres_cluster(self) -> None:
        repository = Path(__file__).parents[2]
        suffix = ".exe" if os.name == "nt" else ""
        uv_executable = Path(sys.executable).with_name(f"uv{suffix}")
        self.assertTrue(uv_executable.is_file(), "project environment must contain uv")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with _database_url(root) as database_url:
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
                            "project_name": "Postgres Release Acceptance API",
                            "package_name": "postgres_release_acceptance",
                            "target_directory": str(target),
                            "database": "postgres",
                            "container": "none",
                        }
                    ),
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment.pop("SCAFFOLD_TEST_DATABASE_URL", None)
                environment["SCAFFOLD_COMPILER_UV"] = str(uv_executable)
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
                    timeout=360,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue((target / "migrations" / "env.py").is_file())
                self.assertTrue(
                    (target / "src" / "postgres_release_acceptance" / "persistence").is_dir()
                )
                cleanup_root = root / f".scaffold-capsule-cleanup-{CAPSULE_ID}"
                deadline = time.monotonic() + 15
                while (capsule.exists() or cleanup_root.exists()) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(capsule.exists(), "disposable generator was not removed")
                self.assertFalse(cleanup_root.exists(), "cleanup journal was not removed")


class DatabaseUrlSelectionTests(unittest.TestCase):
    def test_explicit_test_database_avoids_host_binary_lifecycle(self) -> None:
        database_url = "postgresql+asyncpg://test:test@127.0.0.1:5432/test"
        with (
            TemporaryDirectory() as directory,
            patch(
                "tests.acceptance.test_postgres_disposable_release_workflow.EXTERNAL_DATABASE_URL",
                database_url,
            ),
            _database_url(Path(directory)) as selected,
        ):
            self.assertEqual(selected, database_url)


@contextmanager
def _database_url(root: Path) -> Iterator[str]:
    if EXTERNAL_DATABASE_URL is not None:
        yield EXTERNAL_DATABASE_URL
        return

    assert POSTGRES_BIN is not None
    suffix = ".exe" if os.name == "nt" else ""
    initdb = POSTGRES_BIN / f"initdb{suffix}"
    pg_ctl = POSTGRES_BIN / f"pg_ctl{suffix}"
    data_directory = root / "postgres-data"
    initialized = subprocess.run(
        (
            str(initdb),
            "--pgdata",
            str(data_directory),
            "--username",
            "postgres",
            "--auth",
            "trust",
            "--encoding",
            "UTF8",
            "--no-locale",
        ),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if initialized.returncode != 0:
        raise AssertionError(initialized.stderr)

    port = _reserve_local_port()
    started = subprocess.run(
        (
            str(pg_ctl),
            "--pgdata",
            str(data_directory),
            "--log",
            str(root / "postgres.log"),
            "--options",
            f"-h 127.0.0.1 -p {port}",
            "--wait",
            "--timeout",
            "60",
            "start",
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=70,
    )
    if started.returncode != 0:
        raise AssertionError((root / "postgres.log").read_text(encoding="utf-8", errors="replace"))
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        stopped = subprocess.run(
            (
                str(pg_ctl),
                "--pgdata",
                str(data_directory),
                "--wait",
                "--timeout",
                "60",
                "stop",
                "--mode",
                "fast",
            ),
            capture_output=True,
            check=False,
            text=True,
            timeout=70,
        )
        if stopped.returncode != 0:
            raise AssertionError(stopped.stderr)


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
