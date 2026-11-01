import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import httplib2
from googleapiclient.errors import HttpError

from email_agent.domain.message import EmailMessage
from email_agent.application.historical_sync import HistoricalSync
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.infrastructure.gmail.client import GmailClient
from email_agent.infrastructure.gmail.reader import GmailEmailReader
from email_agent.infrastructure.gmail.request_policy import RequestGate, execute, sync_lock


def error(status, reason="backendError", retry_after=None):
    headers = {"status": str(status)}
    if retry_after:
        headers["retry-after"] = retry_after
    return HttpError(httplib2.Response(headers), json.dumps({"error": {"errors": [{"reason": reason}]}}).encode())


def message(id="1", account="personal", labels=None):
    return EmailMessage(id, account, "shop@example.com", "Your order shipped", 12, labels)


class HistoricalSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mail.db"
        self.store = SQLiteHistoryStore(self.path)
        self.messages = SQLiteMessageRepository(self.path)
        self.reader = Mock()
        self.sync = HistoricalSync(self.reader, self.store)

    def test_pagination_and_completed_resume_no_network(self):
        self.reader.list_history_page.side_effect = [([message()], "next"), ([message("2")], None)]
        state = self.sync.execute("personal", 2)
        self.assertEqual(state["pages_completed"], 2)
        self.assertEqual(state["messages_processed"], 2)
        self.assertEqual(state["completed"], 1)
        self.sync.execute("personal")
        self.assertEqual(self.reader.list_history_page.call_count, 2)
        self.reader.list_history_page.assert_called_with("personal", "next", 50)

    def test_interrupted_fetch_resumes_last_committed_page(self):
        self.reader.list_history_page.side_effect = [([message()], "next"), TimeoutError()]
        with self.assertRaises(TimeoutError):
            self.sync.execute("personal", 2)
        self.assertEqual(self.store.progress("personal")["next_page_token"], "next")
        self.reader.list_history_page.side_effect = [([message("2")], None)]
        self.sync.execute("personal")
        self.assertEqual(self.messages.count_by_account("personal"), 2)

    def test_failed_commit_rolls_back_entire_page_and_checkpoint(self):
        original = SQLiteMessageRepository.save_in_transaction
        calls = []
        def fail_second(db, msg):
            calls.append(msg.id)
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk full")
            original(db, msg)
        self.reader.list_history_page.return_value = ([message(), message("2")], "next")
        with patch.object(SQLiteMessageRepository, "save_in_transaction", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.sync.execute("personal")
        self.assertEqual(self.messages.count_by_account("personal"), 0)
        self.assertEqual(self.store.progress("personal")["pages_completed"], 0)
        self.sync.execute("personal")
        self.assertEqual(self.messages.count_by_account("personal"), 2)

    def test_stale_writer_and_account_mismatch_rejected(self):
        self.store.commit_page("personal", None, [message()], "next")
        with self.assertRaises(RuntimeError):
            self.store.commit_page("personal", None, [message("2")], None)
        self.reader.list_history_page.return_value = ([message(account="university")], None)
        with self.assertRaises(ValueError):
            self.sync.execute("personal")
        self.assertEqual(self.messages.count_by_account("university"), 0)
        self.assertEqual(self.store.progress("university")["pages_completed"], 0)

    def test_explicit_recheck_refreshes_empty_labels_without_deletion(self):
        self.reader.list_history_page.return_value = ([message(labels=("SPAM",))], None)
        self.sync.execute("personal")
        self.store.restart("personal")
        self.reader.list_history_page.return_value = ([message(labels=())], None)
        self.sync.execute("personal")
        self.assertEqual(self.messages.get("personal", "1").label_ids, ())
        self.messages.save(message(labels=None))
        self.assertEqual(self.messages.get("personal", "1").label_ids, ())

    def test_legacy_checkpoint_import_is_idempotent(self):
        legacy = Path(self.temp.name) / "old.db"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute("CREATE TABLE history_progress(account_id,next_page_token,pages_completed,messages_processed,completed)")
            db.execute("INSERT INTO history_progress VALUES ('personal','old',2,100,0)")
        self.store.import_legacy("personal", legacy)
        self.store.commit_page("personal", "old", [message()], "new")
        self.store.import_legacy("personal", legacy)
        self.assertEqual(self.store.progress("personal")["next_page_token"], "new")
        self.assertEqual(self.store.progress("personal")["messages_processed"], 101)

    def test_legacy_unknown_labels_migration_preserves_messages(self):
        path = Path(self.temp.name) / "legacy.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE messages(account_id TEXT,message_id TEXT,sender TEXT,subject TEXT,label_ids TEXT,PRIMARY KEY(account_id,message_id))")
            db.executemany("INSERT INTO messages VALUES ('personal',?,'s','s',?)", [("1", "[]"), ("2", '["SPAM"]')])
        repo = SQLiteMessageRepository(path)
        repo.initialize()
        repo.initialize()
        self.assertIsNone(repo.get("personal", "1").label_ids)
        self.assertEqual(repo.get("personal", "2").label_ids, ("SPAM",))

    def test_nonadvancing_token_and_invalid_bounds_fail(self):
        self.store.commit_page("personal", None, [], "same")
        self.reader.list_history_page.return_value = ([], "same")
        with self.assertRaises(ValueError):
            self.sync.execute("personal")
        for kwargs in ({"max_pages": 0}, {"page_size": 501}):
            with self.assertRaises(ValueError):
                self.sync.execute("personal", **kwargs)

    def test_account_lock_releases_after_interruption(self):
        directory = Path(self.temp.name)
        with sync_lock("personal", directory):
            with self.assertRaises(RuntimeError):
                with sync_lock("personal", directory):
                    pass
            with sync_lock("university", directory):
                pass
        with sync_lock("personal", directory):
            pass


class GmailRequestPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.delays = []
        def sleep(delay):
            self.delays.append(delay)
            self.now += delay
        self.gate = RequestGate(Path(self.temp.name) / "gate", lambda: self.now, sleep)

    def test_two_clients_share_spacing(self):
        second = RequestGate(self.gate.path, self.gate.clock, self.gate.sleep)
        request = Mock()
        execute(request, self.gate)
        execute(request, second)
        self.assertAlmostEqual(self.delays[-1], .7)
        request.execute.assert_called_with(num_retries=0)

    def test_transient_quota_and_timeout_retry(self):
        for exc in (error(429), error(403, "userRateLimitExceeded"), error(503), TimeoutError()):
            with self.subTest(type=type(exc).__name__):
                request = Mock()
                request.execute.side_effect = [exc, {"ok": True}]
                self.assertEqual(execute(request, self.gate), {"ok": True})
                self.assertEqual(request.execute.call_count, 2)
                self.assertGreaterEqual(self.delays[-1], 2)

    def test_permanent_permission_error_not_retried(self):
        request = Mock()
        request.execute.side_effect = error(403, "forbidden")
        with self.assertRaises(HttpError):
            execute(request, self.gate)
        self.assertEqual(request.execute.call_count, 1)

    def test_retries_bounded_and_cooldown_persisted(self):
        request = Mock()
        request.execute.side_effect = error(429)
        with self.assertRaises(HttpError):
            execute(request, self.gate)
        self.assertEqual(request.execute.call_count, 6)
        self.assertGreater(float(self.gate.path.read_text()), self.now)

    def test_long_retry_after_fails_closed_without_extra_request(self):
        request = Mock()
        request.execute.side_effect = error(429, retry_after="3600")
        with self.assertRaises(HttpError):
            execute(request, self.gate)
        with self.assertRaises(TimeoutError):
            execute(request, self.gate)
        self.assertEqual(request.execute.call_count, 1)

    def test_pagination_uses_retry_policy_and_preserves_token(self):
        client = GmailClient.__new__(GmailClient)
        client._service = MagicMock()
        client._gate = self.gate
        request = client._service.users.return_value.messages.return_value.list.return_value
        request.execute.side_effect = [error(500), {"messages": [{"id": "1"}], "nextPageToken": "next"}]
        self.assertEqual(client.list_message_page("saved"), ([{"id": "1"}], "next"))
        self.assertEqual(request.execute.call_count, 2)

    def test_full_message_and_metadata_use_same_policy(self):
        client = GmailClient.__new__(GmailClient)
        client._service = MagicMock()
        client._gate = self.gate
        request = client._service.users.return_value.messages.return_value.get.return_value
        request.execute.return_value = {"id": "1"}
        self.assertEqual(client.get_message("1")["label_ids"], ())
        self.assertEqual(client.get_full_message("1"), {"id": "1"})
        self.assertAlmostEqual(self.delays[-1], .7)

    def test_reader_retries_whole_page_after_metadata_failure(self):
        client = Mock()
        client.list_message_page.return_value = ([{"id": "1"}, {"id": "2"}], "next")
        client.get_message.side_effect = [{"id": "1", "sender": "s", "subject": "s"}, TimeoutError()]
        reader = GmailEmailReader(client)
        with self.assertRaises(TimeoutError):
            reader.list_history_page("personal")

    def test_message_deleted_between_list_and_get_does_not_block_page(self):
        client = Mock()
        client.list_message_page.return_value = ([{"id": "gone"}], None)
        client.get_message.side_effect = error(404)
        self.assertEqual(GmailEmailReader(client).list_history_page("personal"), ([], None))

    def test_concurrent_clients_are_serialized(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        starts = []
        request = Mock()
        request.execute.side_effect = lambda **kwargs: starts.append(time.monotonic())
        path = Path(self.temp.name) / "real-gate"
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: execute(request, RequestGate(path)), range(2)))
        self.assertGreaterEqual(starts[1] - starts[0], 0.65)

    def test_transport_timeout_is_explicit(self):
        with patch("email_agent.infrastructure.gmail.client.build") as build, patch("email_agent.infrastructure.gmail.client.AuthorizedHttp") as authorized, patch("email_agent.infrastructure.gmail.client.httplib2.Http") as http:
            GmailClient(Mock())
            http.assert_called_once_with(timeout=30)
            self.assertFalse(build.call_args.kwargs["cache_discovery"])
            self.assertEqual(build.call_args.kwargs["http"], authorized.return_value)
