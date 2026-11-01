import tempfile
import unittest
from pathlib import Path

from email_agent.application.category_registry import CategoryRegistry
from email_agent.infrastructure.database.sqlite_category_repository import SQLiteCategoryRepository


class CategoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = CategoryRegistry(
            SQLiteCategoryRepository(
                Path(self.temp.name) / "categories.db"
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_reject_keeps_category_inactive(self):
        self.registry.propose(
            "security", "Security", "Security notifications"
        )
        proposal_id = self.registry.proposals()[0]["id"]

        self.assertTrue(self.registry.reject(proposal_id))
        self.assertFalse(self.registry.approve(proposal_id))

        active = {c["id"] for c in self.registry.categories()}
        self.assertNotIn("security", active)

    def test_approve_activates_category(self):
        self.registry.propose(
            "travel", "Travel", "Travel reservations"
        )
        proposal_id = self.registry.proposals()[0]["id"]

        self.assertTrue(self.registry.approve(proposal_id))
        self.assertFalse(self.registry.reject(proposal_id))

        active = {c["id"] for c in self.registry.categories()}
        self.assertIn("travel", active)


if __name__ == "__main__":
    unittest.main()
