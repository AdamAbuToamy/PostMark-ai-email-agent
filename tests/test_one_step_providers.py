"""Archived-provider integrity and authenticated metadata proof reuse. No network."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS,ModelCapability
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore

class OneStepProvidersTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=SQLiteHybridStore(Path(self.tmp.name)/'mail.db');self.store.initialize()
  self.cap=ModelCapability('groq','proven-model',32000,free=True,validated=True,structured=True,quality={'en':.95},fingerprint='same')
 def test_retired_providers_keep_secrets_but_cannot_enable_or_reserve(self):
  secrets=LocalSecretStore(Path(self.tmp.name)/'secrets')
  for name in ('sambanova','cerebras','cloudflare','openai'):
   secrets.put(name,'private-fixture-key');self.assertEqual(secrets.get(name),'private-fixture-key')
   self.assertNotIn(name,ACTIVE_PROVIDERS)
   with self.assertRaisesRegex(ValueError,'retired'):self.store.configure(name,True)
   cap=replace(self.cap,provider=name)
   with self.assertRaisesRegex(ValueError,'retired'):self.store.reserve('personal',cap,'test',['m'],100,100)
 def test_discovery_reuses_successful_same_model_proof_without_mailbox_consent(self):
  self.store.record_model_check('personal',self.cap,{'passed':True},'actual-recorded-proof')
  self.store.credential_changed('groq')
  self.assertFalse(self.store.model_checked('personal','groq',self.cap.model))
  self.assertTrue(self.store.reuse_discovered_model('personal',self.cap,True))
  self.assertTrue(self.store.model_checked('personal','groq',self.cap.model))
  self.assertFalse(self.store.standing('personal','groq'));self.assertFalse(self.store.standing('university','groq'))
  self.assertEqual(self.store.candidates('personal'),[])
 def test_new_unqualified_changed_or_unknown_cost_model_cannot_borrow_evidence(self):
  self.store.record_model_check('personal',self.cap,{'passed':True},'proof')
  for cap,unchanged in [(self.cap,False),(replace(self.cap,validated=False),True),(replace(self.cap,free=False),True),(replace(self.cap,model='unseen'),True)]:
   self.assertFalse(self.store.reuse_discovered_model('personal',cap,unchanged))
 def test_saving_same_key_preserves_revision_and_standing_scope(self):
  self.store.observe_credential('groq','same-private-key');self.store.authorize_standing('personal',['groq'],'explicit-existing-consent')
  revision=self.store.credential_revision('groq');self.store.observe_credential('groq','same-private-key')
  self.assertEqual(self.store.credential_revision('groq'),revision);self.assertTrue(self.store.standing('personal','groq'))
  self.store.observe_credential('groq','different-private-key');self.assertFalse(self.store.standing('personal','groq'))
  self.assertFalse(self.store.standing('university','groq'))
