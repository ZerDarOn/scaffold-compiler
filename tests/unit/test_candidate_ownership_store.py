from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.candidate_ownership_store import (
    CandidateOwnershipCorruptionError,
    CandidateOwnershipStore,
)
from scaffold_compiler.candidate_project_assembler import (
    CandidateAssemblyResult,
    CandidateFileRecord,
    calculate_candidate_digest,
)


def make_candidate(workspace: Path) -> CandidateAssemblyResult:
    candidate_root = workspace / "candidate"
    candidate_root.mkdir()
    content = b"# Example\n"
    (candidate_root / "README.md").write_bytes(content)
    files = (
        CandidateFileRecord(
            path="README.md",
            owner="project-quality",
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        ),
    )
    return CandidateAssemblyResult(
        root=candidate_root,
        files=files,
        digest=calculate_candidate_digest(candidate_root, files),
        plan_digest="a" * 64,
    )


class CandidateOwnershipStoreTests(unittest.TestCase):
    def test_round_trips_exact_candidate_ownership_deterministically(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve() / ".delivery.scaffold-run"
            workspace.mkdir()
            candidate = make_candidate(workspace)
            store = CandidateOwnershipStore(workspace / "candidate_ownership.json")

            store.save(candidate)
            first_bytes = store.path.read_bytes()
            loaded = store.load(workspace)
            store.save(candidate)

            self.assertEqual(loaded, candidate)
            self.assertEqual(store.path.read_bytes(), first_bytes)
            self.assertEqual(
                set(json.loads(first_bytes)),
                {"candidate_digest", "files", "plan_digest", "schema_version"},
            )

    def test_rejects_unknown_fields_duplicate_paths_and_digest_mismatch(self) -> None:
        mutations = ("unknown", "duplicate", "digest")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                workspace = Path(directory).resolve() / ".delivery.scaffold-run"
                workspace.mkdir()
                candidate = make_candidate(workspace)
                store = CandidateOwnershipStore(workspace / "candidate_ownership.json")
                store.save(candidate)
                record = json.loads(store.path.read_text(encoding="utf-8"))
                if mutation == "unknown":
                    record["unexpected"] = True
                elif mutation == "duplicate":
                    record["files"].append(record["files"][0])
                else:
                    record["candidate_digest"] = "0" * 64
                store.path.write_text(json.dumps(record), encoding="utf-8")

                with self.assertRaises(CandidateOwnershipCorruptionError):
                    store.load(workspace)

    def test_refuses_a_manifest_or_candidate_outside_the_fixed_workspace_children(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / ".delivery.scaffold-run"
            workspace.mkdir()
            candidate = make_candidate(workspace)

            with self.assertRaises(ValueError):
                CandidateOwnershipStore(root / "outside.json").save(candidate)

            store = CandidateOwnershipStore(workspace / "candidate_ownership.json")
            store.save(candidate)
            with self.assertRaises(CandidateOwnershipCorruptionError):
                store.load(root)


if __name__ == "__main__":
    unittest.main()
