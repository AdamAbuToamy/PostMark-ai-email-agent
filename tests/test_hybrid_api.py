"""Provider-management consent/secret boundaries without external requests."""
import json
import os
from unittest.mock import patch,Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
from email_agent.api.batch3 import register_batch3_routes
from email_agent.api.message_management import register_message_management_routes
from email_agent.infrastructure.ai.hybrid_providers import GeminiProvider


class HybridAPITests(Base):
    def api(self):
        admission=patch('email_agent.infrastructure.ai.resource_monitor.resources.local_allowed',return_value=True)
        admission.start();self.addCleanup(admission.stop)
        app=FastAPI()
        register_message_management_routes(app,self.messages,self.root/'data/categories.db')
        with patch.dict(os.environ,{},clear=True):register_batch3_routes(app,self.messages,self.root,Mock(),lambda:Mock())
        api=TestClient(app,client=('127.0.0.1',1234));self.addCleanup(api.close);self.app=app
        return api
    def test_secret_config_is_private_and_account_consent_is_not_inferred(self):
        api=self.api();url='/api/hybrid/personal/providers/groq';key='fixture-secret-not-for-ui'
        self.assertEqual(api.post(url,json={'enabled':True,'key':key}).status_code,403)
        response=api.post(url,headers=self.headers,json={'enabled':True,'key':key})
        self.assertEqual(response.status_code,200);self.assertNotIn(key,response.text)
        state=api.get('/api/hybrid/university').json();self.assertEqual(state['grants'],[]);self.assertNotIn(key,json.dumps(state));self.assertEqual(state['cloud'],'Limited')
        self.assertEqual((self.root/'data/hybrid-secrets/groq').stat().st_mode&0o077,0)
    def test_preferences_and_exact_scopes_are_account_isolated(self):
        api=self.api();api.post('/api/hybrid/personal/preference',headers=self.headers,json={'preference':'local'})
        self.assertEqual(api.get('/api/hybrid/personal').json()['preference'],'local');self.assertEqual(api.get('/api/hybrid/university').json()['preference'],'auto')
        scope=dict(approved=True,models=['fixture'],requests=1,input_tokens=1000,output_tokens=1000,messages=2,free_only_confirmed=True,request_id='scope')
        a=api.post('/api/hybrid/personal/providers/groq/grant',headers=self.headers,json=scope);b=api.post('/api/hybrid/personal/providers/groq/grant',headers=self.headers,json=scope)
        self.assertEqual(a.status_code,200);self.assertEqual(a.json(),b.json())
        changed=api.post('/api/hybrid/university/providers/groq/grant',headers=self.headers,json=scope);self.assertEqual(changed.status_code,400)
    def test_discovery_does_not_claim_schema_or_free_verification_and_is_not_replayed(self):
        api=self.api();api.post('/api/hybrid/personal/providers/gemini',headers=self.headers,json={'enabled':True,'key':'fixture-private'})
        def discover(provider):
            self.assertTrue(provider.metadata_reserve())
            return [dict(provider='gemini',model='new-fixture',context=32000,structured=False,free=False)]
        with patch.object(GeminiProvider,'discover',discover):
            request={'approved':True,'request_id':'metadata'}
            a=api.post('/api/hybrid/personal/providers/gemini/discover',headers=self.headers,json=request)
            b=api.post('/api/hybrid/personal/providers/gemini/discover',headers=self.headers,json=request)
            self.assertEqual(a.json(),b.json());self.assertEqual(a.status_code,200)
            self.assertEqual(api.post('/api/hybrid/university/providers/gemini/discover',headers=self.headers,json=request).status_code,400)
        model=api.get('/api/hybrid/personal').json()['models'][0]['payload']
        self.assertFalse(model['validated']);self.assertFalse(model['free']);self.assertFalse(model['structured'])
    def test_other_population_reconciles_and_unknown_accounts_fail(self):
        api=self.api();state=api.get('/api/hybrid/personal/other').json()
        self.assertEqual(sum(state['counts'].values()),state['total'])
        self.assertEqual(api.get('/api/hybrid/not-an-account').status_code,400)
        self.assertEqual(api.post('/api/hybrid/personal/providers/ollama',headers=self.headers,json={'enabled':True,'endpoint':'http://8.8.8.8:11434'}).status_code,400)

    def test_remote_inference_releases_gmail_and_body_sync_locks(self):
        from email_agent.infrastructure.gmail.request_policy import sync_lock
        from email_agent.workers.fill_content import fill_lock
        api=self.api()
        def inference(account):
            with fill_lock(self.root/'data'),sync_lock(account,self.root/'data'):
                return {'status':'waiting_model'}
        with patch('email_agent.infrastructure.database.continuous_store.ContinuousStore.enabled',return_value=True),patch.object(self.app.state.semantic_organizer,'organize_local',return_value={'processed':0}),patch.object(self.app.state.hybrid_organizer,'batch',side_effect=inference):
            response=api.post('/api/organizer/personal/automation-step',json={},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['semantic']['status'],'continuous_worker')

    def test_status_reads_persisted_data_without_probing_or_queue_reconciliation(self):
        api=self.api()
        with patch.object(self.app.state.hybrid_store,'reconcile_other',side_effect=AssertionError('Status must not mutate the queue')),patch('email_agent.infrastructure.ai.hybrid_providers.LocalModelProvider.discover',side_effect=AssertionError('No live probing')),patch('email_agent.infrastructure.ai.hybrid_providers.OpenAICompatibleProvider.discover',side_effect=AssertionError('No live probing')):
            response=api.get('/api/hybrid/personal')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('eligible_routes',response.json())

    def test_existing_background_owner_immediately_continues_after_a_successful_batch(self):
        import asyncio,time
        from types import SimpleNamespace
        from email_agent.workers.analysis_worker import single_worker
        self.api();calls=[];local_active=[]
        async def exercise():
            done=asyncio.Event();loop=asyncio.get_running_loop()
            def batch(account,**kwargs):
                provider=kwargs['provider'];calls.append((account,provider,time.monotonic(),bool(local_active)))
                if provider=='ollama':local_active.append(True);time.sleep(.3);local_active.pop()
                if sum(c[1]=='gemini' for c in calls)>=4:loop.call_soon_threadsafe(done.set)
                return {'accepted':1,'status':'classified'}
            models=[SimpleNamespace(provider=p,model='fixture') for p in ('ollama','gemini')]
            with patch('email_agent.infrastructure.database.continuous_store.ContinuousStore.enabled',return_value=True),patch.object(self.app.state.hybrid_store,'candidates',return_value=models),patch.object(self.app.state.hybrid_store,'healthy',return_value=True),patch.object(self.app.state.hybrid_organizer,'refresh_eligibility'),patch.object(self.app.state.hybrid_organizer,'batch',side_effect=batch),patch.object(self.app.state.provider_qualification,'step',return_value={'status':'idle'}):
                async with self.app.router.lifespan_context(self.app):
                    await asyncio.wait_for(done.wait(),3)
                    with self.assertRaises(RuntimeError):
                        with single_worker(self.root/'data/ai_worker.lock'):pass
        asyncio.run(exercise())
        cloud=[c for c in calls if c[1]=='gemini']
        self.assertEqual([c[0] for c in cloud[:4]],['personal','university','personal','university'])
        self.assertTrue(any(c[3] for c in cloud),'Slow local work must not block the cloud lane')
        self.assertLess(cloud[3][2]-cloud[0][2],3)
