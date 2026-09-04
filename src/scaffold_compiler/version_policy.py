"""Frozen toolchain and generated-project dependency versions."""

from types import MappingProxyType
from typing import Final

GENERATOR_MINIMUM_PYTHON: Final = (3, 11)
GENERATED_PROJECT_PYTHON: Final = "3.14"
UV_VERSION: Final = "0.12.9"
SETUPTOOLS_VERSION: Final = "84.0.0"

GENERATED_PROJECT_RUNTIME_DEPENDENCIES: Final = MappingProxyType(
    {
        "fastapi": "0.141.1",
        "pydantic-settings": "2.15.0",
        "uvicorn[standard]": "0.52.4",
    }
)

GENERATED_PROJECT_POSTGRES_DEPENDENCIES: Final = MappingProxyType(
    {
        "alembic": "1.19.1",
        "asyncpg": "0.31.0",
        "sqlalchemy[asyncio]": "2.0.52",
    }
)

GENERATED_PROJECT_DEVELOPMENT_DEPENDENCIES: Final = MappingProxyType(
    {
        "httpx": "0.28.1",
        "mypy": "2.3.1",
        "pytest": "9.1.1",
        "ruff": "0.16.6",
    }
)
