"""Production cleanup boundaries and automatic quota recovery; no provider requests."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS,SETUP_PROVIDERS,ModelCapability
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore

class FinalFreeAITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.store=SQLiteHybridStore(self.root/'mail.db');self.store.initialize()
  self.cap=ModelCapability('mistral','proven',32000,free=True,structured=True,validated=True,quality={'en':.95})
 def test_only_five_providers_are_active_and_archived_rows_secrets_remain(self):
  self.assertEqual(set(ACTIVE_PROVIDERS),{'ollama','gemini','groq','mistral','zai'});self.assertEqual(SETUP_PROVIDERS,ACTIVE_PROVIDERS)
  secrets=LocalSecretStore(self.root/'secrets')
  for provider in ('siliconflow','nvidia','openrouter','cloudflare','openai','cerebras','sambanova'):
   secrets.put(provider,'saved-private-key');cap=replace(self.cap,provider=provider)
   self.store.register(cap,time.time()+86400)
   with self.store.connect() as db:db.execute('update hybrid_providers set enabled=1 where provider=?',(provider,))
   self.assertFalse(self.store.payment_allowed('personal',cap))
   with self.assertRaisesRegex(ValueError,'retired'):self.store.select_models('personal',provider,[])
   with self.assertRaisesRegex(ValueError,'retired'):self.store.reserve('personal',cap,'task',['m'],100,100)
   self.store.initialize();self.assertFalse(self.store.provider(provider)['enabled']);self.assertEqual(secrets.get(provider),'saved-private-key')
   self.assertTrue(any(m['provider']==provider for m in self.store.models()))
 def test_gemini_and_groq_resume_when_recorded_cooldown_expires_without_reset(self):
  now=time.time()
  for provider in ('gemini','groq'):
   self.store.configure(provider,True)
   with self.store.connect() as db:
    db.execute("insert or replace into hybrid_health values(?,'*',1,?,0,'provider_rate_limited')",(provider,now+60))
    db.execute("insert or replace into hybrid_health values(?,'proven',1,?,0,'provider_rate_limited')",(provider,now+60))
   with patch('email_agent.infrastructure.database.hybrid_store.time.time',return_value=now):self.assertFalse(self.store.healthy(provider,'proven'))
   with patch('email_agent.infrastructure.database.hybrid_store.time.time',return_value=now+61):self.assertTrue(self.store.healthy(provider,'proven'))
   with self.store.connect() as db:self.assertEqual(db.execute("select until from hybrid_health where provider=? and model='*'",(provider,)).fetchone()[0],now+60)
 def test_qualified_zai_requires_only_separate_account_consent(self):
  cap=replace(self.cap,provider='zai',model='glm-4.5-flash',quality={'ar':.9333});self.store.register(cap,time.time()+86400);self.store.configure('zai',True);self.store.record_model_check('personal',cap,{'passed':True},'stored-real-proof')
  self.assertEqual(self.store.candidates('personal'),[])
  self.store.authorize_standing('personal',['zai'],'explicit-personal-scope')
  self.assertEqual([m.model for m in self.store.candidates('personal')],['glm-4.5-flash']);self.assertEqual(self.store.candidates('university'),[])
  self.assertEqual(next(m for m in self.store.models() if m['provider']=='zai')['payload']['quality'],{'ar':.9333})
 def test_retained_mistral_and_local_candidates_stay_available(self):
  for provider in ('mistral','ollama'):
   cap=replace(self.cap,provider=provider);self.store.register(cap,time.time()+86400);self.store.configure(provider,True)
   if provider!='ollama':
    self.store.record_model_check('personal',cap,{'passed':True},'existing-proof');self.store.authorize_standing('personal',[provider],'existing-personal-access')
  self.assertEqual({m.provider for m in self.store.candidates('personal')},{'mistral','ollama'})
