import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.intelligence import build_classifier
from email_agent.workers import inbox_worker, historical_sync


class WorkerIntegrationTests(unittest.TestCase):
    def test_incremental_worker_is_idempotent_and_classifies_without_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = SQLiteMessageRepository(root / "email_agent.db")
            repo.initialize()
            repo.save(EmailMessage('archived-untouched','personal','s','Payment receipt',label_ids=()))
            reader = Mock()
            reader.list_recent.return_value = [EmailMessage("1", "personal", "s", "Order confirmation", label_ids=("INBOX",))]
            with patch.object(inbox_worker, "STATE_PATH", root / "worker_state.db"), patch.object(inbox_worker, "build_reader", return_value=reader):
                inbox_worker.initialize_state()
                self.assertEqual(inbox_worker.sync_account("personal", repo), 0)
                reader.list_recent.return_value.append(EmailMessage("2", "personal", "s", "Hello"))
                self.assertEqual(inbox_worker.sync_account("personal", repo), 1)
                self.assertEqual(inbox_worker.sync_account("personal", repo), 0)
            counts = build_classifier(repo).assignments.progress("personal")
            self.assertEqual((counts["classified"], counts["needs_review"]), (1, 1))
            self.assertIsNone(build_classifier(repo).assignments.message_details('personal','archived-untouched')['category_id'])
            self.assertEqual(inbox_worker.DEFAULT_INTERVAL, 600)

    def test_historical_worker_classifies_committed_pages_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reader = Mock()
            reader.list_history_page.side_effect = [([EmailMessage("1", "personal", "s", "Order confirmation")], "next"), TimeoutError()]
            with patch.object(historical_sync, "MESSAGE_DB", root / "email_agent.db"), patch.object(historical_sync, "STATE_DB", root / "legacy.db"), patch.object(historical_sync, "build_reader", return_value=reader):
                with self.assertRaises(TimeoutError):
                    historical_sync.sync("personal", 2, 50)
                repo = SQLiteMessageRepository(root / "email_agent.db")
                self.assertEqual(build_classifier(repo).assignments.progress("personal")["classified"], 1)
                reader.list_history_page.side_effect = [([], None)]
                self.assertEqual(historical_sync.sync("personal")["completed"], 1)
                reader.reset_mock()
                historical_sync.sync("personal")
                reader.list_history_page.assert_not_called()
