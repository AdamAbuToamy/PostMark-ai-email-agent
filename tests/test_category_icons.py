import tempfile
import unittest
from pathlib import Path

from email_agent.application.category_registry import CategoryRegistry
from email_agent.domain.category_icon_resolver import resolve_category_icon
from email_agent.infrastructure.database.sqlite_category_repository import (
    SQLiteCategoryRepository,
)


class CategoryIconTests(unittest.TestCase):
    def test_semantic_resolution(self):
        cases = {
            "Security": "shield",
            "Security Alerts": "shield",
            "Travel": "plane",
            "Invoices": "receipt",
            "Job Applications": "briefcase",
            "University": "graduation-cap",
            "Shipping": "package",
            "Health": "heart-pulse",
            "Notifications": "bell",
        }

        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(resolve_category_icon(name), expected)

    def test_icon_survives_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteCategoryRepository(
                Path(directory) / "categories.db"
            )
            registry = CategoryRegistry(repository)

            self.assertTrue(
                registry.propose(
                    "security",
                    "Security",
                    "Account security notifications",
                )
            )

            proposal = registry.proposals()[0]
            self.assertEqual(proposal["icon_key"], "shield")

            self.assertTrue(registry.approve(proposal["id"]))

            category = next(
                item for item in registry.categories()
                if item["id"] == "security"
            )
            self.assertEqual(category["icon_key"], "shield")


if __name__ == "__main__":
    unittest.main()
