from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import Mock, patch

from scaffold_compiler.capsule_package import build_capsule_package
from scaffold_compiler.command_line_interface import CommandOutcome, ScaffoldCommandService
from scaffold_compiler.release_entry import (
    main,
    resolve_cmake_executable,
    resolve_ctest_executable,
    resolve_docker_executable,
    resolve_uv_executable,
)

CAPSULE_ID = "12345678-1234-4678-9234-567812345678"


class SuccessfulRunApplication:
    def run_non_interactive(self, config_path: Path) -> CommandOutcome:
        return CommandOutcome(0, "completed")

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected command: {name}")


class ReleaseEntryTests(unittest.TestCase):
    def test_default_application_previews_legacy_and_v2_fastapi_through_generic_cli(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "delivery"
            config_path = root / "project.json"
            previews: list[dict[str, object]] = []
            configurations = (
                {
                    "container": "none",
                    "database": "none",
                    "package_name": "example",
                    "project_name": "Example API",
                    "target_directory": str(target),
                },
                {
                    "answers": {
                        "database": "none",
                        "delivery": "none",
                        "package_name": "example",
                    },
                    "project_name": "Example API",
                    "recipe": "python-fastapi-service",
                    "schema_version": 2,
                    "target_directory": str(target),
                },
            )

            for configuration in configurations:
                config_path.write_text(json.dumps(configuration), encoding="utf-8")
                stdout = io.StringIO()
                exit_code = main(
                    ["preview", "--config", str(config_path)],
                    entry_path=root / "source-entry.py",
                    stdout=stdout,
                    stderr=io.StringIO(),
                    environment={},
                )
                self.assertEqual(exit_code, 0)
                previews.append(json.loads(stdout.getvalue()))

            self.assertEqual(previews[0], previews[1])
            self.assertEqual(previews[0]["recipe"], "python-fastapi-service")
            self.assertEqual(previews[0]["recipe_version"], "1.0.0")

    def test_default_application_previews_both_cmake_warning_modes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "project.json"
            blueprints_by_mode: dict[bool, list[str]] = {}

            for strict_warnings in (False, True):
                config_path.write_text(
                    json.dumps(
                        {
                            "answers": {
                                "strict_warnings": strict_warnings,
                                "target_name": "example_cli",
                            },
                            "project_name": "Example CLI",
                            "recipe": "c-cmake-cli",
                            "schema_version": 2,
                            "target_directory": str(root / "delivery"),
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = io.StringIO()

                exit_code = main(
                    ["preview", "--config", str(config_path)],
                    entry_path=root / "source-entry.py",
                    stdout=stdout,
                    stderr=io.StringIO(),
                    environment={},
                )

                self.assertEqual(exit_code, 0)
                summary = json.loads(stdout.getvalue())
                self.assertEqual(summary["recipe"], "c-cmake-cli")
                blueprints_by_mode[strict_warnings] = summary["blueprints"]

            self.assertNotIn("cmake-strict-warnings", blueprints_by_mode[False])
            self.assertIn("cmake-strict-warnings", blueprints_by_mode[True])

    def test_successful_packaged_run_arms_external_cleanup_before_reporting_success(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule_root = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            entry = capsule_root / "scaffold_compiler.pyz"
            stdout = io.StringIO()
            stderr = io.StringIO()
            supervisor = Mock()

            with patch(
                "scaffold_compiler.release_entry.start_capsule_cleanup_supervisor",
                return_value=supervisor,
            ) as start_supervisor:
                exit_code = main(
                    [
                        "run",
                        "--config",
                        "project.json",
                        "--non-interactive",
                        "--confirm-finalize",
                        "FINALIZE",
                    ],
                    entry_path=entry,
                    application=cast(ScaffoldCommandService, SuccessfulRunApplication()),
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue(), "completed\n")
            self.assertEqual(stderr.getvalue(), "")
            cleanup_root = root / f".scaffold-capsule-cleanup-{CAPSULE_ID}"
            journal = cleanup_root / "capsule_cleanup_journal.json"
            self.assertTrue(journal.is_file())
            self.assertEqual(start_supervisor.call_args.kwargs["journal_path"], journal)

    def test_failed_command_does_not_arm_or_delete_capsule(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule_root = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            application = Mock()
            application.run_non_interactive.return_value = CommandOutcome(1, "failed")

            with patch(
                "scaffold_compiler.release_entry.start_capsule_cleanup_supervisor"
            ) as start_supervisor:
                exit_code = main(
                    [
                        "run",
                        "--config",
                        "project.json",
                        "--non-interactive",
                        "--confirm-finalize",
                        "FINALIZE",
                    ],
                    entry_path=capsule_root / "scaffold_compiler.pyz",
                    application=application,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

            self.assertEqual(exit_code, 1)
            start_supervisor.assert_not_called()
            self.assertTrue(capsule_root.exists())

    def test_uv_resolution_accepts_only_an_existing_ordinary_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "uv"
            uv.write_bytes(b"uv")

            self.assertEqual(
                resolve_uv_executable({"SCAFFOLD_COMPILER_UV": str(uv)}),
                uv.resolve(),
            )
            self.assertIsNone(
                resolve_uv_executable({"SCAFFOLD_COMPILER_UV": str(root / "missing")})
            )

    def test_docker_resolution_is_optional_and_uses_its_explicit_setting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_bytes(b"docker")

            self.assertEqual(
                resolve_docker_executable({"SCAFFOLD_COMPILER_DOCKER": str(docker)}),
                docker.resolve(),
            )
            self.assertIsNone(
                resolve_docker_executable({"SCAFFOLD_COMPILER_DOCKER": str(root / "missing")})
            )

    def test_cmake_tool_resolution_uses_only_fixed_settings_or_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake"
            ctest = root / "ctest"
            cmake.write_bytes(b"cmake")
            ctest.write_bytes(b"ctest")

            self.assertEqual(
                resolve_cmake_executable({"SCAFFOLD_COMPILER_CMAKE": str(cmake)}),
                cmake.resolve(),
            )
            self.assertEqual(
                resolve_ctest_executable({"SCAFFOLD_COMPILER_CTEST": str(ctest)}),
                ctest.resolve(),
            )

    def test_executable_resolution_uses_the_selected_environment_path(self) -> None:
        with TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_bytes(b"docker")

            with patch(
                "scaffold_compiler.release_entry.shutil.which",
                return_value=str(docker),
            ) as find_executable:
                resolved = resolve_docker_executable({"PATH": "selected-path"})

            self.assertEqual(resolved, docker.resolve())
            find_executable.assert_called_once_with("docker", path="selected-path")

    def test_tampered_capsule_fails_without_a_traceback_or_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "scaffold_compiler.pyz").write_bytes(b"zipapp")
            capsule = build_capsule_package(
                source,
                root / "capsule",
                included_paths=("scaffold_compiler.pyz",),
                capsule_id=CAPSULE_ID,
                compiler_version="1.0.0",
            )
            (capsule / "scaffold_compiler.pyz").write_bytes(b"tampered")
            stderr = io.StringIO()

            exit_code = main(
                ["--help"],
                entry_path=capsule / "scaffold_compiler.pyz",
                application=cast(ScaffoldCommandService, SuccessfulRunApplication()),
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(exit_code, 1)
            self.assertIn("ownership verification failed", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertTrue(capsule.exists())


if __name__ == "__main__":
    unittest.main()
