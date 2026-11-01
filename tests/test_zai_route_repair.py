"""Bounded qualification/consent regression; no network or real mailbox."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from importlib.resources import files
from email_agent.application.hybrid_ai import ModelCapability
from email_agent.infrastructure.ai.usage import SQLiteAIQuota
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore
from email_agent.infrastructure.database.qualification_store import SQLiteQualificationStore

class ZAIRouteRepairTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.quota=SQLiteAIQuota(self.root/'quota.db');self.quota.configure(20)
  self.hybrid=SQLiteHybridStore(self.root/'mail.db',self.quota);self.hybrid.initialize();self.hybrid.configure('zai',True)
  self.cases=json.loads(files('email_agent.resources').joinpath('semantic_gold_v3.json').read_text())['cases']
  self.q=SQLiteQualificationStore(self.hybrid,self.cases,lambda _:True);self.q.initialize()
  for name,context in [('glm-4.5-flash',128000),('glm-4.7-flash',200000)]:self.hybrid.register(ModelCapability('zai',name,context,free=True,output_mode='json_object'),time.time()+86400)
  with self.hybrid.connect() as db:db.execute("update hybrid_providers set last_state='metadata_connected',last_checked=? where provider='zai'",(time.time(),))
 def test_zai_standing_policy_is_accepted_without_granting_account_access(self):
  self.quota.configure(0);self.quota.authorize_free_automation(['zai'],'Explicit fixture policy')
  self.assertTrue(self.quota.reserve('personal','hybrid:zai'));self.assertFalse(self.hybrid.standing('personal','zai'));self.assertEqual(self.hybrid.candidates('personal'),[])
 def test_preview_is_single_candidate_bounded_and_reuses_saved_discovery(self):
  plan=self.q.preview(['zai']);p=plan['providers'][0]
  self.assertEqual(p['allowed_pool'],['glm-4.7-flash']);self.assertEqual(plan['max_requests'],18);self.assertEqual(plan['metadata_requests'],0);self.assertFalse(plan['free_until_quota'])
  self.q.approve(plan['id'],plan['gold_hash'],['zai'],20,False)
  adapter=Mock();self.q.discovery(self.q.runnable(),'zai',adapter);adapter.discover.assert_not_called()
  with self.hybrid.connect() as db:self.assertEqual(db.execute('select count(*) from qualification_jobs').fetchone()[0],18)
  self.assertEqual(self.hybrid.candidates('university'),[])
 def test_unsupported_routes_are_excluded_while_rate_limited_routes_are_retained(self):
  with self.hybrid.connect() as db:db.execute("insert into hybrid_health values('zai','glm-4.7-flash',1,0,1,'model_unavailable')")
  self.assertEqual(self.q.preview(['zai'])['providers'][0]['allowed_pool'],['glm-4.5-flash'])
  with self.hybrid.connect() as db:db.execute("update hybrid_health set disabled=0,last_error='provider_rate_limited',until=?",(time.time()+60,))
  self.assertEqual(self.q.preview(['zai'])['providers'][0]['allowed_pool'],['glm-4.7-flash'])
 def test_account_access_preflight_rejects_unqualified_provider_without_partial_commit(self):
  from fastapi import FastAPI,HTTPException
  from starlette.requests import Request
  from email_agent.api.provider_qualification import register_provider_qualification,Access
  app=FastAPI();app.state.hybrid_organizer=Mock();register_provider_qualification(app,self.hybrid,lambda _:Mock(),lambda _:True)
  access=next(r.endpoint for r in app.routes if r.path=='/api/qualification/access')
  req=Request({'type':'http','method':'POST','path':'/api/qualification/access','headers':[(b'x-email-agent-action',b'message-management')],'client':('127.0.0.1',1234),'server':('localhost',80),'scheme':'http','query_string':b''})
  with self.assertRaises(HTTPException):access(Access(approved=True,account='personal',providers=['zai'],free_only_confirmed=True,reference='fixture'),req)
  self.assertFalse(self.hybrid.standing('personal','zai'));self.assertFalse(self.quota.free_automation('zai'))

 def test_legacy_probe_uses_explicit_multidimensional_receipt_gold(self):
  from email_agent.application.hybrid_evaluation import category_matches,dimension_scores
  case=json.loads(files('email_agent.resources').joinpath('semantic_benchmark.json').read_text())[0]
  self.assertTrue(category_matches(case,'orders','receipt','paid'))
  self.assertTrue(category_matches(case,'bills','receipt','paid'))
  self.assertFalse(category_matches(case,'orders','invoice','payment_due'))
  self.assertFalse(category_matches(case,'promotions','receipt','paid'))
  self.assertFalse(category_matches(case,'bills','receipt',None))
  result=dimension_scores([dict(case,id=str(i)) for i in range(3)],[dict(case=str(i),language='en',schema_valid=True,predicted_category='orders',predicted_status='paid',predicted_document_type='receipt',predicted_payment_status='paid') for i in range(3)])
  self.assertEqual(result['category_quality']['en'],1)

 def test_explicit_model_selection_is_pinned_and_unknown_cost_is_not_a_candidate(self):
  plan=self.q.preview(['zai'],{'zai':'glm-4.5-flash'})
  self.assertEqual(plan['providers'][0]['allowed_pool'],['glm-4.5-flash']);self.assertEqual(plan['max_requests'],18)
  self.hybrid.register(ModelCapability('zai','paid-or-unknown',200000,free=False),0)
  with self.assertRaises(ValueError):self.q.preview(['zai'],{'zai':'paid-or-unknown'})
  self.assertFalse(self.hybrid.standing('personal','zai'))

 def test_failed_quality_cannot_revoke_verified_free_eligibility_or_successful_schema(self):
  from email_agent.application.hybrid_evaluation import evaluated_capability
  cap=ModelCapability('zai','glm-4.5-flash',128000,free=True,structured=True)
  result={'attempted':6,'valid_schema':4,'category_quality':{'en':0},'workflow_quality':{'en':0},'mean_seconds':10}
  actual=evaluated_capability(cap,result,False)
  self.assertTrue(actual.free);self.assertTrue(actual.structured);self.assertTrue(actual.generation);self.assertFalse(actual.validated)
  self.assertFalse(evaluated_capability(ModelCapability('openai','paid-fixture',128000),result,False).free)
