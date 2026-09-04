from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.docker_validation_commands import DockerValidationCommands
from scaffold_compiler.docker_validation_resources import DockerValidationResources


class DockerValidationCommandsTests(unittest.TestCase):
    def test_image_and_container_commands_are_fixed_owned_and_exact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            docker = root / "docker.exe"
            docker.write_bytes(b"")
            resources = DockerValidationResources.create("run-1", "a" * 64)
            commands = DockerValidationCommands(docker, candidate, resources)

            specifications = (
                commands.build_image(),
                commands.check_non_root(),
                commands.start_health_container(),
                commands.inspect_container_health(),
                commands.remove_container(),
                commands.remove_image(),
            )

            self.assertTrue(all(item.argv[0] == str(docker) for item in specifications))
            self.assertTrue(all(item.cwd == candidate for item in specifications))
            self.assertTrue(all(isinstance(item.argv, tuple) for item in specifications))
            label = f"{resources.ownership_label_name}={resources.ownership_label_value}"
            self.assertIn(label, commands.build_image().argv)
            self.assertIn(label, commands.check_non_root().argv)
            self.assertIn(label, commands.start_health_container().argv)
            self.assertEqual(commands.remove_container().argv[-1], resources.container_name)
            self.assertEqual(commands.remove_image().argv[-1], resources.image_tag)
            self.assertTrue(
                any("os.geteuid()" in argument for argument in commands.check_non_root().argv)
            )

    def test_compose_commands_share_one_project_and_cleanup_removes_volumes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            docker = root / "docker"
            docker.write_bytes(b"")
            resources = DockerValidationResources.create("run-1", "a" * 64)
            commands = DockerValidationCommands(
                docker,
                candidate,
                resources,
                compose_password="temporary-secret",
            )

            specifications = (
                commands.compose_config(),
                commands.compose_up(),
                commands.compose_health(),
                commands.compose_down(),
            )

            for specification in specifications:
                self.assertEqual(specification.argv[1:5], (
                    "compose",
                    "--project-name",
                    resources.compose_project,
                    "--file",
                ))
                self.assertIn(("POSTGRES_PASSWORD", "temporary-secret"), specification.environment)
                self.assertIn("temporary-secret", specification.secrets)
            self.assertIn("--wait", commands.compose_up().argv)
            self.assertEqual(
                commands.compose_health().argv[-3:],
                ("--status", "running", "--services"),
            )
            self.assertEqual(
                commands.compose_down().argv[-4:],
                ("--volumes", "--remove-orphans", "--rmi", "local"),
            )

    def test_invalid_executable_candidate_or_password_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            docker = root / "docker"
            docker.write_bytes(b"")
            resources = DockerValidationResources.create("run-1", "a" * 64)

            with self.assertRaises(ValueError):
                DockerValidationCommands(root / "missing", candidate, resources)
            with self.assertRaises(ValueError):
                DockerValidationCommands(docker, root / "missing", resources)
            with self.assertRaises(ValueError):
                DockerValidationCommands(docker, candidate, resources, compose_password="")


if __name__ == "__main__":
    unittest.main()
