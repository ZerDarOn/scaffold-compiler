from __future__ import annotations

import unittest
from pathlib import Path


class DocumentationContractTests(unittest.TestCase):
    readme: str
    user_guide: str
    author_guide: str

    @classmethod
    def setUpClass(cls) -> None:
        repository = Path(__file__).parents[2]
        cls.readme = (repository / "README.md").read_text(encoding="utf-8")
        cls.user_guide = (repository / "docs" / "user_guide.md").read_text(encoding="utf-8")
        cls.author_guide = (repository / "docs" / "trusted_recipe_authoring.md").read_text(
            encoding="utf-8"
        )

    def test_user_guide_covers_all_recipes_and_the_complete_public_lifecycle(self) -> None:
        for recipe_id in ("python-fastapi-service", "c-cmake-cli", "go-cli"):
            self.assertIn(recipe_id, self.user_guide)
        for command in ("init", "preview", "run", "inspect", "discard", "cleanup"):
            self.assertIn(f" {command} `", self.user_guide)
        for confirmation in (
            "--confirm-finalize FINALIZE",
            "--confirm DISCARD",
            "--confirm CLEANUP",
        ):
            self.assertIn(confirmation, self.user_guide)
        normalized_user_guide = " ".join(self.user_guide.split())
        self.assertIn("does not delete the published target", normalized_user_guide)
        self.assertIn("existing file is never overwritten", normalized_user_guide)
        self.assertIn("does not arm capsule self-cleanup", normalized_user_guide)
        self.assertIn("example.com/example-tool", self.user_guide)
        self.assertIn("120 UTF-16 code units", self.user_guide)
        self.assertIn(".scw-<workspace-id>", self.user_guide)
        self.assertIn("does not repeat the project directory name", normalized_user_guide)
        self.assertIn("docs/user_guide.md", self.readme)

    def test_author_guide_preserves_the_trusted_data_only_extension_boundary(self) -> None:
        for term in (
            "trusted built-in",
            "schema_version",
            "allowed_blueprints",
            "allowed_validations",
            "assembly adapter",
            "validation adapter",
            "cannot execute",
            "docs/trusted_recipe_authoring.md",
        ):
            document = self.readme if term.startswith("docs/") else self.author_guide
            self.assertIn(term, document)


if __name__ == "__main__":
    unittest.main()
