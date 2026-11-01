import tempfile
import unittest
from pathlib import Path

from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


class TestMessageOrdering(unittest.TestCase):
    def test_newest_email_first_regardless_of_insert_order(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteMessageRepository(
                Path(directory) / "emails.db"
            )
            repo.initialize()

            repo.save(EmailMessage(
                "new", "personal", "A", "Newest", 2000
            ))
            repo.save(EmailMessage(
                "old", "personal", "B", "Oldest", 1000
            ))

            messages = repo.list_by_account("personal")

            self.assertEqual(
                [message.id for message in messages],
                ["new", "old"],
            )

    def test_existing_timestamp_survives_metadata_only_update(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteMessageRepository(
                Path(directory) / "emails.db"
            )
            repo.initialize()

            repo.save(EmailMessage(
                "one", "personal", "A", "Original", 2000
            ))
            repo.save(EmailMessage(
                "one", "personal", "A", "Updated"
            ))

            message = repo.list_by_account("personal")[0]

            self.assertEqual(message.internal_date_ms, 2000)
            self.assertEqual(message.subject, "Updated")


if __name__ == "__main__":
    unittest.main()
