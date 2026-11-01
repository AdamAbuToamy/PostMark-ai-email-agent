import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from email_agent.api.analysis import register_analysis_routes, MODEL_NAME
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.ai.usage import reserve_call
from email_agent.workers import analysis_worker


class AIControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = SQLiteMessageRepository(self.root / "data" / "email_agent.db")
        self.repo.initialize()
        self.repo.save(EmailMessage("1", "personal", "s", "hello"))
        app = FastAPI()
        register_analysis_routes(app, self.repo, self.root)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"origin": "http://localhost:8000"}
        self.result = dict(category="other", priority="low", summary="summary", action="", index=0)

    def cache(self, raw):
        with closing(sqlite3.connect(self.root / "data" / "analysis_cache.db")) as db, db:
            db.execute("INSERT INTO analyses VALUES (?,?,?,?)", ("personal", "1", MODEL_NAME, raw))

    def test_no_opt_in_never_calls_model_or_gmail(self):
        with patch("email_agent.api.analysis.GeminiTextModel") as model, patch("email_agent.main.build_reader") as gmail:
            response = self.client.post("/api/analyze/personal", headers=self.headers, json={})
            self.assertEqual(response.status_code, 400)
            model.assert_not_called()
            gmail.assert_not_called()

    def test_cache_reused_without_paid_requests(self):
        self.cache(json.dumps(self.result))
        with patch("email_agent.api.analysis.GeminiTextModel") as model, patch("email_agent.main.build_reader") as gmail:
            response = self.client.post("/api/analyze/personal", headers=self.headers, json={"approved": True})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["cached"], 1)
            model.assert_not_called()
            gmail.assert_not_called()

    def test_invalid_cache_fails_closed_without_overwriting(self):
        self.cache('{"category":"invented"}')
        with patch("email_agent.api.analysis.GeminiTextModel") as model:
            self.assertEqual(self.client.post("/api/analyze/personal", headers=self.headers, json={"approved": True}).status_code, 409)
            model.assert_not_called()

    def test_api_and_worker_share_daily_cap(self):
        ledger = self.root / "data" / "ai_usage.db"
        with patch.object(analysis_worker, "LEDGER_DB", ledger):
            for _ in range(3):
                self.assertTrue(analysis_worker.reserve_ai_call("personal", "1"))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-test-key"}), patch("email_agent.api.analysis.GeminiTextModel") as model, patch("email_agent.main.build_reader") as gmail:
            response = self.client.post("/api/analyze/personal", headers=self.headers, json={"approved": True})
            self.assertEqual(response.status_code, 429)
            model.assert_not_called()
            gmail.assert_not_called()

    def test_concurrent_reservations_never_exceed_cap(self):
        ledger = self.root / "usage.db"
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda n: reserve_call(ledger, "personal", str(n)), range(12)))
        self.assertEqual(sum(results), 3)

    def test_approved_mock_call_cached_and_not_repeated(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-test-key"}), patch("email_agent.api.analysis.GeminiTextModel") as model, patch("email_agent.main.build_reader"), patch("email_agent.api.analysis.GmailContentReader") as content:
            model.return_value.generate.return_value = json.dumps({"items": [self.result]})
            content.return_value.get_content.return_value = dict(sender="s", subject="s", body="untrusted")
            for _ in range(2):
                response = self.client.post("/api/analyze/personal", headers=self.headers, json={"approved": True})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(model.return_value.generate.call_count, 1)

    def test_worker_cached_event_is_idempotent(self):
        cache = self.root / "data" / "analysis_cache.db"
        state = self.root / "state.db"
        self.cache(json.dumps(self.result))
        with closing(sqlite3.connect(state)) as db, db:
            db.execute("CREATE TABLE inbox_events(account_id,message_id,status)")
            db.execute("INSERT INTO inbox_events VALUES ('personal','1','pending')")
        with patch.object(analysis_worker, "CACHE_DB", cache), patch.object(analysis_worker, "STATE_DB", state), patch.object(analysis_worker, "GeminiTextModel") as model, patch.object(analysis_worker, "build_reader") as gmail:
            analysis_worker.process_approved_batch([("personal", "1")])
            analysis_worker.process_approved_batch([("personal", "1")])
            model.assert_not_called()
            gmail.assert_not_called()
        with closing(sqlite3.connect(state)) as db:
            self.assertEqual(db.execute("SELECT status FROM inbox_events").fetchone()[0], "completed")
