import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.intelligence import build_classifier
from email_agent.api.intelligence import register_intelligence_routes
from email_agent.application.sync_recent_emails import SyncRecentEmails


class LocalClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mail.db"
        self.messages = SQLiteMessageRepository(self.path)
        self.messages.initialize()
        self.service = build_classifier(self.messages)
        self.store = self.service.assignments

    def add(self, subject, id="1", labels=None, account="personal"):
        msg = EmailMessage(id, account, "AliExpress <orders@example.com>", subject, 1, labels)
        self.messages.save(msg)
        self.service.classify(msg)
        return self.store.items(account)[-1]

    def test_actual_orders_and_aliexpress_promotions(self):
        for subject in ("Order confirmation", "Your order 123 has shipped", "Your package is out for delivery", "Refund processed", "Thank you for your purchase", "Your order is confirmed"):
            with self.subTest(subject=subject):
                row = self.add(subject)
                self.assertEqual((row["category_id"], row["status"]), ("orders", "classified"))
        self.assertEqual(self.add("Payment received")["category_id"],"bills")
        for subject in ("AliExpress big sale", "Coupon for your next purchase", "Shop now", "Place your order now"):
            with self.subTest(subject=subject):
                self.assertEqual(self.add(subject)["category_id"], "promotions")
        self.assertEqual(self.add("Shipping confirmation - discount on your next purchase")["status"], "needs_review")
        self.assertEqual(self.add("Order update")["status"], "needs_review")

    def test_recruitment_ads_are_not_applications(self):
        self.assertEqual(self.add("We are hiring - apply now")["category_id"], "promotions")
        row = self.add("Thank you for applying - application received")
        self.assertEqual((row["category_id"], row["status"]), ("job_applications", "classified"))
        with self.assertRaises(ValueError):
            self.service.manual("personal", "1", "job_applications", "Reviewed")
        self.service.manual("personal", "1", "job_applications", "Matched application", "Employer / role / submitted 2026-10-01")
        self.assertEqual(self.store.items("personal")[0]["category_id"], "job_applications")

    def test_dynamic_categories_require_approval_and_manual_decisions_survive(self):
        self.add("Order confirmation")
        self.service.registry.propose("travel", "Travel", "Travel bookings")
        with self.assertRaises(ValueError):
            self.service.manual("personal", "1", "travel", "Reviewed")
        self.service.registry.approve(self.service.registry.proposals()[0]["id"])
        self.service.manual("personal", "1", "travel", "Actual trip booking")
        self.service.run("personal")
        self.assertEqual(self.store.items("personal")[0]["category_id"], "travel")
        self.service.manual("personal", "1", "orders", "Corrected decision")
        self.assertEqual(self.store.items("personal")[0]["category_id"], "orders")

    def test_invalid_categories_and_cross_account_override_fail(self):
        self.add("Order confirmation")
        for category in ("unknown", "../bad", "jobs"):
            with self.assertRaises(ValueError):
                self.service.manual("personal", "1", category, "Reviewed")
        with self.assertRaises(ValueError):
            self.service.manual("university", "1", "orders", "Reviewed")

    def test_reclassification_idempotent_with_audit(self):
        self.add("Order confirmation")
        self.service.run("personal")
        self.service.run("personal")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM classification_audit").fetchone()[0], 1)
        self.assertEqual(self.store.progress("personal")["classified"], 1)

    def test_progress_partitions_local_messages_and_never_infers_history_completion(self):
        self.add("Order confirmation", "1")
        self.add("Hello", "2")
        self.messages.save(EmailMessage("3", "personal", "s", "not processed"))
        self.add("Order confirmation", "1", account="university")
        counts = self.store.progress("personal")
        self.assertEqual((counts["classified"], counts["needs_review"], counts["unclassified"], counts["total_local"]), (1,1,1,3))
        self.assertEqual(self.store.progress("university")["total_local"], 1)

    def test_spam_review_never_claims_legitimacy_or_modifies_gmail(self):
        self.add("Order confirmation", labels=("SPAM", "IMPORTANT"))
        reviews = self.store.reviews("personal")
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["status"], "proposed")
        self.assertIn("verify sender", reviews[0]["reason"])
        self.service.run("personal")
        self.assertEqual(len(self.store.reviews("personal")), 1)
        self.add("Account alert", "2", ("SPAM", "IMPORTANT"))
        self.assertEqual(len(self.store.reviews("personal")), 1)
        self.assertEqual(self.store.reviews("university"), [])

    def test_label_updates_supersede_proposals_unknown_does_not(self):
        self.add("Order confirmation", labels=("TRASH",))
        self.add("Order confirmation", labels=None)
        self.assertEqual(self.store.reviews("personal")[0]["status"], "proposed")
        self.add("Order confirmation", labels=())
        self.assertEqual(self.store.reviews("personal")[0]["status"], "superseded")

    def test_local_review_requires_account_and_is_audited(self):
        self.add("Order confirmation", labels=("SPAM",))
        review_id = self.store.reviews("personal")[0]["id"]
        with self.assertRaises(ValueError):
            self.store.review("university", review_id, "dismissed", "reviewed")
        self.store.review("personal", review_id, "confirmed_for_manual_action", "Verified externally")
        self.assertEqual(self.store.reviews("personal")[0]["status"], "confirmed_for_manual_action")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM placement_audit").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            self.store.review("personal", review_id, "dismissed", "again")

    def test_all_local_pages_classified_and_incoming_classification(self):
        for n in range(205):
            self.messages.save(EmailMessage(str(n), "personal", "s", "Order confirmation"))
        self.assertEqual(self.service.run("personal"), 205)
        self.assertEqual(self.store.progress("personal")["classified"], 205)
        reader = Mock()
        reader.list_recent.return_value = [EmailMessage("incoming", "personal", "s", "Payment received")]
        SyncRecentEmails(reader, self.messages, self.service).execute("personal")
        self.assertEqual(self.store.progress("personal")["classified"], 206)

    def test_progress_route_order_and_paginated_account_isolation(self):
        self.add("Order confirmation", "1")
        self.add("Hello", "2")
        app = FastAPI()
        register_intelligence_routes(app, self.messages)
        with TestClient(app) as client:
            progress = client.get("/api/classifications/personal/progress")
            self.assertEqual(progress.status_code, 200)
            self.assertFalse(progress.json()["history"]["completed"])
            rows = client.get("/api/classifications/personal?limit=1").json()
            self.assertEqual(rows["next_after"], "1")
            rows = client.get("/api/classifications/personal?after=1").json()
            self.assertEqual(rows["items"][0]["message_id"], "2")
            self.assertEqual(client.get("/api/classifications/university").json()["items"], [])
            self.assertEqual(client.get("/api/classifications/bad/progress").status_code, 400)
            self.assertEqual(client.get("/api/classifications/personal?limit=101").status_code, 422)

    def test_recurring_label_evidence_reopens_only_superseded_proposals(self):
        self.add("Order confirmation", labels=("SPAM",))
        self.add("Order confirmation", labels=())
        self.add("Order confirmation", labels=("SPAM",))
        review = self.store.reviews("personal")[0]
        self.assertEqual(review["status"], "proposed")
        self.store.review("personal", review["id"], "dismissed", "Not misplaced")
        self.service.run("personal")
        self.assertEqual(self.store.reviews("personal")[0]["status"], "dismissed")

    def test_orders_is_a_system_category_with_package_icon(self):
        orders = next(c for c in self.service.registry.categories() if c["id"] == "orders")
        self.assertEqual((orders["source"], orders["icon_key"]), ("system", "package"))
