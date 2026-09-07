"""Fixed V1 validation execution with explicit unsupported-gate semantics."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from secrets import token_urlsafe
from typing import Final

from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    verify_candidate_digest,
)
from scaffold_compiler.docker_validation_commands import DockerValidationCommands
from scaffold_compiler.docker_validation_lifecycle import execute_docker_validation_lifecycle
from scaffold_compiler.docker_validation_resources import DockerValidationResources
from scaffold_compiler.validation import (
    ControlledProcessResult,
    ControlledProcessSpec,
    ValidationCheck,
    ValidationPhase,
    ValidationReport,
    ValidationStatus,
    check_from_process_result,
    run_controlled_process,
    scan_candidate_static_safety,
    skipped_validation_check,
)

LOGGER = logging.getLogger(__name__)

_INSTALL_TIMEOUT_SECONDS: Final = 300.0
_CHECK_TIMEOUT_SECONDS: Final = 180.0
_OUTPUT_LIMIT_BYTES: Final = 65_536
_PYTHON_SYNTAX_CODE: Final = """from pathlib import Path

for root in (Path("src"), Path("tests")):
    for path in sorted(root.rglob("*.py")):
        compile(path.read_bytes(), path.as_posix(), "exec", dont_inherit=True)
"""
_CODE_VALIDATION_ARGUMENTS: Final = {
    "python-syntax": ("run", "--no-sync", "python", "-c", _PYTHON_SYNTAX_CODE),
    "ruff-format": ("run", "--no-sync", "ruff", "format", "--check", "."),
    "ruff-lint": ("run", "--no-sync", "ruff", "check", "."),
    "mypy": ("run", "--no-sync", "mypy"),
    "pytest": ("run", "--no-sync", "pytest", "-p", "no:cacheprovider"),
}
_HTTP_VALIDATION_PATHS: Final = {
    "application-start": "/api/v1",
    "health-live": "/health/live",
    "health-ready": "/health/ready",
    "openapi": "/openapi.json",
}
_HTTP_CHECK_CODE: Final = """import importlib
import sys
from fastapi.testclient import TestClient

module = importlib.import_module(sys.argv[1])
application = module.application
with TestClient(application) as client:
    response = client.get(sys.argv[2])
    if response.status_code != 200:
        raise SystemExit(1)
    if sys.argv[2] == "/openapi.json" and not response.json().get("openapi"):
        raise SystemExit(1)
"""
_POSTGRES_CONNECT_CODE: Final = """import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def check() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

asyncio.run(check())
"""
_DATABASE_READINESS_CODE: Final = """import asyncio
import importlib
import os
import sys
from sqlalchemy.ext.asyncio import create_async_engine

module = importlib.import_module(sys.argv[1])
engine = create_async_engine(os.environ["DATABASE_URL"])
async def check() -> None:
    try:
        if not await module.database_is_ready(engine):
            raise SystemExit(1)
    finally:
        await engine.dispose()
