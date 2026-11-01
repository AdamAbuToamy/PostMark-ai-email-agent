import tempfile
import unittest
from pathlib import Path

from email_agent.domain.message_category import MessageCategoryAssignment
from email_agent.infrastructure.database.sqlite_message_category_repository import (
    SQLiteMessageCategoryRepository,
)


class MessageCategoryRepositoryTests(unittest.TestCase):
    def test_assignment_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteMessageCategoryRepository(
                Path(directory) / "assignments.db"
            )
            repository.initialize()

            assignment = MessageCategoryAssignment(
                account_id="personal",
                message_id="gmail-123",
                category_id="security",
                source="automatic",
            )

            self.assertTrue(repository.assign(assignment))

            self.assertEqual(
                repository.list_for_account("personal"),
                {"gmail-123": "security"},
            )

    def test_existing_assignment_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteMessageCategoryRepository(
                Path(directory) / "assignments.db"
            )
            repository.initialize()

            original = MessageCategoryAssignment(
                "personal", "gmail-123", "security", "manual"
            )

            conflicting = MessageCategoryAssignment(
                "personal", "gmail-123", "promotions", "automatic"
            )

            self.assertTrue(repository.assign(original))
            self.assertFalse(repository.assign(conflicting))

            self.assertEqual(
                repository.list_for_account("personal")["gmail-123"],
                "security",
            )

    def test_accounts_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteMessageCategoryRepository(
                Path(directory) / "assignments.db"
            )
            repository.initialize()

            repository.assign(
                MessageCategoryAssignment(
                    "personal", "same-id", "security", "automatic"
                )
            )

            self.assertEqual(
                repository.list_for_account("university"),
                {},
            )


if __name__ == "__main__":
    unittest.main()
