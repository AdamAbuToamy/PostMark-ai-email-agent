import tempfile
import unittest
from pathlib import Path

from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


class MessageLabelsRoundTripTests(unittest.TestCase):
    def test_labels_survive_read(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteMessageRepository(Path(directory) / "mail.db")
            repo.initialize()

            repo.save(EmailMessage(
                id="gmail-123",
                account_id="personal",
                sender="security@example.com",
                subject="Account warning",
                label_ids=("SPAM", "UNREAD"),
            ))

            message = repo.list_by_account("personal")[0]

            self.assertEqual(
                message.label_ids,
                ("SPAM", "UNREAD"),
            )

    def test_empty_legacy_update_preserves_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteMessageRepository(Path(directory) / "mail.db")
            repo.initialize()

            repo.save(EmailMessage(
                id="gmail-123",
                account_id="personal",
                sender="security@example.com",
                subject="Account warning",
                label_ids=("SPAM",),
            ))

            repo.save(EmailMessage(
                id="gmail-123",
                account_id="personal",
                sender="security@example.com",
                subject="Account warning",
            ))

            message = repo.list_by_account("personal")[0]

            self.assertEqual(message.label_ids, ("SPAM",))


if __name__ == "__main__":
    unittest.main()
