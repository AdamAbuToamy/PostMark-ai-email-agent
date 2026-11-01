import unittest

from email_agent.application.list_recent_emails import ListRecentEmails
from email_agent.domain.message import EmailMessage
from email_agent.main import ACCOUNTS


class FakeEmailReader:
    """Fake reader that returns messages for the requested account."""

    def list_recent(self, account_id: str, limit: int = 10):
        return [
            EmailMessage(
                id="same-message-id",
                account_id=account_id,
                sender="sender@example.com",
                subject="Test email",
            )
        ][:limit]


class TestAccountIsolation(unittest.TestCase):

    def setUp(self):
        self.use_case = ListRecentEmails(FakeEmailReader())

    def test_accounts_have_different_token_paths(self):
        self.assertNotEqual(
            ACCOUNTS["personal"],
            ACCOUNTS["university"],
        )

    def test_personal_account_identity(self):
        messages = self.use_case.execute("personal")
        self.assertEqual(messages[0].account_id, "personal")

    def test_university_account_identity(self):
        messages = self.use_case.execute("university")
        self.assertEqual(messages[0].account_id, "university")

    def test_same_message_id_is_distinguished_by_account(self):
        personal = self.use_case.execute("personal")[0]
        university = self.use_case.execute("university")[0]

        self.assertNotEqual(
            (personal.account_id, personal.id),
            (university.account_id, university.id),
        )


if __name__ == "__main__":
    unittest.main()
