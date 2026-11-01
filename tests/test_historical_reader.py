import unittest
from unittest.mock import Mock

from email_agent.infrastructure.gmail.reader import GmailEmailReader


class HistoricalReaderTests(unittest.TestCase):
    def test_reads_messages_and_next_token(self):
        client = Mock()
        client.list_message_page.return_value = (
            [{"id": "gmail-1"}],
            "next-page",
        )
        client.get_message.return_value = {
            "id": "gmail-1",
            "sender": "sender@example.com",
            "subject": "Test",
            "internal_date_ms": 123,
        }

        reader = GmailEmailReader(client)
        messages, token = reader.list_history_page(
            account_id="personal",
            page_size=50,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].account_id, "personal")
        self.assertEqual(token, "next-page")

        client.list_message_page.assert_called_once_with(
            page_token=None,
            page_size=50,
            include_spam_trash=True,
        )

    def test_rejects_unknown_account(self):
        reader = GmailEmailReader(Mock())

        with self.assertRaises(ValueError):
            reader.list_history_page("unknown")
