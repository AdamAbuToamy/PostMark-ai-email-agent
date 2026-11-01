import base64
import unittest

from email_agent.infrastructure.gmail.content_reader import (
    GmailContentReader,
)


def encode(text):
    return base64.urlsafe_b64encode(
        text.encode("utf-8")
    ).decode("ascii")


class FakeClient:
    def __init__(self, mime, content):
        self.mime = mime
        self.content = content

    def get_full_message(self, message_id):
        return {
            "id": message_id,
            "payload": {
                "mimeType": self.mime,
                "headers": [
                    {"name": "Subject", "value": "Test invoice"},
                    {"name": "From", "value": "sender@example.com"},
                ],
                "body": {
                    "data": encode(self.content),
                },
            },
        }


class TestGmailContentReader(unittest.TestCase):
    def test_plain_text(self):
        reader = GmailContentReader(
            FakeClient("text/plain", "Hello from Gmail")
        )

        result = reader.get_content("msg-1")

        self.assertEqual(result["body"], "Hello from Gmail")
        self.assertEqual(result["subject"], "Test invoice")

    def test_html_to_plain_text(self):
        reader = GmailContentReader(
            FakeClient(
                "text/html",
                "<p>Invoice 123</p><script>danger()</script>",
            )
        )

        result = reader.get_content("msg-2")

        self.assertIn("Invoice 123", result["body"])
        self.assertNotIn("danger()", result["body"])


if __name__ == "__main__":
    unittest.main()
