from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.blueprint_catalog import load_blueprint_catalog
from scaffold_compiler.blueprint_plan_compiler import compile_blueprint_plan
from scaffold_compiler.candidate_project_assembler import (
    CandidateAlreadyExistsError,
    CandidateAssemblyError,
    CandidateChangedError,
    assemble_candidate_project,
    calculate_candidate_digest,
    verify_candidate_digest,
)


def write_test_catalog(root: Path) -> None:
    blueprint = root / "core"
    templates = blueprint / "templates"
    static = blueprint / "static"
    templates.mkdir(parents=True)
    static.mkdir()
    (templates / "README.md.template").write_text(
        "# ${project_name}\nPackage: ${package_name}\n",
        encoding="utf-8",
    )
    static_bytes = b"root = true\n"
    (static / "editorconfig").write_bytes(static_bytes)
    manifest = {
        "schema_version": 2,
        "id": "core",
        "version": "1.0.0",
        "after": [],
        "provides": ["core"],
        "requires": [],
        "conflicts": [],
        "variables": {
            "project_name": {"type": "string", "required": True},
            "package_name": {"type": "string", "required": True},
        },
        "files": [
            {
                "target": "README.md",
                "source": "templates/README.md.template",
                "kind": "template",
            },
            {
                "target": ".editorconfig",
                "source": "static/editorconfig",
                "kind": "static",
            },
        ],
        "contributions": {},
        "validations": [],
    }
    (blueprint / "blueprint.json").write_text(json.dumps(manifest), encoding="utf-8")


class CandidateProjectAssemblerTests(unittest.TestCase):
    def test_materializes_templates_and_static_files_with_an_owned_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            workspace = root / "workspace"
            catalog_root.mkdir()
            workspace.mkdir()
            write_test_catalog(catalog_root)
            catalog = load_blueprint_catalog(catalog_root)
            plan = compile_blueprint_plan(catalog, ("core",))

            result = assemble_candidate_project(
                catalog,
                plan,
                workspace,
                values={"project_name": "Example", "package_name": "example"},
            )

            self.assertEqual(result.root, workspace / "candidate")
            self.assertEqual(
                (result.root / "README.md").read_text(encoding="utf-8"),
                "# Example\nPackage: example\n",
            )
            self.assertEqual((result.root / ".editorconfig").read_bytes(), b"root = true\n")
            self.assertEqual(
                tuple(item.path for item in result.files),
                (".editorconfig", "README.md"),
            )
            self.assertTrue(all(item.owner == "core" for item in result.files))
            self.assertEqual(result.digest, calculate_candidate_digest(result.root, result.files))

    def test_refuses_to_reuse_or_overwrite_an_existing_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            workspace = root / "workspace"
            catalog_root.mkdir()
            workspace.mkdir()
            (workspace / "candidate").mkdir()
            existing = workspace / "candidate" / "user.txt"
            existing.write_text("keep", encoding="utf-8")
            write_test_catalog(catalog_root)
            catalog = load_blueprint_catalog(catalog_root)
            plan = compile_blueprint_plan(catalog, ("core",))

            with self.assertRaises(CandidateAlreadyExistsError):
                assemble_candidate_project(
                    catalog,
                    plan,
                    workspace,
                    values={"project_name": "Example", "package_name": "example"},
                )

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_write_failure_removes_only_the_new_incomplete_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            workspace = root / "workspace"
            catalog_root.mkdir()
            workspace.mkdir()
            sentinel = workspace / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            write_test_catalog(catalog_root)
            catalog = load_blueprint_catalog(catalog_root)
            plan = compile_blueprint_plan(catalog, ("core",))

            with (
                patch(
                    "scaffold_compiler.candidate_project_assembler._write_new_file",
                    side_effect=OSError("simulated write failure"),
                ),
                self.assertRaises(CandidateAssemblyError),
            ):
                assemble_candidate_project(
                    catalog,
                    plan,
                    workspace,
                    values={"project_name": "Example", "package_name": "example"},
                )

            self.assertFalse((workspace / "candidate").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_candidate_modification_invalidates_the_frozen_digest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            workspace = root / "workspace"
            catalog_root.mkdir()
            workspace.mkdir()
            write_test_catalog(catalog_root)
            catalog = load_blueprint_catalog(catalog_root)
            plan = compile_blueprint_plan(catalog, ("core",))
            result = assemble_candidate_project(
                catalog,
                plan,
                workspace,
                values={"project_name": "Example", "package_name": "example"},
            )

            (result.root / "README.md").write_text("changed", encoding="utf-8")

            with self.assertRaises(CandidateChangedError):
                verify_candidate_digest(result)

    def test_repeated_assembly_in_separate_workspaces_is_byte_deterministic(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_root = root / "blueprints"
            catalog_root.mkdir()
            write_test_catalog(catalog_root)
            catalog = load_blueprint_catalog(catalog_root)
            plan = compile_blueprint_plan(catalog, ("core",))
            results = []
            for workspace_name in ("first", "second"):
                workspace = root / workspace_name
                workspace.mkdir()
                results.append(
                    assemble_candidate_project(
                        catalog,
                        plan,
                        workspace,
                        values={"project_name": "Example", "package_name": "example"},
                    )
                )

            self.assertEqual(results[0].files, results[1].files)
            self.assertEqual(results[0].digest, results[1].digest)
            self.assertEqual(
                hashlib.sha256((results[0].root / "README.md").read_bytes()).digest(),
                hashlib.sha256((results[1].root / "README.md").read_bytes()).digest(),
            )


if __name__ == "__main__":
    unittest.main()
