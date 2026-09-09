from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.generation_workspace_identity import (
    derive_generation_workspace,
    resolve_bound_generation_target,
)

CONFIGURATION_DIGEST = "a" * 64


class GenerationWorkspaceIdentityTests(unittest.TestCase):
    def test_current_workspace_name_is_fixed_length_deterministic_and_opaque(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / ("private-" + "x" * 120)

            first = derive_generation_workspace(
                target,
                run_id="customer-visible-run",
                configuration_digest=CONFIGURATION_DIGEST,
            )
            second = derive_generation_workspace(
                target,
                run_id="customer-visible-run",
                configuration_digest=CONFIGURATION_DIGEST,
            )

            self.assertEqual(first, second)
            self.assertEqual(first.parent, root)
            self.assertRegex(first.name, r"^\.scw-[0-9a-f]{32}$")
            self.assertNotIn(target.name, first.name)
            self.assertNotIn("customer-visible-run", first.name)

    def test_each_binding_fact_changes_the_workspace_identity(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            baseline = derive_generation_workspace(
                root / "delivery",
                run_id="run-1",
                configuration_digest=CONFIGURATION_DIGEST,
            )
            variants = {
                derive_generation_workspace(
                    root / "other",
                    run_id="run-1",
                    configuration_digest=CONFIGURATION_DIGEST,
                ),
                derive_generation_workspace(
                    root / "delivery",
                    run_id="run-2",
                    configuration_digest=CONFIGURATION_DIGEST,
                ),
                derive_generation_workspace(
                    root / "delivery",
                    run_id="run-1",
                    configuration_digest="b" * 64,
                ),
            }

            self.assertEqual(len(variants), 3)
            self.assertNotIn(baseline, variants)

    def test_current_workspace_resolves_only_its_bound_target(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "delivery"
            workspace = derive_generation_workspace(
                target,
                run_id="run-1",
                configuration_digest=CONFIGURATION_DIGEST,
            )

            resolved = resolve_bound_generation_target(
                workspace,
                run_id="run-1",
                configuration_digest=CONFIGURATION_DIGEST,
                target_name="delivery",
            )

            self.assertEqual(resolved, target)
            with self.assertRaises(ValueError):
                resolve_bound_generation_target(
                    root / (".scw-" + "0" * 32),
                    run_id="run-1",
                    configuration_digest=CONFIGURATION_DIGEST,
                    target_name="delivery",
                )

    def test_target_binding_rejects_non_leaf_or_noncanonical_names(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()

            for target_name in ("", ".", "..", "../victim", "child/victim", "child\\victim"):
                with self.subTest(target_name=target_name), self.assertRaises(ValueError):
                    resolve_bound_generation_target(
                        root / (".scw-" + "0" * 32),
                        run_id="run-1",
                        configuration_digest=CONFIGURATION_DIGEST,
                        target_name=target_name,
                    )

    def test_legacy_workspace_name_remains_recoverable_without_target_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / ".delivery.scaffold-run-1"

            target = resolve_bound_generation_target(
                workspace,
                run_id="run-1",
                configuration_digest=CONFIGURATION_DIGEST,
                target_name=None,
            )

            self.assertEqual(target, root / "delivery")
            with self.assertRaises(ValueError):
                resolve_bound_generation_target(
                    root / ".delivery.scaffold-other-run",
                    run_id="run-1",
                    configuration_digest=CONFIGURATION_DIGEST,
                    target_name=None,
                )


if __name__ == "__main__":
    unittest.main()
