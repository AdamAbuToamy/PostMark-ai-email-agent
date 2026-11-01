"""Explicit region, preserved key, authenticated metadata and region-specific pricing."""
import json
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock,patch
from email_agent.application.hybrid_ai import ModelCapability
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,JSONTransport,siliconflow_endpoint
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore

class SiliconFlowRegionTests(TestCase):
 def test_regions_are_exact_origins_and_do_not_guess_or_fall_back(self):
  for region,host in [('international','https://api.siliconflow.com/v1'),('china','https://api.siliconflow.cn/v1')]:
   adapter=OpenAICompatibleProvider('siliconflow','private-fixture',siliconflow_endpoint(region));self.assertEqual(adapter.http.origin,host)
  for value in ['https://api.siliconflow.com.evil.test/v1','http://api.siliconflow.com/v1','https://elsewhere.test']:
   with self.assertRaises(ValueError):OpenAICompatibleProvider('siliconflow','private-fixture',value)
  with self.assertRaises(ValueError):siliconflow_endpoint('auto')
 def test_international_auth_success_is_separate_from_free_models(self):
  adapter=OpenAICompatibleProvider('siliconflow','private-fixture',siliconflow_endpoint('international'));adapter.http=Mock();adapter.http.request.return_value={'data':[{'id':'Qwen/Qwen3-8B'},{'id':'paid/model'}]}
  models=adapter.discover();self.assertEqual(len(models),2);self.assertTrue(all(not m['free'] for m in models));self.assertTrue(adapter.discovery_info['authentication_verified']);self.assertEqual(adapter.discovery_info['region'],'international');self.assertEqual(adapter.discovery_info['free_candidates'],[])
  adapter.http.request.assert_called_once_with('/models?type=text&sub_type=chat')
 def test_bearer_header_and_exact_endpoint_are_used_once(self):
  with patch('email_agent.infrastructure.ai.hybrid_providers.requests.Session') as factory:
   session=factory.return_value.__enter__.return_value;response=session.request.return_value.__enter__.return_value;response.status_code=200;response.headers={};response.iter_content.return_value=[b'{"data":[]}']
   transport=JSONTransport(siliconflow_endpoint('international'),'server-only-key')
   self.assertEqual(transport.request('/models'),{'data':[]});session.request.assert_called_once()
   args,kwargs=session.request.call_args;self.assertEqual(args[:2],('GET','https://api.siliconflow.com/v1/models'));self.assertEqual(kwargs['headers']['Authorization'],'Bearer server-only-key');self.assertFalse(kwargs['allow_redirects'])
 def test_region_change_preserves_key_but_cannot_inherit_chinese_prices_or_proofs(self):
  with tempfile.TemporaryDirectory() as directory:
   store=SQLiteHybridStore(Path(directory)/'db');store.initialize();secrets=LocalSecretStore(Path(directory)/'secrets');secrets.put('siliconflow','saved-key-remains')
   cap=ModelCapability('siliconflow','Qwen/Qwen3-8B',131072,free=True,validated=True,structured=True,quality={'en':.99})
   store.register(cap,time.time()+86400);store.record_model_check('personal',cap,{'passed':True},'china-proof');store.authorize_standing('personal',['siliconflow'],'old-china-scope')
   store.siliconflow_region('international');self.assertEqual(secrets.get('siliconflow'),'saved-key-remains');self.assertFalse(store.payment_allowed('personal',cap));self.assertFalse(store.standing('personal','siliconflow'));self.assertFalse(store.model_checked('personal','siliconflow',cap.model));self.assertEqual(store.candidates('personal'),[])
   revision=store.credential_revision('siliconflow');store.siliconflow_region('international');self.assertEqual(store.credential_revision('siliconflow'),revision)
