"""Seven-provider contracts, real persistence gates, no external requests."""
import json
import time
import unittest
from unittest.mock import Mock,patch
from dataclasses import replace
import test_hybrid_api as api_fixture
import test_hybrid_ai as fixture
from email_agent.application.hybrid_ai import ModelCapability,ModelRouter,InferenceResult,HybridInference
from email_agent.application.model_errors import ModelFailure
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider


class MultiProviderTests(unittest.TestCase):
    setUp=fixture.HybridTests.setUp
    cap=fixture.HybridTests.cap
    def test_mistral_discovery_filters_nonchat_and_does_not_claim_free_or_quality(self):
        provider=OpenAICompatibleProvider('mistral','fixture');provider.http=Mock()
        provider.http.request.return_value={'data':[{'id':'opaque-chat','max_context_length':64000,'capabilities':{'completion_chat':True,'function_calling':True}},{'id':'speech','capabilities':{'completion_chat':False}}]}
        result=provider.discover();self.assertEqual(len(result),1);self.assertEqual(result[0]['context'],64000);self.assertFalse(result[0]['free']);self.assertFalse(result[0]['structured'])
    def test_openai_uses_bounded_reasoning_output_and_does_not_store_requests(self):
        provider=OpenAICompatibleProvider('openai','fixture');provider.http=Mock()
        provider.http.request.return_value={'model':'exact-snapshot','choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':{'prompt_tokens':10,'completion_tokens':4}}
        result=provider.analyze(ModelCapability('openai','exact-snapshot',64000),'System',{'messages':[{'id':'m0'}],'catalog':[]},fixture.SCHEMA)
        body=provider.http.request.call_args.args[1]
        self.assertEqual(body['max_completion_tokens'],2048);self.assertNotIn('temperature',body);self.assertNotIn('max_tokens',body);self.assertFalse(body['store']);self.assertEqual(result.provider,'openai')
    def test_openai_never_inherits_free_and_requires_explicit_paid_mode_and_scope(self):
        model=ModelCapability('openai','snapshot',64000,structured=True,validated=True,free=True,quality={'en':1})
        self.hybrid.register(model);self.hybrid.configure('openai',True)
        self.hybrid.record_model_check('personal',model,{'passed':True},'fixture')
        self.hybrid.grant('personal','openai',['snapshot'],10,100000,10000,10,'Fixture generic consent')
        self.assertFalse(next(m for m in self.hybrid.models() if m['provider']=='openai')['payload']['free'])
        self.assertEqual(self.hybrid.candidates('personal'),[])
        self.hybrid.authorize_paid('personal',model,10,100000,10000,10,'1','5','0.1','paid')
        self.assertTrue(self.hybrid.free_only('personal'));self.assertEqual(self.hybrid.candidates('personal'),[])
        self.hybrid.free_only('personal',False)
        self.assertEqual([m.model for m in ModelRouter(self.hybrid).select('personal','en',1000)],['snapshot'])
        self.assertEqual(self.hybrid.candidates('university'),[])
        self.hybrid.free_only('personal',True);self.assertEqual(self.hybrid.candidates('personal'),[])
    def test_paid_reservations_preserve_usd_ceiling_and_credential_binding(self):
        model=ModelCapability('openai','snapshot',64000)
        self.hybrid.register(model);self.hybrid.configure('openai',True)
        self.hybrid.authorize_paid('personal',model,3,100000,10000,5,'1','5','0.001','paid')
        self.hybrid.free_only('personal',False)
        with self.assertRaises(ValueError):self.hybrid.reserve('personal',model,'over',['m'],1000,2048)
        with self.hybrid.connect() as db:self.assertEqual(db.execute('SELECT reserved_usd FROM hybrid_paid_scopes').fetchone()[0],'0')
        self.hybrid.credential_changed('openai');self.assertIsNone(self.hybrid.paid_scope('personal',model))
    def test_unprobed_cloud_cannot_enter_production_even_with_mocked_qualification_flags(self):
        cap=self.cap('mistral')
        with self.hybrid.connect() as db:db.execute("DELETE FROM hybrid_model_checks WHERE provider='mistral'")
        self.assertEqual(self.hybrid.candidates('personal'),[])
        self.hybrid.record_model_check('personal',cap,{'passed':True},'fixture');self.assertEqual(len(self.hybrid.candidates('personal')),1)
    def test_mistral_429_fails_over_to_local_with_no_paid_fallback(self):
        self.cap('mistral',.99);local=self.cap('ollama',.95)
        cloud=Mock();cloud.analyze.side_effect=ModelFailure('provider_rate_limited','429',429)
        ollama=Mock();ollama.analyze.return_value=InferenceResult('{}','ollama',local.model,{},.1)
        result,_=HybridInference(self.hybrid,{'mistral':cloud,'ollama':ollama}.__getitem__).run('personal','scope',['m'],'System',{},fixture.SCHEMA,'en','cloud')
        self.assertEqual(result.provider,'ollama');self.assertFalse(self.hybrid.healthy('mistral','evaluated-fixture'))

    def test_public_probe_never_grants_mail_consent_or_enables_provider(self):
        model=ModelCapability('mistral','public-fixture',64000,free=True)
        self.hybrid.register(model)
        self.hybrid.revoke('university','mistral')
        self.hybrid.synthetic_scope('probe:public','university',model)
        with self.assertRaises(ValueError):self.hybrid.reserve('university',model,'probe:public',['private-mail'],100,100,synthetic_scope='probe:public')
        with self.assertRaises(ValueError):self.hybrid.reserve('personal',model,'probe:public',[],100,100,synthetic_scope='probe:public')
        attempt=self.hybrid.reserve('university',model,'probe:public',[],100,100,synthetic_scope='probe:public')
        self.hybrid.finish(attempt,'done',InferenceResult('{}','mistral',model.model,{},.1))
        with self.assertRaises(ValueError):self.hybrid.reserve('university',model,'probe:public',[],100,100,synthetic_scope='probe:public')
        self.assertFalse(self.hybrid.provider('mistral')['enabled'])
        self.assertFalse(self.hybrid.account_allows('university','mistral'))
        with self.hybrid.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hybrid_grants').fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT grant_id FROM hybrid_attempts').fetchone()[0])

    def test_changed_environment_credential_invalidates_live_proof_without_resetting_usage(self):
        model=self.cap('mistral')
        self.hybrid.observe_credential('mistral','initial-fixture-key')
        attempt=self.hybrid.reserve('personal',model,'used',['m'],100,100)
        self.hybrid.finish(attempt,'done',InferenceResult('{}','mistral',model.model,{},.1))
        self.hybrid.observe_credential('mistral','changed-fixture-key')
        self.assertFalse(self.hybrid.model_checked('personal','mistral',model.model))
        self.assertEqual(self.hybrid.candidates('personal'),[])
        with self.hybrid.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM hybrid_attempts').fetchone()[0],1)


