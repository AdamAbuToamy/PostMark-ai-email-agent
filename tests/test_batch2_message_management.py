import base64
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from email_agent.api.message_management import register_message_management_routes
from email_agent.application.message_management import MessageManagement, order_notice
from email_agent.domain.message import EmailMessage
from email_agent.domain.message_category import MessageCategoryAssignment
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository
from email_agent.intelligence import build_classifier


class MessageManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.messages = SQLiteMessageRepository(self.root / "mail.db")
        self.messages.initialize()
        self.classifier = build_classifier(self.messages)
        self.store = self.classifier.assignments
        self.service = MessageManagement(self.messages, self.store, self.classifier.registry, self.classifier)
        self.headers = {"X-Email-Agent-Action": "message-management", "Origin": "http://localhost:8000"}
        for account in ("personal", "university"):
            for id, subject, date in (("same-id", "Your order has shipped", 300), ("older", "Order confirmation 20%_", 100)):
                message = EmailMessage(id, account, "AliExpress <orders@example.com>", subject, date, ("INBOX",))
                self.messages.save(message)
                self.classifier.classify(message)
        self.app = FastAPI()
        register_message_management_routes(self.app, self.messages, self.root / "categories.db")
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.addCleanup(self.client.close)

    def test_defaults_and_status_survive_reopening(self):
        self.assertEqual(self.service.mailbox("personal", "orders")["counts"], {"new": 2, "done": 0, "all": 2})
        self.service.set_status("personal", "same-id", "done")
        reopened = SQLiteMessageCategoryRepository(self.root / "mail.db")
        reopened.initialize()
        self.assertEqual(reopened.message_details("personal", "same-id")["local_state"], "done")
        self.service.set_status("personal", "same-id", "new")
        self.assertEqual(reopened.message_details("personal", "same-id")["local_state"], "new")

    def test_idempotent_new_done_audit_and_invalid_state(self):
        for _ in range(2):
            self.service.set_status("personal", "same-id", "done")
        with closing(sqlite3.connect(self.root / "mail.db")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM local_message_state_audit").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.service.set_status("personal", "same-id", "read")

    def test_account_isolation_with_same_message_id(self):
        self.service.set_status("personal", "same-id", "done")
        self.assertEqual(self.service.mailbox("university", "orders")["counts"]["done"], 0)
        self.service.change_category("personal", "same-id", "bills")
        self.assertEqual(self.service.mailbox("university", "orders")["counts"]["all"], 2)
        self.assertEqual(self.service.mailbox("personal", "orders")["counts"]["all"], 1)

    def test_move_preserves_done_and_manual_override(self):
        self.service.set_status("personal", "same-id", "done")
        self.service.change_category("personal", "same-id", "bills")
        self.classifier.run("personal")
        self.assertEqual(self.service.mailbox("personal", "bills", "done")["items"][0]["message_id"], "same-id")
        self.assertEqual(self.service.details("personal", "same-id")["source"], "manual")
        self.assertEqual(self.service.mailbox("personal", "orders", "all")["total"], 1)

    def test_only_active_categories_can_receive_messages(self):
        self.classifier.registry.propose("security", "Security", "Security notices")
        self.assertNotIn("security", {c["id"] for c in self.service.categories("personal")})
        with self.assertRaises(ValueError):
            self.service.change_category("personal", "same-id", "security")
        self.classifier.registry.approve(self.classifier.registry.proposals()[0]["id"])
        self.service.change_category("personal", "same-id", "security")
        active = next(c for c in self.service.categories("personal") if c["id"] == "security")
        self.assertEqual(active["counts"], {"new": 1, "done": 0, "all": 1})
        self.assertEqual(active["icon_key"], "shield")
        self.assertEqual(next(c for c in self.service.categories("university") if c["id"] == "security")["counts"]["all"], 0)

    def test_submitted_application_rule_still_applies(self):
        with self.assertRaises(ValueError):
            self.service.change_category("personal", "same-id", "job_applications")
        self.service.change_category("personal", "same-id", "job_applications", "Employer / role / submitted date")
        self.assertEqual(self.service.details("personal", "same-id")["category_id"], "job_applications")

    def test_search_sort_and_bounded_pagination(self):
        result = self.service.mailbox("personal", "orders", "all", "", "newest", 0, 1)
        self.assertEqual(result["items"][0]["message_id"], "same-id")
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(self.service.mailbox("personal", "orders", "all", "", "newest", 1, 1)["items"][0]["message_id"], "older")
        self.assertEqual(self.service.mailbox("personal", "orders", "all", "", "oldest")["items"][0]["message_id"], "older")
        self.assertEqual(self.service.mailbox("personal", "orders", search="20%_")["total"], 1)
        self.assertEqual(self.service.mailbox("personal", "orders", search="ALIEXPRESS")["total"], 2)
        self.assertEqual(self.service.mailbox("personal", "orders", search="no match")["total"], 0)

    def test_tab_counts_ignore_search_and_status_filter(self):
        self.service.set_status("personal", "same-id", "done")
        result = self.service.mailbox("personal", "orders", "new", "20%_")
        self.assertEqual(result["counts"], {"new": 1, "done": 1, "all": 2})
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.service.mailbox("personal", "orders", "done")["items"][0]["message_id"], "same-id")

    def test_promotions_are_not_mixed_by_sender(self):
        message = EmailMessage("sale", "personal", "AliExpress", "Huge sale - shop now")
        self.messages.save(message)
        self.classifier.classify(message)
        self.assertEqual(self.service.mailbox("personal", "orders")["counts"]["all"], 2)
        self.assertEqual(self.service.mailbox("personal", "promotions")["counts"]["all"], 1)

    def test_order_notices_require_explicit_positive_evidence(self):
        self.assertEqual(order_notice("Your order has shipped"), "Shipping confirmation")
        self.assertEqual(order_notice("Your package was delivered"), "Delivery confirmation")
        for subject in ("Shipping update", "Order confirmation", "Your order has not shipped", "Delivery delayed", "Has your order shipped?", "Your package will be delivered tomorrow", "Your order is ready to be shipped", "Shipping confirmation cancelled"):
            self.assertIsNone(order_notice(subject))

    def test_invalid_and_unknown_inputs(self):
        for kwargs in ({"category": "unknown"}, {"category": "orders", "state": "bad"}, {"category": "orders", "sort": "bad"}, {"category": "orders", "limit": 101}):
            with self.assertRaises(ValueError):
                self.service.mailbox("personal", **kwargs)
        with self.assertRaises(LookupError):
            self.service.set_status("personal", "missing", "done")
        with self.assertRaises(ValueError):
            self.service.categories("unknown")

    def test_api_mutations_and_details(self):
        response = self.client.patch("/api/local-messages/personal/same-id/status", headers=self.headers, json={"state": "done"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/local-messages/personal/same-id").json()["local_state"], "done")
        response = self.client.patch("/api/local-messages/personal/same-id/category", headers=self.headers, json={"category_id": "bills"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/category-mailbox/personal/bills?state=done").json()["total"], 1)
        self.assertEqual(self.client.get("/api/category-mailbox/personal/categories").status_code, 200)

    def test_mutation_guards_and_failed_moves_leave_state_intact(self):
        path = "/api/local-messages/personal/same-id/category"
        self.assertEqual(self.client.patch(path, json={"category_id": "bills"}).status_code, 403)
        self.assertEqual(self.client.patch(path, headers={**self.headers, "Origin": "https://evil.invalid"}, json={"category_id": "bills"}).status_code, 403)
        with TestClient(self.app, client=("192.168.1.20", 50000)) as remote:
            self.assertEqual(remote.patch(path, headers=self.headers, json={"category_id": "bills"}).status_code, 403)
        self.assertEqual(self.client.patch(path, headers=self.headers, json={"category_id": "unknown"}).status_code, 400)
        self.assertEqual(self.client.get("/api/local-messages/personal/same-id").json()["category_id"], "orders")
        self.assertEqual(self.client.patch("/api/local-messages/personal/missing/status", headers=self.headers, json={"state": "done"}).status_code, 404)

    def test_unclassified_details_keep_account_identity(self):
        self.messages.save(EmailMessage("unclassified", "personal", "s", "s"))
        self.service.set_status("personal", "unclassified", "done")
        details = self.service.details("personal", "unclassified")
        self.assertEqual((details["account_id"], details["message_id"], details["local_state"]), ("personal", "unclassified", "done"))

    def test_additive_migration_preserves_existing_assignments(self):
        path = self.root / "legacy.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE message_category_assignments(account_id TEXT,message_id TEXT,category_id TEXT,source TEXT,PRIMARY KEY(account_id,message_id))")
            db.execute("INSERT INTO message_category_assignments VALUES ('personal','legacy','orders','manual')")
        migrated = SQLiteMessageCategoryRepository(path)
        migrated.initialize(); migrated.initialize()
        self.assertEqual(migrated.list_for_account("personal"), {"legacy": "orders"})
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM local_message_state").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT MAX(version) FROM classification_schema_versions").fetchone()[0], 6)

    def test_full_message_navigation_uses_real_account_aware_reader(self):
        # test_api isolates the ASGI module import from production databases.
        from test_api import create_app
        client = TestClient(create_app(self.messages))
        self.addCleanup(client.close)
        encoded = base64.urlsafe_b64encode(b"Real reader fixture body").decode()
        with patch("email_agent.infrastructure.gmail.auth.GmailAuthenticator") as auth, patch("email_agent.infrastructure.gmail.client.GmailClient") as gmail:
            gmail.return_value.get_full_message.return_value = {"id": "same-id", "payload": {"mimeType": "text/plain", "body": {"data": encoded}, "headers": []}}
            response = client.get("/api/messages/university/same-id/content")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["body"], "Real reader fixture body")
            self.assertEqual(auth.call_args.kwargs["token_path"].name, "university.json")
            gmail.return_value.get_full_message.assert_called_once_with("same-id")
            gmail.reset_mock()
            self.assertEqual(client.get("/api/messages/personal/not-owned/content").status_code, 404)
            gmail.assert_not_called()

    def test_category_approval_rejection_and_navigation_listing_contract(self):
        from email_agent.api.categories import register_category_routes
        from email_agent.application.category_registry import CategoryRegistry
        from email_agent.infrastructure.database.sqlite_category_repository import SQLiteCategoryRepository
        project = self.root / "review-project"
        registry = CategoryRegistry(SQLiteCategoryRepository(project / "data" / "categories.db"))
        registry.propose("travel", "Travel", "Travel bookings")
        registry.propose("security", "Security", "Security messages")
        proposal_ids = {p["category_id"]: p["id"] for p in registry.proposals()}
        app = FastAPI()
        register_category_routes(app, project)
        with TestClient(app) as client:
            headers = {"X-Email-Agent-Action": "category-review", "Origin": "http://localhost:8000"}
            response = client.post(f"/api/categories/proposals/{proposal_ids['travel']}/approve", headers=headers)
            self.assertEqual(response.json()["status"], "approved")
            self.assertIn("travel", {c["id"] for c in client.get("/api/categories").json()})
            response = client.post(f"/api/categories/proposals/{proposal_ids['security']}/reject", headers=headers)
            self.assertEqual(response.json()["status"], "rejected")
            self.assertNotIn("security", {c["id"] for c in client.get("/api/categories").json()})
            self.assertEqual(client.post(f"/api/categories/proposals/{proposal_ids['security']}/approve", headers=headers).status_code, 409)
