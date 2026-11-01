import tempfile
import unittest
from pathlib import Path
import sqlite3
import json

from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


class MessageLabelTests(unittest.TestCase):
    def test_labels_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.db"
            repository = SQLiteMessageRepository(path)
            repository.initialize()

            repository.save(EmailMessage(
                id="message-1",
                account_id="personal",
                sender="sender@example.com",
                subject="Security alert",
                internal_date_ms=123,
                label_ids=("SPAM", "UNREAD"),
            ))

            with sqlite3.connect(path) as db:
                raw = db.execute(
                    "SELECT label_ids FROM messages "
                    "WHERE account_id = ? AND message_id = ?",
                    ("personal", "message-1"),
                ).fetchone()[0]

            self.assertEqual(
                json.loads(raw),
                ["SPAM", "UNREAD"],
            )

    def test_labels_update_on_resync(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.db"
            repository = SQLiteMessageRepository(path)
            repository.initialize()

            for labels in [("SPAM",), ("INBOX",)]:
                repository.save(EmailMessage(
                    id="message-1",
                    account_id="personal",
                    sender="sender@example.com",
                    subject="Security alert",
                    label_ids=labels,
                ))

            with sqlite3.connect(path) as db:
                raw = db.execute(
                    "SELECT label_ids FROM messages "
                    "WHERE message_id = ?",
                    ("message-1",),
                ).fetchone()[0]

            self.assertEqual(json.loads(raw), ["INBOX"])


if __name__ == "__main__":
    unittest.main()
