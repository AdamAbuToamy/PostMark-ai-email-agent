"""Recorded accounting and resource admission; never invokes a provider."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from email_agent.application.hybrid_ai import organization_schema
from email_agent.application.semantic_mail import SCHEMA
from email_agent.infrastructure.ai.resource_monitor import ResourceMonitor
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,cloudflare_endpoint
from email_agent.infrastructure.database.capacity_audit import usage


class CapacityResourceTests(unittest.TestCase):
    def test_idle_weight_cache_advice_preserves_files_and_rejects_untrusted_paths(self):
        from email_agent.infrastructure.ai.resource_monitor import advise_idle_model_cache
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'manifests').mkdir();(root/'blobs').mkdir()
            digest='sha256:'+('a'*64);weight=root/'blobs'/digest.replace(':','-');weight.write_bytes(b'installed weights')
            (root/'manifests'/'model').write_text(json.dumps({'layers':[{'digest':digest,'mediaType':'application/vnd.ollama.image.model'},{'digest':'../../private','mediaType':'application/vnd.ollama.image.model'}]}))
            with patch('email_agent.infrastructure.ai.resource_monitor.os.posix_fadvise') as advice:
                self.assertEqual(advise_idle_model_cache(root),len(b'installed weights'));advice.assert_called_once()
            self.assertEqual(weight.read_bytes(),b'installed weights')

    def test_cache_advice_waits_for_genuine_idle_pressure_and_is_rate_bounded(self):
        monitor=ResourceMonitor();server=[{'name':'ollama'}]
        with patch('email_agent.infrastructure.ai.resource_monitor.advise_idle_model_cache',return_value=123) as advice:
            monitor._release_idle_cache(1000,server+[{'name':'llama-server'}],'Critical');advice.assert_not_called()
            monitor._release_idle_cache(1001,server,'Critical');monitor._release_idle_cache(1032,server,'Normal');advice.assert_not_called()
            monitor._release_idle_cache(1033,server,'Critical');advice.assert_called_once()
            monitor._release_idle_cache(1050,server,'Critical');self.assertEqual(advice.call_count,1)
            monitor._release_idle_cache(1400,server+[{'name':'llama-server'}],'Critical');self.assertEqual(advice.call_count,1)

    def test_reasoning_is_not_double_counted_and_unknown_usage_stays_unknown(self):
        rows=[dict(task_key='mail',status='done',usage=json.dumps({'prompt_tokens':20,'output_tokens':10,'reasoning_tokens':6,'total_tokens':30})),dict(task_key='mail#retry1',status='failed',usage=None)]
        u=usage(rows);self.assertEqual(u['reported_tokens'],30);self.assertEqual(u['reasoning_tokens'],6);self.assertEqual(u['unreported_usage_requests'],1);self.assertEqual(u['retries'],1)

    def test_impossible_single_message_changes_are_empty_without_changing_frame_contract(self):
        schema=organization_schema({'messages':[{'id':'m0'}],'catalog':[{'id':'orders'}]},SCHEMA)
        self.assertEqual(schema['properties']['changes']['maxItems'],0)
        self.assertEqual(schema['properties']['changes']['items']['properties'],{})
        self.assertEqual(schema['properties']['frames']['items']['properties']['category_id']['enum'],['orders',None])
        multi=organization_schema({'messages':[{'id':'m0'},{'id':'m1'}],'catalog':[{'id':'orders'}]},SCHEMA)
        self.assertIn('action',multi['properties']['changes']['items']['properties'])

    def test_host_pressure_pauses_only_new_local_admission_then_recovers(self):
        monitor=ResourceMonitor();now=time.time();monitor.host_at=now;monitor.host={'FreePhysicalMemory':512*1024,'TotalVisibleMemorySize':24*1024*1024}
        def read(path,*args,**kwargs):
            if str(path)=='/proc/meminfo':return 'MemTotal: 12000000 kB\nMemAvailable: 5000000 kB\nSwapTotal: 3000000 kB\nSwapFree: 1000000 kB\n'
            if str(path)=='/proc/pressure/memory':return 'some avg10=0\nfull avg10=0 avg60=0\n'
            return 'VmRSS: 1024 kB\n'
        with patch.object(Path,'read_text',read),patch.object(Path,'iterdir',return_value=[]),patch('email_agent.infrastructure.ai.resource_monitor.time.time',return_value=now):
            self.assertFalse(monitor.local_allowed());self.assertEqual(monitor.snapshot()['pressure'],'Critical')
        monitor.host={'FreePhysicalMemory':6*1024*1024,'TotalVisibleMemorySize':24*1024*1024};monitor.host_at=now+301
        with patch.object(Path,'read_text',read),patch.object(Path,'iterdir',return_value=[]),patch('email_agent.infrastructure.ai.resource_monitor.time.time',return_value=now+301):self.assertTrue(monitor.local_allowed())

    def test_cloudflare_origin_and_paid_model_gate_remain_closed(self):
        origin=cloudflare_endpoint('a'*32);self.assertTrue(origin.endswith('/ai/v1'))
        with self.assertRaises(ValueError):cloudflare_endpoint('http://untrusted.invalid')
        with self.assertRaises(ValueError):OpenAICompatibleProvider('cloudflare','fixture-private',endpoint='https://untrusted.invalid')
        provider=OpenAICompatibleProvider('cloudflare','fixture-private',endpoint=origin)
        with patch('email_agent.infrastructure.ai.hybrid_providers.JSONTransport.request',return_value={'success':True,'result':[{'name':'@cf/meta/llama-3.3-70b-instruct-fp8-fast','task':{'name':'Text Generation'}},{'name':'@cf/zai-org/glm-5.3','task':{'name':'Text Generation'},'properties':[{'property_id':'require_workers_paid','value':'true'}]}]}):models=provider.discover()
        self.assertTrue(models[0]['free']);self.assertFalse(models[1]['free'])

    def test_only_safe_capacity_headers_are_retained(self):
        from email_agent.infrastructure.ai.hybrid_providers import JSONTransport
        response=Mock();response.status_code=200;response.headers={'x-ratelimit-remaining-tokens':'3000','Authorization':'private','Set-Cookie':'private'};response.iter_content.return_value=[b'{"data":[]}'];response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        session=Mock();session.request.return_value=response;session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        with patch('email_agent.infrastructure.ai.hybrid_providers.requests.Session',return_value=session):
            transport=JSONTransport('https://example.invalid');transport.request('/models')
        self.assertEqual(transport.last_capacity,{'x-ratelimit-remaining-tokens':'3000'})


class CapacityAPITests(unittest.TestCase):
    from test_hybrid_api import HybridAPITests as Fixture
    setUp=Fixture.setUp
    api=Fixture.api
    add=Fixture.add

    def test_account_scoped_usage_unknown_capacity_and_fixed_cloudflare_destination(self):
        api=self.api();secret='fixture-private-not-public'
        response=api.post('/api/hybrid/personal/providers/cloudflare',headers=self.headers,json={'key':secret,'enabled':False,'cloudflare_account_id':'a'*32})
        self.assertEqual(response.status_code,200);self.assertNotIn(secret,response.text)
        store=self.app.state.hybrid_store
        self.assertEqual(store.provider('cloudflare')['endpoint'],cloudflare_endpoint('a'*32))
        self.assertEqual((self.root/'data/hybrid-secrets/cloudflare').stat().st_mode&0o077,0)
        seen=[]
        def discovered(provider):
            seen.append((provider.http.origin,provider.http.key));return []
        with patch('email_agent.infrastructure.ai.hybrid_providers.OpenAICompatibleProvider.discover',discovered):
            check=api.post('/api/hybrid/personal/providers/cloudflare/discover',headers=self.headers,json={'approved':True,'request_id':'isolated-metadata'})
        self.assertEqual(check.status_code,200,check.text)
        self.assertEqual(seen,[(cloudflare_endpoint('a'*32),secret)])
        state=api.get('/api/hybrid/personal').json();self.assertNotIn(secret,json.dumps(state))
        self.assertEqual(next(p for p in state['providers'] if p['provider']=='cloudflare')['endpoint'],cloudflare_endpoint('a'*32))
        with patch('email_agent.infrastructure.ai.resource_monitor.resources.model_snapshot',return_value=[]):
            r=api.get('/api/hybrid/personal/capacity')
        self.assertEqual(r.status_code,200,r.text);self.assertNotIn(secret,r.text)
        cloud=next(p for p in r.json()['providers'] if p['provider']=='cloudflare')
        self.assertEqual(cloud['allocation']['unit'],'neurons/day');self.assertIsNone(cloud['allocation']['remaining'])
        self.assertEqual(api.post('/api/hybrid/personal/providers/sambanova',headers=self.headers,json={'key':'another-private-fixture','enabled':True}).status_code,400)
        self.assertEqual(api.get('/api/hybrid/missing/capacity').status_code,400)