asyncio.run(check())
"""
_PACKAGE_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_DELIVERY_VALIDATIONS: Final = frozenset(
    {
        "docker-build",
        "container-non-root",
        "container-health",
        "compose-config",
        "compose-up",
        "compose-health",
        "compose-cleanup",
    }
)
_VALIDATION_ORDER: Final = {
    name: index
    for index, name in enumerate(
        (
            "python-syntax",
            "ruff-format",
            "ruff-lint",
            "mypy",
            "pytest",
            "postgres-connect",
            "alembic-upgrade",
            "database-readiness",
            "session-rollback",
            "application-start",
            "health-live",
            "health-ready",
            "openapi",
            "docker-build",
            "container-non-root",
            "container-health",
            "compose-config",
            "compose-up",
            "compose-health",
            "compose-cleanup",
        )
    )
}

ValidationProcessRunner = Callable[[ControlledProcessSpec], ControlledProcessResult]


def execute_v1_validation(
    candidate: CandidateAssemblyResult,
    configuration_digest: str,
    blueprint_digest: str,
    required_validations: tuple[str, ...],
    *,
    package_name: str,
    uv_executable: Path,
    validation_environment: Path,
    database_url: str | None = None,
    run_id: str | None = None,
    docker_executable: Path | None = None,
    forbidden_absolute_paths: tuple[Path, ...],
    secrets: tuple[str, ...] = (),
    process_runner: ValidationProcessRunner = run_controlled_process,
    compose_password_factory: Callable[[], str] = lambda: token_urlsafe(24),
) -> ValidationReport:
    """Run supported fixed checks and mark every unavailable required gate as skipped."""
    if not _PACKAGE_NAME_PATTERN.fullmatch(package_name):
        raise ValueError("Package name is invalid for runtime validation.")
    if not validation_environment.is_absolute():
        raise ValueError("Validation environment path must be absolute.")
    resolved_validation_environment = validation_environment.resolve(strict=False)
    if validation_environment.is_symlink():
        raise ValueError("Validation environment cannot be a symbolic link.")
    if _path_is_within(resolved_validation_environment, candidate.root.resolve(strict=True)):
        raise ValueError("Validation environment must be outside the candidate project.")
    if resolved_validation_environment.exists() and not resolved_validation_environment.is_dir():
        raise ValueError("Validation environment path must be a directory when it exists.")
    if not resolved_validation_environment.parent.is_dir():
        raise ValueError("Validation environment parent must already exist.")
    effective_secrets = secrets
    process_environment: tuple[tuple[str, str], ...] = (
        ("MYPY_CACHE_DIR", str(resolved_validation_environment / "mypy-cache")),
        ("PYTHONPATH", str(candidate.root / "src")),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("RUFF_CACHE_DIR", str(resolved_validation_environment / "ruff-cache")),
        ("UV_CACHE_DIR", str(resolved_validation_environment / "uv-cache")),
        ("UV_PROJECT_ENVIRONMENT", str(resolved_validation_environment / "venv")),
    )
    if database_url is not None:
        if not database_url.startswith("postgresql+asyncpg://") or "\0" in database_url:
            raise ValueError("Database URL is invalid for PostgreSQL validation.")
        process_environment += (("DATABASE_URL", database_url),)
        effective_secrets = tuple(dict.fromkeys((*secrets, database_url)))
    resolved_uv = uv_executable.resolve(strict=True)
    if not resolved_uv.is_file():
        raise ValueError("uv executable must be an existing ordinary file.")
    static_check = scan_candidate_static_safety(
        candidate,
        forbidden_absolute_paths=forbidden_absolute_paths,
        secrets=effective_secrets,
    )
    checks: list[ValidationCheck] = [static_check]
    LOGGER.info(
        "v1_validation_started required=%d candidate_digest=%s",
        len(required_validations),
        candidate.digest,
    )
    if static_check.status is ValidationStatus.PASS:
        install_result = process_runner(
            _process_specification(
                "locked-install",
                resolved_uv,
                ("sync", "--frozen", "--no-install-project"),
                candidate.root,
                _INSTALL_TIMEOUT_SECONDS,
                effective_secrets,
                process_environment,
            )
        )
        install_check = check_from_process_result(
            "locked-install",
            ValidationPhase.INSTALL,
            required=True,
            result=install_result,
        )
    else:
        install_check = skipped_validation_check(
            "locked-install",
            ValidationPhase.INSTALL,
            required=True,
            reason="Static safety validation did not pass.",
        )
    checks.append(install_check)

    blocking_failure = install_check.status is not ValidationStatus.PASS
    active_phase: ValidationPhase | None = None
    phase_failed = False
    ordered_validations = tuple(
        sorted(required_validations, key=lambda item: (_VALIDATION_ORDER.get(item, 999), item))
    )
    delivery_validations = tuple(
        name for name in ordered_validations if name in _DELIVERY_VALIDATIONS
    )
    for name in (name for name in ordered_validations if name not in _DELIVERY_VALIDATIONS):
        validation = _validation_command(
            name,
            package_name,
            database_url=database_url,
            database_required="postgres-connect" in required_validations,
        )
        phase = validation[1] if validation is not None else _phase_for_unavailable_check(name)
        if active_phase is not phase:
            if active_phase is not None and phase_failed:
                blocking_failure = True
            active_phase = phase
            phase_failed = False
        if validation is None:
            checks.append(
                skipped_validation_check(
                    name,
                    phase,
                    required=True,
                    reason="This required runtime or delivery gate is not connected yet.",
                )
            )
            phase_failed = True
            continue
        arguments, phase = validation
        if blocking_failure or (phase is ValidationPhase.RUNTIME and phase_failed):
            checks.append(
                skipped_validation_check(
                    name,
                    phase,
                    required=True,
                    reason="A prerequisite validation gate did not pass.",
                )
            )
            phase_failed = True
            continue
        result = process_runner(
            _process_specification(
                name,
                resolved_uv,
                arguments,
                candidate.root,
                _CHECK_TIMEOUT_SECONDS,
                effective_secrets,
                process_environment,
            )
        )
        check = check_from_process_result(
            name,
            phase,
            required=True,
            result=result,
        )
        if check.status is ValidationStatus.FAIL:
            LOGGER.error(
                "v1_validation_gate_failed gate=%s return_code=%d stdout=%r stderr=%r",
                name,
                result.return_code,
                result.stdout[-2000:],
                result.stderr[-2000:],
            )
        checks.append(check)
        if check.status is not ValidationStatus.PASS:
            phase_failed = True

    if phase_failed:
        blocking_failure = True
    if delivery_validations:
        if blocking_failure:
            checks.extend(
                skipped_validation_check(
                    name,
                    ValidationPhase.DELIVERY,
                    required=True,
                    reason="A prerequisite validation gate did not pass.",
                )
                for name in delivery_validations
            )
        elif docker_executable is None or run_id is None:
            checks.extend(
                skipped_validation_check(
                    name,
                    ValidationPhase.DELIVERY,
                    required=True,
                    reason="Docker validation is unavailable in this runtime.",
                )
                for name in delivery_validations
            )
        else:
            resources = DockerValidationResources.create(run_id, candidate.digest)
            compose_password = (
                compose_password_factory()
                if any(name.startswith("compose-") for name in delivery_validations)
                else None
            )
            docker_commands = DockerValidationCommands(
                docker_executable,
                candidate.root,
                resources,
                compose_password=compose_password,
            )
            checks.extend(
                execute_docker_validation_lifecycle(
                    docker_commands,
                    delivery_validations,
                    process_runner=process_runner,
                )
            )

    verify_candidate_digest(candidate)
    report = ValidationReport(
        configuration_digest=configuration_digest,
        blueprint_digest=blueprint_digest,
        plan_digest=candidate.plan_digest,
        candidate_digest=candidate.digest,
        checks=tuple(checks),
    )
    incomplete = sum(
        check.required and check.status is not ValidationStatus.PASS for check in report.checks
    )
    LOGGER.info(
        "v1_validation_completed checks=%d incomplete=%d candidate_digest=%s",
        len(report.checks),
        incomplete,
        candidate.digest,
    )
    return report


def _process_specification(
    name: str,
    uv_executable: Path,
    arguments: tuple[str, ...],
    candidate_root: Path,
    timeout_seconds: float,
    secrets: tuple[str, ...],
    environment: tuple[tuple[str, str], ...],
) -> ControlledProcessSpec:
    return ControlledProcessSpec(
        name=name,
        argv=(str(uv_executable), *arguments),
        cwd=candidate_root,
        timeout_seconds=timeout_seconds,
        output_limit_bytes=_OUTPUT_LIMIT_BYTES,
        secrets=secrets,
        environment=environment,
    )


def _phase_for_unavailable_check(name: str) -> ValidationPhase:
    if name.startswith("docker") or name.startswith("container") or name.startswith("compose"):
        return ValidationPhase.DELIVERY
    return ValidationPhase.RUNTIME


def _validation_command(
    name: str,
    package_name: str,
    *,
    database_url: str | None,
    database_required: bool,
) -> tuple[tuple[str, ...], ValidationPhase] | None:
    code_arguments = _CODE_VALIDATION_ARGUMENTS.get(name)
    if code_arguments is not None:
        return code_arguments, ValidationPhase.QUALITY
    endpoint = _HTTP_VALIDATION_PATHS.get(name)
    if endpoint is not None:
        if database_required and database_url is None:
            return None
        return (
            (
                "run",
                "--no-sync",
                "python",
                "-c",
                _HTTP_CHECK_CODE,
                f"{package_name}.asgi",
                endpoint,
            ),
            ValidationPhase.RUNTIME,
        )
    if database_url is None:
        return None
    if name == "postgres-connect":
        return (
            "run",
            "--no-sync",
            "python",
            "-c",
            _POSTGRES_CONNECT_CODE,
        ), ValidationPhase.RUNTIME
    if name == "alembic-upgrade":
        return ("run", "--no-sync", "alembic", "upgrade", "head"), ValidationPhase.RUNTIME
    if name == "database-readiness":
        return (
            (
                "run",
                "--no-sync",
                "python",
                "-c",
                _DATABASE_READINESS_CODE,
                f"{package_name}.persistence.database_readiness",
            ),
            ValidationPhase.RUNTIME,
        )
    if name == "session-rollback":
        return (
            "run",
            "--no-sync",
            "pytest",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_database_session.py",
        ), ValidationPhase.RUNTIME
    return None


def _path_is_within(path: Path, parent: Path) -> bool:
    normalized_path = os.path.normcase(str(path.resolve(strict=False)))
    normalized_parent = os.path.normcase(str(parent.resolve(strict=False)))
    try:
        return os.path.commonpath((normalized_path, normalized_parent)) == normalized_parent
    except ValueError:
        return False
