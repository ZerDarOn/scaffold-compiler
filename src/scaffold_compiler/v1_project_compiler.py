"""Compose the four supported V1 configurations into isolated candidates."""

from __future__ import annotations

import json
from pathlib import Path

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import compile_blueprint_plan
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    GeneratedCandidateFile,
    assemble_candidate_project,
)
from scaffold_compiler.central_project_file_assembly import (
    ProjectIdentity,
    assemble_central_project_files,
    parse_blueprint_contribution,
)
from scaffold_compiler.project_configuration import (
    ContainerChoice,
    DatabaseChoice,
    ProjectConfiguration,
)
from scaffold_compiler.strict_template_renderer import render_strict_template


def compile_v1_candidate(
    configuration: ProjectConfiguration,
    workspace: Path,
    *,
    catalog_root: Path | None = None,
) -> CandidateAssemblyResult:
    """Compile one supported configuration without writing its final target path."""
    resolved_catalog_root = catalog_root or Path(__file__).parents[2] / "blueprints"
    catalog = load_blueprint_catalog(resolved_catalog_root)
    requested = ["final-project-assembly"]
    if configuration.database is DatabaseChoice.POSTGRES:
        requested.append("postgres-persistence")
    if configuration.container is ContainerChoice.DOCKER:
        requested.append("docker-delivery")
    if (
        configuration.database is DatabaseChoice.POSTGRES
        and configuration.container is ContainerChoice.DOCKER
    ):
        requested.append("postgres-docker-integration")
    plan = compile_blueprint_plan(catalog, tuple(requested))

    values = {
        "project_name": configuration.project_name,
        "package_name": configuration.package_name,
    }
    selected_manifests = tuple(catalog.by_id(item) for item in plan.blueprint_ids)
    contributions = tuple(
        parse_blueprint_contribution(manifest.blueprint_id, manifest.contributions_json)
        for manifest in selected_manifests
        if manifest.contributions_json != "{}"
    )
    central_files = assemble_central_project_files(
        ProjectIdentity(
            project_name=configuration.project_name,
            package_name=configuration.package_name,
            description="A generated FastAPI service.",
        ),
        contributions,
    )
    declared_variables = frozenset(values)
    generated_files = [
        GeneratedCandidateFile(
            path=file.path,
            owner="final-project-assembly",
            content=render_strict_template(
                file.content,
                declared_variables=declared_variables,
                values=values,
            ).encode(),
        )
        for file in central_files
    ]
    generated_files.extend(_core_generated_python_files(configuration))
    generated_files.append(
        GeneratedCandidateFile(
            path="uv.lock",
            owner="final-project-assembly",
            content=_render_lock_file(resolved_catalog_root, configuration),
        )
    )
    return assemble_candidate_project(
        catalog,
        plan,
        workspace,
        values=values,
        generated_files=tuple(generated_files),
    )


def _core_generated_python_files(
    configuration: ProjectConfiguration,
) -> tuple[GeneratedCandidateFile, ...]:
    package = configuration.package_name
    settings_imports = "from pydantic_settings import BaseSettings, SettingsConfigDict"
    database_field = ""
    asgi_imports = f"""from {package}.application import create_application
from {package}.configuration.application_settings import ApplicationSettings
from {package}.observability.logging_configuration import configure_logging"""
    asgi_setup = "application = create_application(settings=ApplicationSettings())"
    if configuration.database is DatabaseChoice.POSTGRES:
        settings_imports = """from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict"""
        database_field = (
            "\n    database_url: str = Field(\n"
            '        min_length=1, pattern=r"^postgresql\\+asyncpg://", '
            'validation_alias="DATABASE_URL"\n'
            "    )\n"
        )
        asgi_imports = f"""from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from {package}.application import create_application
from {package}.configuration.application_settings import ApplicationSettings
from {package}.observability.logging_configuration import configure_logging
from {package}.persistence.database_engine import create_database_engine
from {package}.persistence.database_readiness import database_is_ready
from {package}.persistence.database_session import create_session_factory"""
        asgi_setup = """settings = ApplicationSettings()
database_engine = create_database_engine(settings.database_url)
database_session_factory = create_session_factory(database_engine)


@asynccontextmanager
async def application_lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.database_engine = database_engine
    application.state.database_session_factory = database_session_factory
    try:
        yield
    finally:
        await database_engine.dispose()


async def readiness_probe() -> bool:
    return await database_is_ready(database_engine)


application = create_application(
    settings=settings,
    readiness_probe=readiness_probe,
    lifespan=application_lifespan,
)"""
    settings = f'''"""Environment-backed application settings."""

{settings_imports}


class ApplicationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    application_name: str = {json.dumps(configuration.project_name, ensure_ascii=False)}
    environment: str = "local"
    cors_allowed_origins: list[str] = []
{database_field}'''
    asgi = f'''"""ASGI process entrypoint."""

{asgi_imports}

configure_logging()
{asgi_setup}
'''
    return (
        GeneratedCandidateFile(
            path=f"src/{package}/configuration/application_settings.py",
            owner="final-project-assembly",
            content=settings.encode(),
        ),
        GeneratedCandidateFile(
            path=f"src/{package}/asgi.py",
            owner="final-project-assembly",
            content=asgi.encode(),
        ),
    )


def _render_lock_file(catalog_root: Path, configuration: ProjectConfiguration) -> bytes:
    lock_variant = "postgres" if configuration.database is DatabaseChoice.POSTGRES else "core"
    lock_path = catalog_root / "final_project_assembly" / "locks" / lock_variant / "uv.lock"
    lock_content = lock_path.read_text(encoding="utf-8")
    fixture_record = 'name = "example"\nversion = "0.1.0"\nsource = { editable = "." }'
    distribution_name = configuration.package_name.replace("_", "-")
    project_record = (
        f'name = "{distribution_name}"\nversion = "0.1.0"\nsource = {{ editable = "." }}'
    )
    if lock_content.count(fixture_record) != 1:
        raise ValueError("Bundled lock file does not contain its expected project record.")
    return lock_content.replace(fixture_record, project_record).encode()
