"""Aggregation boundary for the compiler's complete built-in recipe units."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scaffold_compiler.cmake_trusted_recipe_registration import (
    build_cmake_trusted_recipe_registration,
)
from scaffold_compiler.fastapi_trusted_recipe_registration import (
    build_fastapi_trusted_recipe_registration,
)
from scaffold_compiler.go_trusted_recipe_registration import (
    build_go_trusted_recipe_registration,
)
from scaffold_compiler.trusted_recipe_registration import TrustedRecipeRegistration


def build_builtin_trusted_recipe_registrations(
    *,
    catalog_root: Path,
    uv_executable: Path | None,
    docker_executable: Path | None,
    cmake_executable: Path | None,
    ctest_executable: Path | None,
    go_executable: Path | None,
    gofmt_executable: Path | None,
    environment: Mapping[str, str],
) -> tuple[TrustedRecipeRegistration, ...]:
    """Return one complete registration per trusted built-in recipe."""
    return (
        build_fastapi_trusted_recipe_registration(
            catalog_root=catalog_root,
            uv_executable=uv_executable,
            docker_executable=docker_executable,
            database_url=environment.get("DATABASE_URL"),
        ),
        build_cmake_trusted_recipe_registration(
            cmake_executable=cmake_executable,
            ctest_executable=ctest_executable,
        ),
        build_go_trusted_recipe_registration(
            go_executable=go_executable,
            gofmt_executable=gofmt_executable,
        ),
    )
