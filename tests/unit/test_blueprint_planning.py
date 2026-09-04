from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import (
    BlueprintCatalogError,
    load_blueprint_catalog,
)
from scaffold_compiler.blueprint_plan_compiler import (
    PlanCompilationError,
    compile_blueprint_plan,
)


def manifest(
    blueprint_id: str,
    *,
    stage: str,
    provides: tuple[str, ...],
    requires: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "id": blueprint_id,
        "version": "1.0.0",
        "stage": stage,
        "provides": list(provides),
        "requires": list(requires),
        "conflicts": list(conflicts),
        "variables": {},
        "files": [
            {"target": target, "source": f"templates/{target}.template", "kind": "template"}
            for target in files
        ],
        "contributions": {},
        "validations": [],
    }
    if extra:
        record.update(extra)
    return record


def write_catalog(root: Path, records: Mapping[str, dict[str, object]]) -> None:
    for directory_name, record in records.items():
        blueprint_directory = root / directory_name
        blueprint_directory.mkdir()
        (blueprint_directory / "blueprint.json").write_text(
            json.dumps(record),
            encoding="utf-8",
        )


class BlueprintCatalogTests(unittest.TestCase):
    def test_loads_a_strict_declarative_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_catalog(
                root,
                {
                    "quality": manifest(
                        "quality",
                        stage="project-quality",
                        provides=("quality",),
                        files=(".editorconfig",),
                    )
                },
            )

            catalog = load_blueprint_catalog(root)

            loaded = catalog.by_id("quality")
            self.assertEqual(loaded.provides, ("quality",))
            self.assertEqual(loaded.files[0].target, ".editorconfig")

    def test_rejects_unknown_or_executable_manifest_fields(self) -> None:
        invalid_extras: tuple[dict[str, object], ...] = (
            {"unexpected": True},
            {"shell": "rm -rf ."},
            {"download_url": "https://example.invalid/payload"},
        )

        for index, extra in enumerate(invalid_extras):
            with self.subTest(extra=extra), TemporaryDirectory() as directory:
                root = Path(directory)
                write_catalog(
                    root,
                    {
                        f"invalid-{index}": manifest(
                            "invalid",
                            stage="project-quality",
                            provides=("quality",),
                            extra=extra,
                        )
                    },
                )

                with self.assertRaises(BlueprintCatalogError):
                    load_blueprint_catalog(root)

    def test_rejects_duplicate_blueprint_ids(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = manifest(
                "duplicate",
                stage="project-quality",
                provides=("quality",),
            )
            write_catalog(root, {"first": duplicate, "second": duplicate})

            with self.assertRaises(BlueprintCatalogError):
                load_blueprint_catalog(root)


class BlueprintPlanCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_resolves_capability_closure_in_dependency_and_stage_order(self) -> None:
        write_catalog(
            self.root,
            {
                "api": manifest(
                    "api",
                    stage="fastapi-http-api",
                    provides=("api",),
                    requires=("python",),
                    files=("src/app/application.py",),
                ),
                "quality": manifest(
                    "quality",
                    stage="project-quality",
                    provides=("quality",),
                    files=(".editorconfig",),
                ),
                "python": manifest(
                    "python",
                    stage="python-runtime",
                    provides=("python",),
                    requires=("quality",),
                    files=(".python-version",),
                ),
            },
        )

        plan = compile_blueprint_plan(load_blueprint_catalog(self.root), ("api",))

        self.assertEqual(plan.blueprint_ids, ("quality", "python", "api"))
        self.assertEqual(
            plan.file_owners,
            (
                (".editorconfig", "quality"),
                (".python-version", "python"),
                ("src/app/application.py", "api"),
            ),
        )

    def test_rejects_missing_capability(self) -> None:
        write_catalog(
            self.root,
            {
                "api": manifest(
                    "api",
                    stage="fastapi-http-api",
                    provides=("api",),
                    requires=("python",),
                )
            },
        )

        with self.assertRaisesRegex(PlanCompilationError, "python"):
            compile_blueprint_plan(load_blueprint_catalog(self.root), ("api",))

    def test_rejects_selected_conflicts(self) -> None:
        write_catalog(
            self.root,
            {
                "postgres": manifest(
                    "postgres",
                    stage="postgres-persistence",
                    provides=("postgres",),
                    conflicts=("sqlite",),
                ),
                "sqlite": manifest(
                    "sqlite",
                    stage="postgres-persistence",
                    provides=("sqlite",),
                ),
            },
        )

        with self.assertRaisesRegex(PlanCompilationError, "conflict"):
            compile_blueprint_plan(
                load_blueprint_catalog(self.root),
                ("postgres", "sqlite"),
            )

    def test_rejects_capability_cycles(self) -> None:
        write_catalog(
            self.root,
            {
                "a": manifest(
                    "a",
                    stage="project-quality",
                    provides=("a",),
                    requires=("b",),
                ),
                "b": manifest(
                    "b",
                    stage="project-quality",
                    provides=("b",),
                    requires=("a",),
                ),
            },
        )

        with self.assertRaisesRegex(PlanCompilationError, "cycle"):
            compile_blueprint_plan(load_blueprint_catalog(self.root), ("a",))

    def test_rejects_dependency_on_a_later_stage(self) -> None:
        write_catalog(
            self.root,
            {
                "early": manifest(
                    "early",
                    stage="project-quality",
                    provides=("early",),
                    requires=("late",),
                ),
                "late": manifest(
                    "late",
                    stage="python-runtime",
                    provides=("late",),
                ),
            },
        )

        with self.assertRaisesRegex(PlanCompilationError, "stage"):
            compile_blueprint_plan(load_blueprint_catalog(self.root), ("early",))

    def test_rejects_duplicate_output_file_ownership(self) -> None:
        write_catalog(
            self.root,
            {
                "first": manifest(
                    "first",
                    stage="project-quality",
                    provides=("first",),
                    files=("README.md",),
                ),
                "second": manifest(
                    "second",
                    stage="python-runtime",
                    provides=("second",),
                    requires=("first",),
                    files=("README.md",),
                ),
            },
        )

        with self.assertRaisesRegex(PlanCompilationError, "README.md"):
            compile_blueprint_plan(load_blueprint_catalog(self.root), ("second",))

    def test_plan_serialization_and_digest_are_stable(self) -> None:
        records = {
            "zulu": manifest(
                "zulu",
                stage="project-quality",
                provides=("zulu",),
            ),
            "alpha": manifest(
                "alpha",
                stage="project-quality",
                provides=("alpha",),
            ),
        }
        write_catalog(self.root, records)
        catalog = load_blueprint_catalog(self.root)

        first = compile_blueprint_plan(catalog, ("zulu", "alpha"))
        second = compile_blueprint_plan(catalog, ("alpha", "zulu"))

        self.assertEqual(first.blueprint_ids, ("alpha", "zulu"))
        self.assertEqual(first.serialize(), second.serialize())
        self.assertEqual(first.digest, second.digest)

    def test_bundled_catalog_compiles_all_four_v1_combinations(self) -> None:
        catalog_root = Path(__file__).parents[2] / "blueprints"
        catalog = load_blueprint_catalog(catalog_root)
        combinations = (
            (
                ("final-project-assembly",),
                (
                    "project-quality",
                    "python-runtime",
                    "fastapi-http-api",
                    "final-project-assembly",
                ),
            ),
            (
                ("docker-delivery", "final-project-assembly"),
                (
                    "project-quality",
                    "python-runtime",
                    "fastapi-http-api",
                    "docker-delivery",
                    "final-project-assembly",
                ),
            ),
            (
                ("postgres-persistence", "final-project-assembly"),
                (
                    "project-quality",
                    "python-runtime",
                    "fastapi-http-api",
                    "postgres-persistence",
                    "final-project-assembly",
                ),
            ),
            (
                ("postgres-docker-integration", "final-project-assembly"),
                (
                    "project-quality",
                    "python-runtime",
                    "fastapi-http-api",
                    "postgres-persistence",
                    "docker-delivery",
                    "postgres-docker-integration",
                    "final-project-assembly",
                ),
            ),
        )

        for requested, expected_blueprints in combinations:
            with self.subTest(requested=requested):
                plan = compile_blueprint_plan(catalog, requested)
                self.assertEqual(plan.blueprint_ids, expected_blueprints)


if __name__ == "__main__":
    unittest.main()
