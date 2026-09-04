from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory

from scaffold_compiler.project_configuration import (
    ConfigurationValidationError,
    ContainerChoice,
    DatabaseChoice,
    ProjectConfiguration,
    parse_project_configuration,
)


class ProjectConfigurationTests(unittest.TestCase):
    def test_normalizes_valid_input_and_applies_golden_defaults(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            home_directory = working_directory / "home"
            home_directory.mkdir()

            configuration = parse_project_configuration(
                {
                    "project_name": "  Billing   API  ",
                    "target_directory": "billing-service",
                },
                working_directory=working_directory,
                home_directory=home_directory,
            )

            self.assertEqual(configuration.project_name, "Billing API")
            self.assertEqual(configuration.package_name, "billing_api")
            self.assertEqual(
                configuration.target_directory,
                (working_directory / "billing-service").resolve(),
            )
            self.assertIs(configuration.database, DatabaseChoice.POSTGRES)
            self.assertIs(configuration.container, ContainerChoice.DOCKER)

    def test_accepts_explicit_valid_package_and_non_default_choices(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            configuration = parse_project_configuration(
                {
                    "project_name": "Orders API",
                    "package_name": "orders_api",
                    "target_directory": "orders",
                    "database": "none",
                    "container": "none",
                },
                working_directory=working_directory,
                home_directory=working_directory / "home",
            )

            self.assertEqual(configuration.package_name, "orders_api")
            self.assertIs(configuration.database, DatabaseChoice.NONE)
            self.assertIs(configuration.container, ContainerChoice.NONE)

    def test_rejects_invalid_explicit_package_names(self) -> None:
        invalid_package_names = ("Billing-API", "2billing", "class", "BillingApi", "billing.api")

        for package_name in invalid_package_names:
            with self.subTest(package_name=package_name), TemporaryDirectory() as directory:
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_project_configuration(
                        {
                            "project_name": "Billing API",
                            "package_name": package_name,
                            "target_directory": "billing",
                        },
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.field, "package_name")
                self.assertEqual(error_context.exception.code, "invalid_package_name")

    def test_requires_explicit_package_when_name_cannot_be_safely_derived(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationValidationError) as error_context:
                parse_project_configuration(
                    {
                        "project_name": "项目接口",
                        "target_directory": "project-api",
                    },
                    working_directory=Path(directory),
                    home_directory=Path(directory) / "home",
                )

            self.assertEqual(error_context.exception.code, "package_name_required")

    def test_rejects_empty_or_control_character_project_names(self) -> None:
        for project_name in ("   ", "Billing\nAPI", "Billing\x00API"):
            with self.subTest(project_name=project_name), TemporaryDirectory() as directory:
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_project_configuration(
                        {
                            "project_name": project_name,
                            "target_directory": "billing",
                        },
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.field, "project_name")

    def test_rejects_root_home_existing_and_parentless_targets(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            home_directory = working_directory / "home"
            home_directory.mkdir()
            existing_target = working_directory / "existing"
            existing_target.mkdir()
            unsafe_targets = {
                "filesystem_root": Path(working_directory.anchor),
                "home": home_directory,
                "existing": existing_target,
                "missing_parent": working_directory / "missing" / "project",
            }

            for expected_code, target_directory in unsafe_targets.items():
                with self.subTest(expected_code=expected_code):
                    with self.assertRaises(ConfigurationValidationError) as error_context:
                        parse_project_configuration(
                            {
                                "project_name": "Billing API",
                                "target_directory": str(target_directory),
                            },
                            working_directory=working_directory,
                            home_directory=home_directory,
                        )

                    self.assertEqual(error_context.exception.field, "target_directory")
                    self.assertEqual(error_context.exception.code, expected_code)

    def test_rejects_unknown_database_and_container_choices(self) -> None:
        invalid_choices = (("database", "mysql"), ("container", "podman"))

        for field, value in invalid_choices:
            with self.subTest(field=field), TemporaryDirectory() as directory:
                with self.assertRaises(ConfigurationValidationError) as error_context:
                    parse_project_configuration(
                        {
                            "project_name": "Billing API",
                            "target_directory": "billing",
                            field: value,
                        },
                        working_directory=Path(directory),
                        home_directory=Path(directory) / "home",
                    )

                self.assertEqual(error_context.exception.field, field)
                self.assertEqual(error_context.exception.code, "unknown_choice")

    def test_rejects_secret_or_unknown_fields_without_leaking_values(self) -> None:
        secret_value = "do-not-log-this-secret"

        with TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationValidationError) as error_context:
                parse_project_configuration(
                    {
                        "project_name": "Billing API",
                        "target_directory": "billing",
                        "database_password": secret_value,
                    },
                    working_directory=Path(directory),
                    home_directory=Path(directory) / "home",
                )

            self.assertEqual(error_context.exception.code, "unknown_fields")
            self.assertNotIn(secret_value, str(error_context.exception))

    def test_digest_is_deterministic_for_equivalent_input_order(self) -> None:
        with TemporaryDirectory() as directory:
            working_directory = Path(directory)
            shared_arguments = {
                "working_directory": working_directory,
                "home_directory": working_directory / "home",
            }
            first = parse_project_configuration(
                {
                    "project_name": "Billing API",
                    "target_directory": "billing",
                    "database": "postgres",
                    "container": "docker",
                },
                **shared_arguments,
            )
            second = parse_project_configuration(
                {
                    "container": "docker",
                    "database": "postgres",
                    "target_directory": "billing",
                    "project_name": "Billing API",
                },
                **shared_arguments,
            )

            self.assertEqual(first.configuration_digest, second.configuration_digest)
            self.assertRegex(first.configuration_digest, r"^[0-9a-f]{64}$")

    def test_configuration_is_immutable(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = parse_project_configuration(
                {
                    "project_name": "Billing API",
                    "target_directory": "billing",
                },
                working_directory=Path(directory),
                home_directory=Path(directory) / "home",
            )

            with self.assertRaises(FrozenInstanceError):
                configuration.project_name = "Changed"  # type: ignore[misc]

            self.assertIsInstance(configuration, ProjectConfiguration)


if __name__ == "__main__":
    unittest.main()
