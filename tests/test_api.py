import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

# Importing the production ASGI app initializes storage. Isolate that import
# as well as each test repository; never migrate the user's real databases.
import os
from unittest.mock import patch
with tempfile.TemporaryDirectory() as import_root:
    with patch.dict(os.environ, {"EMAIL_AGENT_PROJECT_ROOT": import_root}):
        from email_agent.api.app import create_app
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


class TestEmailAPI(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

        repository = SQLiteMessageRepository(
            Path(self.temp.name) / "test.db"
        )
        repository.initialize()

        repository.save(
            EmailMessage(
                id="1",
                account_id="personal",
                sender="test@example.com",
                subject="Electricity Invoice",
            )
        )

        self.client = TestClient(create_app(repository))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_health(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["status"],
            "ok",
        )

    def test_accounts(self):
        response = self.client.get("/api/accounts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_personal_messages(self):
        response = self.client.get(
            "/api/messages",
            params={"account_id": "personal"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(
            response.json()[0]["subject"],
            "Electricity Invoice",
        )

    def test_university_is_empty(self):
        response = self.client.get(
            "/api/messages",
            params={"account_id": "university"},
        )

        self.assertEqual(response.json(), [])

    def test_unknown_account_rejected(self):
        response = self.client.get(
            "/api/messages",
            params={"account_id": "invalid"},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
