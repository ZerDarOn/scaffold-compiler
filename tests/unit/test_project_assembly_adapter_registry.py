from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.blueprint_catalog import BlueprintCatalog, BlueprintManifest
from scaffold_compiler.blueprint_plan_compiler import GenerationPlan
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
    calculate_candidate_digest,
)
from scaffold_compiler.project_assembly_adapter_registry import (
    ProjectAssemblyAdapterRegistryError,
    ProjectAssemblyRequest,
    assemble_project_candidate,
    build_project_assembly_adapter_registry,
)
from scaffold_compiler.project_recipe_registry import ProjectRecipe
from scaffold_compiler.recipe_project_configuration import RecipeProjectConfiguration
from scaffold_compiler.v1_project_compiler import _assemble_fastapi_candidate


def _recipe() -> ProjectRecipe:
    return ProjectRecipe(
        recipe_id="test-recipe",
        version="1.0.0",
        answer_parser_key="test-answers",
        assembly_adapter_key="test-assembly",
        validation_adapter_key="test-validation",
        required_capabilities=("core",),
        capability_rules=(),
        allowed_blueprint_ids=("core",),
        allowed_validation_gates=(),
        prerequisites=(),
    )


def _request(root: Path, *, answers_json: str = "{}") -> ProjectAssemblyRequest:
    recipe = _recipe()
    catalog = BlueprintCatalog(
        (
            BlueprintManifest(
                blueprint_id="core",
                schema_version=2,
                version="1.0.0",
                provides=("core",),
                requires=(),
                after=(),
                conflicts=(),
                files=(),
                variables=(),
                variables_json="{}",
                contributions_json="{}",
                validations=(),
                directory=root / "blueprints" / "core",
            ),
        )
    )
    plan = GenerationPlan(
        requested_capabilities=("core",),
        capabilities=("core",),
        blueprint_ids=("core",),
        file_owners=(),
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
    )
    return ProjectAssemblyRequest(
        configuration=RecipeProjectConfiguration(
            schema_version=2,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            project_name="Test",
            target_directory=root / "delivery",
            _answers_json=answers_json,
        ),
        recipe=recipe,
        catalog=catalog,
        plan=plan,
        workspace=root / "workspace",
        catalog_root=root / "blueprints",
    )


def _successful_adapter(
    request: ProjectAssemblyRequest,
    *,
    owner: str = "core",
    plan_digest: str | None = None,
    add_unrecorded_file: bool = False,
    corrupt_metadata: bool = False,
) -> CandidateAssemblyResult:
    candidate = request.workspace / "candidate"
    candidate.mkdir()
    content = b"ok\n"
    (candidate / "result.txt").write_bytes(content)
    record = CandidateFileRecord(
        path="result.txt",
        owner=owner,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    result = CandidateAssemblyResult(
        root=candidate,
        files=(record,),
        digest=calculate_candidate_digest(candidate, (record,)),
        plan_digest=plan_digest or request.plan.digest,
    )
    if corrupt_metadata:
        result = replace(result, files=(replace(record, size=record.size + 1),))
    if add_unrecorded_file:
        (candidate / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    return result


class ProjectAssemblyAdapterRegistryTests(unittest.TestCase):
    def test_rejects_duplicate_adapter_keys(self) -> None:
        with self.assertRaises(ProjectAssemblyAdapterRegistryError) as error_context:
            build_project_assembly_adapter_registry(
                (("test-assembly", _successful_adapter), ("test-assembly", _successful_adapter))
            )

        self.assertEqual(error_context.exception.code, "duplicate_assembly_adapter")

    def test_rejects_unknown_adapter_before_writing(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(Path(directory))
            request.workspace.mkdir()
            registry = build_project_assembly_adapter_registry(())

            with self.assertRaises(ProjectAssemblyAdapterRegistryError) as error_context:
                assemble_project_candidate(request, registry)

            self.assertEqual(error_context.exception.code, "unknown_assembly_adapter")
            self.assertFalse((request.workspace / "candidate").exists())

    def test_rejects_configuration_or_plan_recipe_mismatch_before_writing(self) -> None:
        mismatch_cases = ("configuration", "plan")
        for mismatch in mismatch_cases:
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as directory:
                request = _request(Path(directory))
                request.workspace.mkdir()
                if mismatch == "configuration":
                    request = ProjectAssemblyRequest(
                        configuration=RecipeProjectConfiguration(
                            schema_version=2,
                            recipe_id="other-recipe",
                            recipe_version="1.0.0",
                            project_name="Test",
                            target_directory=request.configuration.target_directory,
                            _answers_json="{}",
                        ),
                        recipe=request.recipe,
                        catalog=request.catalog,
                        plan=request.plan,
                        workspace=request.workspace,
                        catalog_root=request.catalog_root,
                    )
                else:
                    request = ProjectAssemblyRequest(
                        configuration=request.configuration,
                        recipe=request.recipe,
                        catalog=request.catalog,
                        plan=GenerationPlan(
                            requested_capabilities=("core",),
                            capabilities=("core",),
                            blueprint_ids=("core",),
                            file_owners=(),
                            recipe_id="other-recipe",
                            recipe_version="1.0.0",
                        ),
                        workspace=request.workspace,
                        catalog_root=request.catalog_root,
                    )
                registry = build_project_assembly_adapter_registry(
                    (("test-assembly", _successful_adapter),)
                )

                with self.assertRaises(ProjectAssemblyAdapterRegistryError):
                    assemble_project_candidate(request, registry)

                self.assertFalse((request.workspace / "candidate").exists())

    def test_accepts_a_bound_and_verifiable_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(Path(directory))
            request.workspace.mkdir()
            registry = build_project_assembly_adapter_registry(
                (("test-assembly", _successful_adapter),)
            )

            result = assemble_project_candidate(request, registry)

            self.assertEqual(result.plan_digest, request.plan.digest)
            self.assertEqual(result.files[0].owner, "core")

    def test_rejects_invalid_adapter_output(self) -> None:
        invalid_adapters = (
            lambda request: _successful_adapter(request, owner="outside-scope"),
            lambda request: _successful_adapter(request, plan_digest="0" * 64),
            lambda request: _successful_adapter(request, add_unrecorded_file=True),
            lambda request: _successful_adapter(request, corrupt_metadata=True),
        )
        for adapter in invalid_adapters:
            with self.subTest(adapter=adapter), TemporaryDirectory() as directory:
                request = _request(Path(directory))
                request.workspace.mkdir()
                registry = build_project_assembly_adapter_registry((("test-assembly", adapter),))

                with self.assertRaises(ProjectAssemblyAdapterRegistryError):
                    assemble_project_candidate(request, registry)

    def test_fastapi_adapter_rejects_invalid_answer_types_before_writing(self) -> None:
        with TemporaryDirectory() as directory:
            request = _request(
                Path(directory),
                answers_json='{"database":1,"delivery":"none","package_name":"example"}',
            )
            request.workspace.mkdir()
            registry = build_project_assembly_adapter_registry(
                (("test-assembly", _assemble_fastapi_candidate),)
            )

            with self.assertRaises(ProjectAssemblyAdapterRegistryError):
                assemble_project_candidate(request, registry)

            self.assertFalse((request.workspace / "candidate").exists())


if __name__ == "__main__":
    unittest.main()
