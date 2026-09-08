"""Independent expected outcomes for every supported V1 project combination."""

from __future__ import annotations

from dataclasses import dataclass

COMMON_PATHS = frozenset(
    {
        ".editorconfig",
        ".env.example",
        ".github/workflows/quality.yml",
        ".gitignore",
        ".python-version",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "src/{package_name}/__init__.py",
        "src/{package_name}/api/__init__.py",
        "src/{package_name}/api/api_router.py",
        "src/{package_name}/api/health_routes.py",
        "src/{package_name}/application.py",
        "src/{package_name}/asgi.py",
        "src/{package_name}/configuration/__init__.py",
        "src/{package_name}/configuration/application_settings.py",
        "src/{package_name}/errors/__init__.py",
        "src/{package_name}/errors/application_error.py",
        "src/{package_name}/errors/http_exception_mapping.py",
        "src/{package_name}/observability/__init__.py",
        "src/{package_name}/observability/logging_configuration.py",
        "src/{package_name}/observability/request_context_middleware.py",
        "tests/unit/test_application.py",
        "tests/integration/test_health_routes.py",
    }
)
POSTGRES_PATHS = frozenset(
    {
        "alembic.ini",
        "migrations/env.py",
        "migrations/versions/.gitkeep",
        "src/{package_name}/persistence/__init__.py",
        "src/{package_name}/persistence/database_engine.py",
        "src/{package_name}/persistence/database_readiness.py",
        "src/{package_name}/persistence/database_session.py",
        "tests/unit/test_database_session.py",
    }
)
DOCKER_PATHS = frozenset({".dockerignore", "Dockerfile"})
COMPOSE_PATHS = frozenset({"compose.yaml"})


@dataclass(frozen=True, slots=True)
class GoldenProjectSpecification:
    matrix_id: str
    database: str
    container: str
    required_paths: frozenset[str]
    forbidden_paths: frozenset[str]
    runtime_dependencies: frozenset[str]
    required_endpoints: tuple[str, ...] = (
        "/api/v1",
        "/health/live",
        "/health/ready",
        "/openapi.json",
    )
    asgi_entrypoint: str = "{package_name}.asgi:application"


BASE_DEPENDENCIES = frozenset({"fastapi", "pydantic-settings", "uvicorn"})
POSTGRES_DEPENDENCIES = frozenset({"alembic", "asyncpg", "sqlalchemy"})
ALL_OPTIONAL_PATHS = POSTGRES_PATHS | DOCKER_PATHS | COMPOSE_PATHS

GOLDEN_PROJECT_SPECIFICATIONS = (
    GoldenProjectSpecification(
        matrix_id="M-01",
        database="none",
        container="none",
        required_paths=COMMON_PATHS,
        forbidden_paths=ALL_OPTIONAL_PATHS,
        runtime_dependencies=BASE_DEPENDENCIES,
    ),
    GoldenProjectSpecification(
        matrix_id="M-02",
        database="none",
        container="docker",
        required_paths=COMMON_PATHS | DOCKER_PATHS,
        forbidden_paths=POSTGRES_PATHS | COMPOSE_PATHS,
        runtime_dependencies=BASE_DEPENDENCIES,
    ),
    GoldenProjectSpecification(
        matrix_id="M-03",
        database="postgres",
        container="none",
        required_paths=COMMON_PATHS | POSTGRES_PATHS,
        forbidden_paths=DOCKER_PATHS | COMPOSE_PATHS,
        runtime_dependencies=BASE_DEPENDENCIES | POSTGRES_DEPENDENCIES,
    ),
    GoldenProjectSpecification(
        matrix_id="M-04",
        database="postgres",
        container="docker",
        required_paths=COMMON_PATHS | POSTGRES_PATHS | DOCKER_PATHS | COMPOSE_PATHS,
        forbidden_paths=frozenset(),
        runtime_dependencies=BASE_DEPENDENCIES | POSTGRES_DEPENDENCIES,
    ),
)


CMAKE_COMMON_PATHS = frozenset(
    {
        ".editorconfig",
        ".github/workflows/quality.yml",
        ".gitignore",
        "CMakeLists.txt",
        "CMakePresets.json",
        "README.md",
        "include/{target_name}/calculator.h",
        "src/calculator.c",
        "src/main.c",
        "tests/test_calculator.c",
    }
)
CMAKE_STRICT_WARNING_PATHS = frozenset({"cmake/StrictWarnings.cmake"})
CMAKE_GENERATOR_MARKERS = ("scaffold_compiler", "blueprint", "journal", "run_id")


@dataclass(frozen=True, slots=True)
class CMakeGoldenProjectSpecification:
    matrix_id: str
    strict_warnings: bool
    required_paths: frozenset[str]
    forbidden_paths: frozenset[str]
    required_cmake_tokens: tuple[str, ...]
    forbidden_cmake_tokens: tuple[str, ...]
    expected_output: str = "scaffold compiler c example\n"


CMAKE_GOLDEN_PROJECT_SPECIFICATIONS = (
    CMakeGoldenProjectSpecification(
        matrix_id="C-01",
        strict_warnings=False,
        required_paths=CMAKE_COMMON_PATHS,
        forbidden_paths=CMAKE_STRICT_WARNING_PATHS,
        required_cmake_tokens=(
            "cmake_minimum_required(VERSION 3.20)",
            "LANGUAGES C",
            "C_STANDARD 11",
            "add_library(",
            "add_executable(",
            "include(CTest)",
            "add_test(",
        ),
        forbidden_cmake_tokens=("StrictWarnings",),
    ),
    CMakeGoldenProjectSpecification(
        matrix_id="C-02",
        strict_warnings=True,
        required_paths=CMAKE_COMMON_PATHS | CMAKE_STRICT_WARNING_PATHS,
        forbidden_paths=frozenset(),
        required_cmake_tokens=(
            "cmake_minimum_required(VERSION 3.20)",
            "LANGUAGES C",
            "C_STANDARD 11",
            "add_library(",
            "add_executable(",
            "include(CTest)",
            "add_test(",
            "include(cmake/StrictWarnings.cmake)",
        ),
        forbidden_cmake_tokens=(),
    ),
)
