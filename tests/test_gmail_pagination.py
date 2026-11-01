import unittest
from unittest.mock import MagicMock

from email_agent.infrastructure.gmail.client import GmailClient


class GmailPaginationTests(unittest.TestCase):

    def setUp(self):
        self.client = GmailClient.__new__(GmailClient)
        self.client._service = MagicMock()
        self.client._gate = MagicMock()

    def test_first_page_includes_spam_and_trash(self):
        service = self.client._service

        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "message-1"}],
            "nextPageToken": "next-token",
        }

        messages, next_token = self.client.list_message_page()

        self.assertEqual(messages, [{"id": "message-1"}])
        self.assertEqual(next_token, "next-token")

        parameters = (
            service.users.return_value
            .messages.return_value
            .list.call_args.kwargs
        )

        self.assertTrue(parameters["includeSpamTrash"])
        self.assertNotIn("labelIds", parameters)
        self.assertNotIn("pageToken", parameters)

    def test_next_page_uses_checkpoint(self):
        service = self.client._service
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [],
        }

        messages, next_token = self.client.list_message_page(
            page_token="saved-token",
            page_size=500,
        )

        self.assertEqual(messages, [])
        self.assertIsNone(next_token)

        parameters = (
            service.users.return_value
            .messages.return_value
            .list.call_args.kwargs
        )

        self.assertEqual(parameters["pageToken"], "saved-token")
        self.assertEqual(parameters["maxResults"], 500)

    def test_invalid_page_size_rejected(self):
        with self.assertRaises(ValueError):
            self.client.list_message_page(page_size=501)


if __name__ == "__main__":
    unittest.main()
