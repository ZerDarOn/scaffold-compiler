from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scaffold_compiler.path_safety import (
    AbsoluteProjectPathError,
    DuplicateTargetPathError,
    LinkedProjectPathError,
    PathTraversalError,
    resolve_safe_output_path,
    validate_unique_target_paths,
)
from scaffold_compiler.strict_template_renderer import (
    MissingTemplateVariableError,
    UnknownTemplateVariableError,
    UnsafeTemplateSyntaxError,
    render_strict_template,
)


class SafeProjectPathTests(unittest.TestCase):
    def test_resolves_a_relative_posix_path_inside_a_real_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            result = resolve_safe_output_path(root, "src/example/application.py")

            self.assertEqual(
                result,
                root.resolve() / "src" / "example" / "application.py",
            )

    def test_rejects_parent_traversal_for_posix_and_windows_spelling(self) -> None:
        for unsafe_path in ("../escape.txt", "src/../../escape.txt", "..\\escape.txt"):
            with (
                self.subTest(path=unsafe_path),
                TemporaryDirectory() as directory,
                self.assertRaises(PathTraversalError),
            ):
                resolve_safe_output_path(Path(directory), unsafe_path)

    def test_rejects_absolute_posix_drive_and_unc_paths_on_every_platform(self) -> None:
        for unsafe_path in ("/tmp/escape", "C:/escape", "\\\\server\\share\\escape"):
            with (
                self.subTest(path=unsafe_path),
                TemporaryDirectory() as directory,
                self.assertRaises(AbsoluteProjectPathError),
            ):
                resolve_safe_output_path(Path(directory), unsafe_path)

    def test_rejects_symbolic_links_in_an_existing_parent_chain(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            linked_parent = root / "linked"
            linked_parent.mkdir()

            with patch.object(Path, "is_symlink", autospec=True) as is_symlink:
                is_symlink.side_effect = lambda path: path.name == linked_parent.name
                with self.assertRaises(LinkedProjectPathError):
                    resolve_safe_output_path(root, "linked/output.txt")

    def test_rejects_windows_reparse_points_even_when_not_symlinks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "reparse"
            parent.mkdir()

            with (
                patch(
                    "scaffold_compiler.path_safety._is_windows_reparse_point",
                    side_effect=lambda path: path.name == parent.name,
                ),
                self.assertRaises(LinkedProjectPathError),
            ):
                resolve_safe_output_path(root, "reparse/output.txt")

    def test_rejects_duplicate_output_targets_before_writing(self) -> None:
        with self.assertRaises(DuplicateTargetPathError):
            validate_unique_target_paths(("README.md", "src/app.py", "README.md"))


class StrictTemplateRendererTests(unittest.TestCase):
    def test_replaces_only_declared_variables_and_leaves_no_placeholders(self) -> None:
        result = render_strict_template(
            "name=${project_name}\npackage=${package_name}\n",
            declared_variables=frozenset({"project_name", "package_name"}),
            values={"project_name": "Example", "package_name": "example"},
        )

        self.assertEqual(result, "name=Example\npackage=example\n")
        self.assertNotIn("${", result)

    def test_rejects_unknown_placeholder_or_supplied_variable(self) -> None:
        cases = (
            ("${unknown}", {"project_name": "Example"}),
            ("${project_name}", {"project_name": "Example", "unknown": "value"}),
        )

        for template, values in cases:
            with self.subTest(template=template), self.assertRaises(UnknownTemplateVariableError):
                render_strict_template(
                    template,
                    declared_variables=frozenset({"project_name"}),
                    values=values,
                )

    def test_rejects_a_missing_variable_value(self) -> None:
        with self.assertRaises(MissingTemplateVariableError):
            render_strict_template(
                "${project_name}/${package_name}",
                declared_variables=frozenset({"project_name", "package_name"}),
                values={"project_name": "Example"},
            )

    def test_rejects_template_code_syntax_without_executing_it(self) -> None:
        with TemporaryDirectory() as directory:
            marker = Path(directory) / "executed.txt"
            malicious_templates = (
                "{{ dangerous() }}",
                "{% import os %}",
                f"${{__import__('pathlib').Path('{marker}').touch()}}",
            )

            for template in malicious_templates:
                with self.subTest(template=template), self.assertRaises(UnsafeTemplateSyntaxError):
                    render_strict_template(
                        template,
                        declared_variables=frozenset(),
                        values={},
                    )

            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
