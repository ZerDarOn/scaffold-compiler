from __future__ import annotations

import unittest
from pathlib import Path

from scaffold_compiler.workspace_path_budget import (
    MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS,
    WorkspacePathBudgetError,
    validate_workspace_path_budget,
)


class WorkspacePathBudgetTests(unittest.TestCase):
    def test_windows_accepts_the_exact_utf16_budget(self) -> None:
        workspace = Path("a" * MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS)

        validate_workspace_path_budget(workspace, run_id="run-1", platform_name="nt")

    def test_windows_rejects_one_utf16_unit_over_budget_without_logging_path(self) -> None:
        workspace = Path("private-" + "a" * MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS)

        with (
            self.assertLogs("scaffold_compiler.workspace_path_budget", level="WARNING") as captured,
            self.assertRaises(WorkspacePathBudgetError) as error_context,
        ):
            validate_workspace_path_budget(workspace, run_id="run-2", platform_name="nt")

        self.assertIn("shorter target directory", str(error_context.exception))
        log_output = "\n".join(captured.output)
        self.assertIn("run_id=run-2", log_output)
        self.assertIn("platform=windows", log_output)
        self.assertNotIn("private-", log_output)

    def test_windows_counts_non_bmp_characters_as_two_utf16_units(self) -> None:
        workspace = Path("a" * (MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS - 1) + "😀")

        with self.assertRaises(WorkspacePathBudgetError):
            validate_workspace_path_budget(workspace, run_id="run-3", platform_name="nt")

    def test_non_windows_platform_does_not_apply_the_legacy_path_budget(self) -> None:
        workspace = Path("a" * (MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS + 1_000))

        validate_workspace_path_budget(workspace, run_id="run-4", platform_name="posix")


if __name__ == "__main__":
    unittest.main()
