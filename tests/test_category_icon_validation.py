import tempfile
import unittest
from pathlib import Path

from email_agent.application.category_registry import CategoryRegistry
from email_agent.infrastructure.database.sqlite_category_repository import (
    SQLiteCategoryRepository,
)


class CategoryIconValidationTests(unittest.TestCase):
    def test_rejects_unsupported_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCategoryRepository(
                Path(directory) / "categories.db"
            )

            repository.initialize()

            with self.assertRaises(ValueError):
                repository.propose(
                    "invalid_icon",
                    "Invalid Icon",
                    "Test category",
                    "fake-icon",
                )

    def test_proposal_icon_survives_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = CategoryRegistry(
                SQLiteCategoryRepository(
                    Path(directory) / "categories.db"
                )
            )

            self.assertTrue(
                registry.propose(
                    "travel",
                    "Travel",
                    "Flight reservations",
                )
            )

            proposal = registry.proposals()[0]
            self.assertEqual(proposal["icon_key"], "plane")

            self.assertTrue(registry.approve(proposal["id"]))

            travel = next(
                item for item in registry.categories()
                if item["id"] == "travel"
            )

            self.assertEqual(travel["icon_key"], "plane")


if __name__ == "__main__":
    unittest.main()
