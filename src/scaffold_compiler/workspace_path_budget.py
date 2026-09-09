"""Platform path budget for generation workspaces."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

LOGGER = logging.getLogger(__name__)

MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS: Final = 120


class WorkspacePathBudgetError(ValueError):
    """Raised before generation when a workspace path is unsafe for local tools."""


def validate_workspace_path_budget(
    workspace: Path,
    *,
    run_id: str,
    platform_name: str | None = None,
) -> None:
    """Reject Windows workspaces that leave too little legacy tool path budget."""
    selected_platform = os.name if platform_name is None else platform_name
    if selected_platform != "nt":
        return

    path_units = len(os.fspath(workspace).encode("utf-16-le")) // 2
    if path_units <= MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS:
        return

    LOGGER.warning(
        "workspace_path_budget_rejected run_id=%s platform=windows "
        "actual_units=%d maximum_units=%d",
        run_id,
        path_units,
        MAXIMUM_WINDOWS_WORKSPACE_PATH_UTF16_UNITS,
    )
    raise WorkspacePathBudgetError(
        "Windows generation path is too long; choose a shorter target parent directory."
    )
