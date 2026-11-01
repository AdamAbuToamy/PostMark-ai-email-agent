import tempfile
import unittest
from pathlib import Path

from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


class TestMessageRepository(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SQLiteMessageRepository(
            Path(self.temp.name) / "test.db"
        )
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_save_and_read(self):
        message = EmailMessage(
            "1", "personal", "test@example.com", "Hello"
        )
        self.repo.save(message)
        self.assertEqual(
            self.repo.list_by_account("personal")[0], message
        )

    def test_no_duplicates(self):
        message = EmailMessage(
            "1", "personal", "test@example.com", "Hello"
        )
        self.repo.save(message)
        self.repo.save(message)
        self.assertEqual(self.repo.count_by_account("personal"), 1)

    def test_account_isolation(self):
        self.repo.save(EmailMessage("1", "personal", "a", "A"))
        self.repo.save(EmailMessage("1", "university", "b", "B"))

        self.assertEqual(self.repo.count_by_account("personal"), 1)
        self.assertEqual(self.repo.count_by_account("university"), 1)
        self.assertEqual(
            self.repo.list_by_account("university")[0].subject,
            "B",
        )