class MultiProviderAPITests(unittest.TestCase):
    setUp=api_fixture.HybridAPITests.setUp
    add=api_fixture.HybridAPITests.add
    api=api_fixture.HybridAPITests.api
    def setup_mistral(self):
        api=self.api();store=self.app.state.hybrid_store
        api.post('/api/hybrid/personal/providers/mistral',headers=self.headers,json={'enabled':False,'key':'fixture-private'})
        store.register(ModelCapability('mistral','opaque-chat',64000))
        api.post('/api/hybrid/personal/providers/mistral',headers=self.headers,json={'enabled':True})
        store.grant('personal','mistral',['opaque-chat'],10,500000,30000,10,'Synthetic fixture consent')
        self.app.state.ai_quota.configure(10)
        return api,store
    def test_one_synthetic_probe_is_budgeted_cached_and_not_quality_qualification(self):
        api,store=self.setup_mistral();path='/api/hybrid/personal/providers/mistral/probe'
        def analyze(provider,model,system,data,schema,**kwargs):
            return InferenceResult(json.dumps({'frames':[dict(id='m0',category_id='bills',primary_intent='Payment receipt',message_type='Receipt',status='Paid',entity=None,sensitivities=['financial'],confidence=.95,evidence=[dict(field='excerpt',quote='Your payment of 42.90 USD was received.')],attributes=[],related_ids=[])],'changes':[]}),model.provider,model.model,{},.01)
        # Use a verbatim quote from the actual public synthetic fixture.
        from importlib.resources import files
        case=json.loads(files('email_agent.resources').joinpath('semantic_benchmark.json').read_text())[0]
        def response(provider,model,system,data,schema,**kwargs):
            result=analyze(provider,model,system,data,schema);value=json.loads(result.text);value['frames'][0]['evidence'][0]['quote']=data['messages'][0]['excerpt'][:120]
            return replace(result,text=json.dumps(value))
        payload={'approved':True,'model':'opaque-chat','free_account_confirmed':True,'request_id':'once'}
        with patch.object(OpenAICompatibleProvider,'analyze',response) as mock:
            a=api.post(path,headers=self.headers,json=payload);b=api.post(path,headers=self.headers,json=payload)
        self.assertEqual(a.status_code,200,a.text);self.assertEqual(a.json(),b.json());self.assertTrue(a.json()['passed'],a.text);self.assertFalse(a.json()['quality_qualified'])
        self.assertEqual(store.candidates('personal'),[])
        with store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM hybrid_attempts').fetchone()[0],1)
        self.assertEqual(api.get('/api/hybrid/university').json()['grants'],[])
    def test_all_providers_share_application_request_ceiling(self):
        api,store=self.setup_mistral();cap=ModelCapability('mistral','opaque-chat',64000,free=True)
        self.app.state.ai_quota.configure(1)
        first=store.reserve('personal',cap,'one',[],100,100);store.finish(first,'done',InferenceResult('{}','mistral',cap.model,{},.1))
        with self.assertRaises(ValueError):store.reserve('personal',cap,'two',[],100,100)
        self.assertEqual(self.app.state.ai_quota.status()['remaining'],0)
    def test_openai_cannot_be_enabled_or_granted_free_consent(self):
        api=self.api();root='/api/hybrid/personal/providers/openai'
        self.assertEqual(api.post(root,headers=self.headers,json={'enabled':True,'key':'fixture-private'}).status_code,200)
        self.assertFalse(self.app.state.hybrid_store.provider('openai')['enabled'])
        self.assertEqual(api.post(root,headers=self.headers,json={'enabled':True}).status_code,400)
        scope=dict(approved=True,models=['any'],requests=1,input_tokens=1000,output_tokens=1000,messages=1,free_only_confirmed=True,request_id='free')
        self.assertEqual(api.post(root+'/grant',headers=self.headers,json=scope).status_code,400)
