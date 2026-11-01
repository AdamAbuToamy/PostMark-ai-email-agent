"""Persistent presentation facts, isolated accounts and removed-model recovery."""
from datetime import datetime, timezone, timedelta
from test_batch3 import Base
from email_agent.application.subscription_tracking import tracking, include_saved_lists
from email_agent.infrastructure.database.action_store import SQLiteActionStore
from email_agent.domain.message import EmailMessage


class SubscriptionTrackingTests(Base):
    def saved(self,account="personal",state="cancelled",kind="unsubscribe:browser"):
        payload={"subscription_id":"list-a","identity":"list.example","display_name":"List A","message_id":"sale1",
                 "items":[{"subscription_id":"list-a","identity":"list.example","display_name":"List A","message_id":"sale1"}]}
        value=self.store.preview(account,kind,payload)
        self.assertTrue(self.store.claim(account,value["id"]))
        self.assertTrue(self.store.start_subscription(account,value["id"],"list-a"))
        if kind=="unsubscribe:browser":self.store.finish_browser(account,value["id"],"list-a",{"state":state,"reason":"website_confirmation","evidence":"You have been unsubscribed from this list"})
        else:self.store.finish_subscriptions(account,value["id"],"succeeded",{"items":[{"subscription_id":"list-a","status":state}]})
        return value

    def test_confirmed_survives_restart_and_cannot_repeat(self):
        value=self.saved()
        reopened=SQLiteActionStore(self.messages.database_path)
        state=reopened.subscription_states("personal")["list-a"]
        status=tracking(reopened,"personal",{},state)
        self.assertEqual(status["state"],"unsubscribed");self.assertTrue(status["completed_at"])
        self.assertEqual(status["method"],"website")
        other=reopened.preview("personal","unsubscribe:browser",value["payload"]);reopened.claim("personal",other["id"])
        self.assertFalse(reopened.start_subscription("personal",other["id"],"list-a"))
        self.assertEqual(reopened.subscription_states("university"),{})

    def test_receipt_uncertain_and_manual_handled_are_not_confirmed(self):
        for state in ("requested","uncertain"):
            account="personal" if state=="requested" else "university"
            self.saved(account,state,"unsubscribe:one_click")
            status=tracking(self.store,account,{},self.store.subscription_states(account)["list-a"])
            self.assertEqual(status["state"],"submitted" if state=="requested" else "uncertain")
            self.assertIsNone(status["completed_at"])
        self.store.mark_manual("personal","manual-only","Visited the page")
        self.assertEqual(tracking(self.store,"personal",{},None)["state"],"active")

    def test_cancelled_without_explicit_evidence_requires_verification(self):
        value=self.saved()
        self.store.finish_browser("personal",value["id"],"list-a",{"state":"cancelled"})
        status=tracking(self.store,"personal",{},self.store.subscription_states("personal")["list-a"])
        self.assertEqual(status["state"],"uncertain")
        self.assertIsNone(status["completed_at"])

    def test_expired_inflight_operation_is_uncertain_without_replay(self):
        from contextlib import closing
        value=self.saved(state="running")
        with closing(self.store.connect()) as db,db:db.execute("UPDATE action_previews SET expires=0 WHERE id=?",(value["id"],))
        state=self.store.subscription_states("personal")["list-a"]
        self.assertEqual(tracking(self.store,"personal",{},state)["state"],"uncertain")
        self.assertEqual(self.store.subscription_states("personal")["list-a"],state)

    def test_unknown_failed_submission_requires_verification(self):
        value=self.saved(state="failed")
        self.store.finish_browser("personal",value["id"],"list-a",{"state":"failed","submission_attempted":True})
        self.assertEqual(tracking(self.store,"personal",{},self.store.subscription_states("personal")["list-a"])["state"],"uncertain")

    def test_later_message_does_not_reset_confirmation(self):
        self.saved();state=self.store.subscription_states("personal")["list-a"]
        future=(datetime.now(timezone.utc)+timedelta(minutes=5)).timestamp()*1000
        status=tracking(self.store,"personal",{"message_dates":[future]},state)
        self.assertTrue(status["later_message"]);self.assertEqual(status["state"],"unsubscribed")
        self.assertEqual(self.store.subscription_states("personal")["list-a"],state)

    def test_saved_lists_remain_available_after_category_change(self):
        self.saved()
        groups=include_saved_lists(self.store,"personal",[],self.store.subscription_states("personal"))
        self.assertEqual(groups[0]["identity"],"list.example")
        self.assertEqual(groups[0]["observed_count"],0)
        self.assertEqual(include_saved_lists(self.store,"university",[],{}),[])
        self.assertEqual(len(include_saved_lists(self.store,"personal",groups,self.store.subscription_states("personal"))),1)
        self.assertTrue(any(v["event"]=="website_succeeded" for v in self.store.subscription_history("personal","list-a")))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import Mock
        from email_agent.api.batch3 import register_batch3_routes
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock())
        with TestClient(app,client=("127.0.0.1",1)) as api:
            self.assertEqual(api.get("/api/subscriptions/personal/list-a/history").status_code,200)
            self.assertEqual(api.get("/api/subscriptions/university/list-a/history").status_code,404)


    def test_multiple_lists_from_same_sender_and_changed_sender_domain_stay_separate(self):
        from email_agent.application.subscriptions import observed_subscriptions
        for id,identity,sender in [("a","list-a.example","old@company.example"),("b","list-a.example","new@other.example"),("c","list-b.example","old@company.example")]:
            message=EmailMessage(id,"personal",sender,"Discount sale",label_ids=("INBOX",))
            self.messages.save(message);self.classifier.classify(message)
            self.messages.record_list_headers("personal",id,{"list-id":"<"+identity+">"})
        groups=observed_subscriptions(self.messages,"personal")
        first=next(g for g in groups if g["identity"]=="list-a.example")
        second=next(g for g in groups if g["identity"]=="list-b.example")
        self.assertCountEqual(first["message_ids"],["a","b"]);self.assertNotEqual(first["id"],second["id"])


class ModelCatalogTests(Base):
    def test_removed_model_is_preserved_but_never_recommended(self):
        from email_agent.infrastructure.ai.usage import SQLiteAIQuota
        from email_agent.application.ai_configuration import AIConfiguration
        from unittest.mock import Mock
        quota=SQLiteAIQuota(self.root/"data"/"usage.db",provider_managed_chat=True)
        config=AIConfiguration(quota,True,"gemini-3.5-flash-lite",Mock())
        config.change_model("gemini-3.8-flash",True)
        status=config.status({"enabled":False})
        self.assertEqual(status["model"],"gemini-3.8-flash")
        self.assertNotIn("gemini-3.8-flash",[item["id"] for item in status["models"]])
        self.assertIn("gemini-3.5-flash-lite",[item["id"] for item in status["models"]])
        config.probe.assert_not_called()
