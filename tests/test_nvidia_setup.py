"""Developer-only NIM catalog and immutable production prohibition; no remote calls."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS,SETUP_PROVIDERS,ModelCapability
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,ORIGINS
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore

class NvidiaSetupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=SQLiteHybridStore(Path(self.tmp.name)/'mail.db');self.store.initialize()
 def test_openrouter_is_archived_without_secret_deletion(self):
  self.assertNotIn('openrouter',SETUP_PROVIDERS)
  secrets=LocalSecretStore(Path(self.tmp.name)/'secrets');secrets.put('openrouter','retained-private-key')
  with self.assertRaisesRegex(ValueError,'retired'):self.store.configure('openrouter',True)
  self.assertEqual(secrets.get('openrouter'),'retained-private-key')
 def test_nvidia_discovery_preserves_exact_ids_and_never_claims_production_free(self):
  adapter=OpenAICompatibleProvider('nvidia','private-fixture-key');adapter.http=Mock()
  adapter.http.request.return_value={'data':[{'id':'other/chat-model'},{'id':'nvidia/nemotron-example','context_length':128000}]}
  found=adapter.discover();adapter.http.request.assert_called_once_with('/models')
  self.assertEqual(found[0]['model'],'nvidia/nemotron-example');self.assertTrue(all(not m['free'] for m in found))
  self.assertFalse(adapter.discovery_info['production_permitted']);self.assertFalse(adapter.discovery_info['model_access_verified'])
  self.assertEqual(ORIGINS['nvidia'],'https://integrate.api.nvidia.com/v1')
 def test_unsupported_and_empty_catalog_have_specific_errors(self):
  adapter=OpenAICompatibleProvider('nvidia','private-fixture-key');adapter.http=Mock()
  adapter.http.request.return_value={'data':[]}
  with self.assertRaisesRegex(ValueError,'no model candidates'):adapter.discover()
  adapter.http.request.return_value={}
  with self.assertRaisesRegex(Exception,'missing its model data array'):adapter.discover()
 def test_even_forged_free_qualification_cannot_authorize_real_mail(self):
  self.assertNotIn('nvidia',SETUP_PROVIDERS);self.assertNotIn('nvidia',ACTIVE_PROVIDERS)
  cap=ModelCapability('nvidia','nvidia/nemotron-example',32000,free=True,validated=True,structured=True,quality={'en':1})
  self.assertFalse(self.store.payment_allowed('personal',cap));self.assertFalse(self.store.payment_allowed('university',cap))
  with self.assertRaisesRegex(ValueError,'retired'):self.store.reserve('personal',cap,'x',['m'],100,100)
  with self.assertRaises(ValueError):self.store.authorize_standing('personal',['nvidia'],'must-reject')
  adapter=OpenAICompatibleProvider('nvidia','private-fixture-key');adapter.http=Mock()
  with self.assertRaisesRegex(ValueError,'production mail is forbidden'):adapter.analyze(cap,'instructions',{'messages':[]},{})
  adapter.http.request.assert_not_called()
