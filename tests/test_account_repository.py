import tempfile
import unittest
from pathlib import Path

from email_agent.domain.account import EmailAccount
from email_agent.infrastructure.database.account_repository import (
    SQLiteAccountRepository,
)


class TestSQLiteAccountRepository(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temp_dir.name) / "test.db"
        )

        self.repository = SQLiteAccountRepository(
            self.database_path
        )
        self.repository.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_account(self):
        account = EmailAccount(
            id="personal",
            provider="gmail",
        )

        self.repository.save(account)

        self.assertEqual(
            self.repository.get("personal"),
            account,
        )

    def test_unknown_account_returns_none(self):
        self.assertIsNone(
            self.repository.get("unknown")
        )

    def test_upsert_account(self):
        self.repository.save(
            EmailAccount("personal", "gmail")
        )

        self.repository.save(
            EmailAccount("personal", "google_workspace")
        )

        result = self.repository.get("personal")

        self.assertEqual(
            result.provider,
            "google_workspace",
        )


if __name__ == "__main__":
    unittest.main()
