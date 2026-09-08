"""Compose the four supported V1 configurations into isolated candidates."""

from __future__ import annotations

import json
from pathlib import Path

from scaffold_compiler.blueprint_catalog import BlueprintCatalog, load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    GenerationPlan,
    compile_recipe_blueprint_plan,
)
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
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyRequest,
    assemble_project_candidate,
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_configuration import (
    ContainerChoice,
    DatabaseChoice,
    ProjectConfiguration,
)
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_ASSEMBLY_ADAPTER_KEY,
    FASTAPI_RECIPE_ID,
    build_builtin_project_recipe_registry,
)
from scaffold_compiler.recipe_project_configuration import convert_legacy_project_configuration
from scaffold_compiler.strict_template_renderer import render_strict_template


def compile_v1_candidate(
    configuration: ProjectConfiguration,
    workspace: Path,
    *,
    catalog_root: Path | None = None,
) -> CandidateAssemblyResult:
    """Compile one supported configuration without writing its final target path."""
    resolved_catalog_root = catalog_root or Path(__file__).parents[2] / "blueprints"
    catalog, plan = compile_v1_plan(configuration, catalog_root=resolved_catalog_root)
    return materialize_v1_candidate(
        configuration,
        workspace,
        catalog=catalog,
        plan=plan,
        catalog_root=resolved_catalog_root,
    )


def compile_v1_plan(
    configuration: ProjectConfiguration,
    *,
    catalog_root: Path | None = None,
) -> tuple[BlueprintCatalog, GenerationPlan]:
    """Resolve the deterministic V1 plan without writing a candidate tree."""
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
    recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)
    return catalog, compile_recipe_blueprint_plan(catalog, recipe, tuple(requested))


def materialize_v1_candidate(
    configuration: ProjectConfiguration,
    workspace: Path,
    *,
    catalog: BlueprintCatalog,
    plan: GenerationPlan,
    catalog_root: Path | None = None,
) -> CandidateAssemblyResult:
    """Delegate legacy FastAPI inputs through the trusted assembly boundary."""
    resolved_catalog_root = catalog_root or Path(__file__).parents[2] / "blueprints"
    recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)
    recipe_configuration = convert_legacy_project_configuration(configuration)
    request = ProjectAssemblyRequest(
        configuration=recipe_configuration,
        recipe=recipe,
        catalog=catalog,
        plan=plan,
        workspace=workspace,
        catalog_root=resolved_catalog_root,
    )
    registry = build_project_assembly_adapter_registry(
        ((FASTAPI_ASSEMBLY_ADAPTER_KEY, _assemble_fastapi_candidate),)
    )
    return assemble_project_candidate(request, registry)


def _assemble_fastapi_candidate(
    request: ProjectAssemblyRequest,
) -> CandidateAssemblyResult:
    answers = request.configuration.answers
    if set(answers) != {"database", "delivery", "package_name"}:
        raise ValueError("FastAPI assembly answers do not match the trusted schema.")
    package_name = answers["package_name"]
    database = answers["database"]
    delivery = answers["delivery"]
    if (
        not isinstance(package_name, str)
        or not isinstance(database, str)
        or not isinstance(delivery, str)
    ):
        raise ValueError("FastAPI assembly answers have invalid types.")
    try:
        legacy_configuration = ProjectConfiguration(
            project_name=request.configuration.project_name,
            package_name=package_name,
            target_directory=request.configuration.target_directory,
            database=DatabaseChoice(database),
            container=ContainerChoice(delivery),
        )
    except ValueError as error:
        raise ValueError("FastAPI assembly answers contain an unsupported choice.") from error
    return _materialize_fastapi_candidate(
        legacy_configuration,
        request.workspace,
        catalog=request.catalog,
        plan=request.plan,
        catalog_root=request.catalog_root,
    )


def _materialize_fastapi_candidate(
    configuration: ProjectConfiguration,
    workspace: Path,
    *,
    catalog: BlueprintCatalog,
    plan: GenerationPlan,
    catalog_root: Path,
) -> CandidateAssemblyResult:
    """Materialize one normalized FastAPI request into its isolated workspace."""

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
            content=_render_lock_file(catalog_root, configuration),
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
    asgi_imports = f"""from {package}.application import (
    create_application,
)
from {package}.configuration.application_settings import (
    ApplicationSettings,
)
from {package}.observability.logging_configuration import (
    configure_logging,
)"""
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

from {package}.application import (
    create_application,
)
from {package}.configuration.application_settings import (
    ApplicationSettings,
)
from {package}.observability.logging_configuration import (
    configure_logging,
)
from {package}.persistence.database_engine import (
    create_database_engine,
)
from {package}.persistence.database_readiness import (
    database_is_ready,
)
from {package}.persistence.database_session import (
    create_session_factory,
)"""
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
