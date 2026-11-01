import tempfile
import unittest
from pathlib import Path

from email_agent.application.category_registry import CategoryRegistry
from email_agent.infrastructure.database.sqlite_category_repository import SQLiteCategoryRepository


class CategoryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = CategoryRegistry(
            SQLiteCategoryRepository(
                Path(self.temp.name) / "categories.db"
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_categories(self):
        ids = {item["id"] for item in self.registry.categories()}
        self.assertIn("job_applications", ids)
        self.assertIn("bills", ids)

    def test_proposal_requires_approval(self):
        created = self.registry.propose(
            "scholarships",
            "Scholarships",
            "Messages about scholarship opportunities",
        )
        self.assertTrue(created)

        ids = {item["id"] for item in self.registry.categories()}
        self.assertNotIn("scholarships", ids)

        proposal_id = self.registry.proposals()[0]["id"]
        self.assertTrue(self.registry.approve(proposal_id))

        ids = {item["id"] for item in self.registry.categories()}
        self.assertIn("scholarships", ids)

    def test_duplicate_proposal(self):
        self.assertTrue(
            self.registry.propose("security", "Security", "Security alerts")
        )
        self.assertFalse(
            self.registry.propose("security", "Security", "Security alerts")
        )

    def test_invalid_category_id(self):
        with self.assertRaises(ValueError):
            self.registry.propose(
                "../unsafe", "Unsafe", "Invalid category ID"
            )


if __name__ == "__main__":
    unittest.main()
