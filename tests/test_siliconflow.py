"""Dated official zero prices plus account metadata; no actual inference or secrets."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from email_agent.application.hybrid_ai import ModelCapability,ACTIVE_PROVIDERS,SETUP_PROVIDERS
from email_agent.application.semantic_mail import SCHEMA
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,ORIGINS
from email_agent.infrastructure.ai.free_catalog import siliconflow_free
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore

class SiliconFlowTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.store=SQLiteHybridStore(Path(self.temp.name)/'mail.db');self.store.initialize()
  self.adapter=OpenAICompatibleProvider('siliconflow','synthetic-private-key');self.adapter.http=Mock()
  self.cap=ModelCapability('siliconflow','Qwen/Qwen3-8B',131072,structured=True,free=True,validated=True,quality={'en':.95},output_mode='json_object')
 def test_exact_available_zero_price_text_candidates_only(self):
  self.adapter.http.request.return_value={'data':[{'id':'deepseek-ai/DeepSeek-V3'},{'id':'Pro/Qwen/Qwen3-8B'},{'id':'XingChenAGI/Xing4.0-29B'},{'id':'Qwen/Qwen3-8B'},{'id':'BAAI/bge-m3'},{'id':'unknown/zero','pricing':{'prompt':'0','completion':'0'}}]}
  models=self.adapter.discover();self.adapter.http.request.assert_called_once_with('/models?type=text&sub_type=chat')
  self.assertEqual([m['model'] for m in models],['Qwen/Qwen3-8B','XingChenAGI/Xing4.0-29B']);self.assertTrue(all(m['free'] and not m['structured'] for m in models));self.assertFalse(self.adapter.discovery_info['model_access_verified'])
  self.assertIsNone(siliconflow_free('XingChenAGI/Xing4.0-29B')['open_weights']);self.assertTrue(siliconflow_free('Qwen/Qwen3-8B')['open_weights'])
 def test_conflicting_price_expired_evidence_and_empty_catalog_block(self):
  for listing in [{'data':[{'id':'Qwen/Qwen3-8B','pricing':{'prompt':'0','completion':'.2'}}]},{'data':[]}]:
   self.adapter.http.request.return_value=listing
   self.assertEqual(self.adapter.discover(),[]);self.assertTrue(self.adapter.discovery_info['authentication_verified']);self.assertIn('No verified zero-price',self.adapter.discovery_info['blocker'])
  with patch('email_agent.infrastructure.ai.free_catalog.time.time',return_value=10**12):self.assertIsNone(siliconflow_free(self.cap.model));self.assertFalse(self.store.payment_allowed('personal',self.cap))
 def test_new_provider_does_not_inherit_account_mail_permission(self):
  self.assertIn('siliconflow',ACTIVE_PROVIDERS);self.store.register(self.cap,time.time()+86400);self.store.configure('siliconflow',True);self.store.record_model_check('personal',self.cap,{'passed':True},'recorded-fixture-proof')
  self.assertEqual(self.store.candidates('personal'),[]);self.assertEqual(self.store.candidates('university'),[])
  self.store.authorize_standing('personal',['siliconflow'],'explicit-personal-test-consent')
  self.assertEqual(len(self.store.candidates('personal')),1);self.assertEqual(self.store.candidates('university'),[])
 def test_adapter_json_object_thinking_control_and_fixed_origin(self):
  self.assertEqual(ORIGINS['siliconflow'],'https://api.siliconflow.cn/v1')
  self.adapter.http.request.return_value={'model':self.cap.model,'choices':[{'message':{'content':'{"frames":[],"changes":[]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':4,'completion_tokens':3}}
  result=self.adapter.analyze(self.cap,'instructions',{'messages':[],'catalog':[],'organization_scope':{'synthetic':True}},SCHEMA)
  args,kwargs=self.adapter.http.request.call_args;payload=args[1] if len(args)>1 else kwargs['payload']
  self.assertEqual(payload['model'],self.cap.model);self.assertEqual(payload['response_format'],{'type':'json_object'});self.assertFalse(payload['enable_thinking']);self.assertNotIn('thinking',payload);self.assertEqual(result.provider,'siliconflow')
 def test_paid_or_unknown_model_cannot_be_forced_by_free_flag(self):
  for name in ['deepseek-ai/DeepSeek-V3','Pro/Qwen/Qwen3-8B','unlisted/model']:
   cap=replace(self.cap,model=name);self.assertFalse(self.store.payment_allowed('personal',cap))
   with self.assertRaisesRegex(ValueError,'pricing is unknown or nonzero'):self.adapter.analyze(cap,'',{},SCHEMA)
  self.adapter.http.request.assert_not_called()
 def test_nvidia_retired_with_history_and_credential_retained(self):
  self.assertNotIn('nvidia',SETUP_PROVIDERS);secrets=LocalSecretStore(Path(self.temp.name)/'secrets');secrets.put('nvidia','old-private-key')
  with self.assertRaisesRegex(ValueError,'retired'):self.store.configure('nvidia',True)
  self.assertEqual(secrets.get('nvidia'),'old-private-key')
