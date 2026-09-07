from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import (
    PlanCompilationError,
    compile_recipe_blueprint_plan,
    resolve_recipe_capabilities,
)
from scaffold_compiler.project_recipe_registry import (
    FASTAPI_RECIPE_ID,
    ProjectRecipe,
    build_builtin_project_recipe_registry,
    build_project_recipe_registry,
)


def _manifest(
    blueprint_id: str,
    *,
    provides: tuple[str, ...],
    requires: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
    validations: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "id": blueprint_id,
        "version": "1.0.0",
        "provides": list(provides),
        "requires": list(requires),
        "after": list(after),
        "conflicts": list(conflicts),
        "variables": {},
        "files": [
            {"target": target, "source": f"templates/{target}.template", "kind": "template"}
            for target in files
        ],
        "contributions": {},
        "validations": list(validations),
    }


def _write_catalog(root: Path, records: Mapping[str, dict[str, object]]) -> None:
    for directory_name, record in records.items():
        blueprint_directory = root / directory_name
        blueprint_directory.mkdir()
        (blueprint_directory / "blueprint.json").write_text(
            json.dumps(record),
            encoding="utf-8",
        )


def _recipe(
    allowed_blueprints: tuple[str, ...],
    *,
    required_capabilities: tuple[str, ...],
    version: str = "1.0.0",
    allowed_validations: tuple[str, ...] = (),
) -> ProjectRecipe:
    registry = build_project_recipe_registry(
        [
            {
                "schema_version": 1,
                "id": "test-recipe",
                "version": version,
                "answer_parser": "test-answers",
                "assembly_adapter": "test-assembly",
                "validation_adapter": "test-validation",
                "required_capabilities": list(required_capabilities),
                "capability_rules": [],
                "allowed_blueprints": list(allowed_blueprints),
                "allowed_validations": list(allowed_validations),
                "prerequisites": [],
            }
        ],
        trusted_answer_parser_keys={"test-answers"},
        trusted_assembly_adapter_keys={"test-assembly"},
        trusted_validation_adapter_keys={"test-validation"},
    )
    return registry.get("test-recipe")


class RecipeBlueprintPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_loads_schema_two_without_a_global_stage(self) -> None:
        _write_catalog(
            self.root,
            {
                "core": _manifest("core", provides=("core",), after=("quality",)),
                "quality": _manifest("quality", provides=("quality",)),
            },
        )

        manifest = load_blueprint_catalog(self.root).by_id("core")

        self.assertEqual(manifest.after, ("quality",))
        self.assertFalse(hasattr(manifest, "stage"))

    def test_rejects_a_capability_provided_only_outside_the_recipe_scope(self) -> None:
        _write_catalog(
            self.root,
            {
                "python": _manifest("python", provides=("python",)),
                "c": _manifest("c", provides=("c",)),
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(("python",), required_capabilities=("python",))

        with self.assertRaisesRegex(PlanCompilationError, "recipe scope"):
            compile_recipe_blueprint_plan(catalog, recipe, ("c",))

    def test_rejects_a_dependency_that_crosses_the_recipe_scope(self) -> None:
        _write_catalog(
            self.root,
            {
                "api": _manifest("api", provides=("api",), requires=("python",)),
                "python": _manifest("python", provides=("python",)),
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(("api",), required_capabilities=("api",))

        with self.assertRaisesRegex(PlanCompilationError, "recipe scope"):
            compile_recipe_blueprint_plan(catalog, recipe, ("api",))

    def test_ignores_duplicate_capability_providers_outside_the_recipe_scope(self) -> None:
        _write_catalog(
            self.root,
            {
                "c-quality": _manifest("c-quality", provides=("quality",)),
                "python-quality": _manifest("python-quality", provides=("quality",)),
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(("c-quality",), required_capabilities=("quality",))

        plan = compile_recipe_blueprint_plan(catalog, recipe, ())

        self.assertEqual(plan.blueprint_ids, ("c-quality",))

    def test_rejects_a_validation_gate_outside_the_recipe_scope(self) -> None:
        _write_catalog(
            self.root,
            {
                "core": _manifest(
                    "core",
                    provides=("core",),
                    validations=("arbitrary-command",),
                )
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(("core",), required_capabilities=("core",))

        with self.assertRaisesRegex(PlanCompilationError, "validation.*recipe scope"):
            compile_recipe_blueprint_plan(catalog, recipe, ())

    def test_uses_explicit_optional_ordering_then_stable_blueprint_id_order(self) -> None:
        _write_catalog(
            self.root,
            {
                "alpha": _manifest("alpha", provides=("alpha",), after=("zulu",)),
                "bravo": _manifest("bravo", provides=("bravo",)),
                "zulu": _manifest("zulu", provides=("zulu",)),
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(
            ("alpha", "bravo", "zulu"),
            required_capabilities=("alpha", "bravo", "zulu"),
        )

        plan = compile_recipe_blueprint_plan(catalog, recipe, ())

        self.assertEqual(plan.blueprint_ids, ("bravo", "zulu", "alpha"))

    def test_rejects_cycles_created_by_explicit_ordering(self) -> None:
        _write_catalog(
            self.root,
            {
                "alpha": _manifest("alpha", provides=("alpha",), after=("bravo",)),
                "bravo": _manifest("bravo", provides=("bravo",), after=("alpha",)),
            },
        )
        catalog = load_blueprint_catalog(self.root)
        recipe = _recipe(
            ("alpha", "bravo"),
            required_capabilities=("alpha", "bravo"),
        )

        with self.assertRaisesRegex(PlanCompilationError, "cycle"):
            compile_recipe_blueprint_plan(catalog, recipe, ())

    def test_plan_digest_binds_recipe_identity(self) -> None:
        _write_catalog(self.root, {"core": _manifest("core", provides=("core",))})
        catalog = load_blueprint_catalog(self.root)

        first = compile_recipe_blueprint_plan(
            catalog,
            _recipe(("core",), required_capabilities=("core",), version="1.0.0"),
            (),
        )
        second = compile_recipe_blueprint_plan(
            catalog,
            _recipe(("core",), required_capabilities=("core",), version="1.0.1"),
            (),
        )

        self.assertEqual(first.recipe_id, "test-recipe")
        self.assertNotEqual(first.digest, second.digest)
        self.assertIn(b'"recipe_version":"1.0.0"', first.serialize())

    def test_resolves_builtin_fastapi_capabilities_from_normalized_answers(self) -> None:
        recipe = build_builtin_project_recipe_registry().get(FASTAPI_RECIPE_ID)

        capabilities = resolve_recipe_capabilities(
            recipe,
            {"database": "postgres", "delivery": "docker", "package_name": "example"},
        )

        self.assertEqual(
            capabilities,
            (
                "docker-delivery",
                "final-project-assembly",
                "postgres-docker-integration",
                "postgres-persistence",
            ),
        )


if __name__ == "__main__":
    unittest.main()
