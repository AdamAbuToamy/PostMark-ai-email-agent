import unittest

from email_agent.application.list_recent_emails import ListRecentEmails
from email_agent.domain.message import EmailMessage


class FakeEmailReader:
    def list_recent(self, account_id, limit=10):
        return [
            EmailMessage(
                id="msg-1",
                account_id=account_id,
                sender="test@example.com",
                subject="Test message",
            )
        ][:limit]


class TestListRecentEmails(unittest.TestCase):

    def setUp(self):
        self.use_case = ListRecentEmails(FakeEmailReader())

    def test_returns_email_messages(self):
        messages = self.use_case.execute("personal")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].account_id, "personal")
        self.assertEqual(messages[0].subject, "Test message")

    def test_rejects_invalid_limit(self):
        with self.assertRaises(ValueError):
            self.use_case.execute("personal", limit=0)

    def test_supports_university_account(self):
        messages = self.use_case.execute("university")

        self.assertTrue(messages[0].is_from_university)


if __name__ == "__main__":
    unittest.main()
