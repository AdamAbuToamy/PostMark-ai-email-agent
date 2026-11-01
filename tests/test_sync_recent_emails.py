import unittest
from email_agent.application.sync_recent_emails import SyncRecentEmails
from email_agent.domain.message import EmailMessage


class FakeReader:
    def list_recent(self, account_id, limit=10):
        return [
            EmailMessage("1", account_id, "a@example.com", "Test")
        ][:limit]


class FakeRepository:
    def __init__(self):
        self.saved = []

    def save(self, message):
        self.saved.append(message)


class TestSyncRecentEmails(unittest.TestCase):
    def test_sync(self):
        repository = FakeRepository()
        use_case = SyncRecentEmails(FakeReader(), repository)

        result = use_case.execute("personal")

        self.assertEqual(len(result), 1)
        self.assertEqual(len(repository.saved), 1)

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            SyncRecentEmails(FakeReader(), FakeRepository()).execute(
                "personal", 0
            )

    def test_rejects_account_mismatch(self):
        class WrongAccountReader:
            def list_recent(self, account_id, limit=10):
                return [
                    EmailMessage("1", "university", "a", "Wrong")
                ]

        repository = FakeRepository()

        with self.assertRaises(ValueError):
            SyncRecentEmails(
                WrongAccountReader(), repository
            ).execute("personal")

        self.assertEqual(repository.saved, [])
