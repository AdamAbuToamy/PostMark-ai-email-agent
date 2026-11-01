"""Probe-specific failure and lock boundaries, no external providers."""
import json
import unittest
from unittest.mock import patch,Mock,MagicMock
from dataclasses import replace
import test_multi_provider as fixture
from email_agent.application.hybrid_ai import ModelCapability,InferenceResult
from email_agent.application.semantic_mail import SCHEMA
from email_agent.application.model_errors import ModelFailure
from email_agent.infrastructure.ai.hybrid_providers import OpenAICompatibleProvider,JSONTransport
from email_agent.workers.fill_content import fill_lock
from email_agent.infrastructure.gmail.request_policy import sync_lock

class ProbeTests(unittest.TestCase):
    setUp=fixture.MultiProviderAPITests.setUp
    add=fixture.MultiProviderAPITests.add
    api=fixture.MultiProviderAPITests.api
    setup_mistral=fixture.MultiProviderAPITests.setup_mistral
    def test_synthetic_probe_needs_no_gmail_locks_and_reports_real_completion(self):
        api,store=self.setup_mistral()
        def response(provider,model,system,data,schema,**kwargs):
            frame=dict(id='m0',category_id='bills',primary_intent='Payment receipt',message_type='Receipt',status='Paid',entity=None,sensitivities=['financial'],confidence=.95,evidence=[dict(field='excerpt',quote=data['messages'][0]['excerpt'][:120])],attributes=[],related_ids=[])
            return InferenceResult(json.dumps(dict(frames=[frame],changes=[])),model.provider,model.model,{},.01)
        with fill_lock(self.root/'data'),sync_lock('personal',self.root/'data'),patch.object(OpenAICompatibleProvider,'analyze',response):
            r=api.post('/api/hybrid/personal/providers/mistral/probe',headers=self.headers,json=dict(approved=True,model='opaque-chat',free_account_confirmed=True,request_id='mail-locks-held'))
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['inference_completed']);self.assertTrue(r.json()['passed']);self.assertFalse(r.json()['quality_qualified'])
    def test_configuration_failure_is_not_key_or_metadata_failure_and_is_durable(self):
        api,store=self.setup_mistral()
        error=ModelFailure('provider_request_rejected','Provider rejected configuration',400);error.safe_context={'param':'response_format'}
        with patch.object(OpenAICompatibleProvider,'analyze',side_effect=error) as call:
            r=api.post('/api/hybrid/personal/providers/mistral/probe',headers=self.headers,json=dict(approved=True,model='opaque-chat',free_account_confirmed=True,request_id='bad-schema'))
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['diagnostic']['http_status'],400);self.assertEqual(r.json()['requests'],1);self.assertFalse(r.json()['inference_completed']);call.assert_called_once()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT disabled FROM hybrid_health WHERE provider=\'mistral\'').fetchone()[0],0)
            self.assertIn('response_format',db.execute('SELECT payload FROM hybrid_attempt_diagnostics').fetchone()[0])
        self.assertEqual(api.get('/api/hybrid/university/tests').json()['tests'],[])
    def test_generated_invalid_json_is_a_validation_failure_not_connection_failure(self):
        api,store=self.setup_mistral()
        with patch.object(OpenAICompatibleProvider,'analyze',return_value=InferenceResult('invalid-json','mistral','opaque-chat',{},.01)):
            r=api.post('/api/hybrid/personal/providers/mistral/probe',headers=self.headers,json=dict(approved=True,model='opaque-chat',free_account_confirmed=True,request_id='invalid-json'))
        self.assertTrue(r.json()['inference_completed']);self.assertFalse(r.json()['structured_valid']);self.assertEqual(r.json()['reason'],'semantic_validation_failed')

    def test_quality_evaluation_uses_public_catalog_without_mailbox_consent(self):
        api,store=self.setup_mistral()
        with patch.object(OpenAICompatibleProvider,'analyze',side_effect=ModelFailure('provider_unavailable','Fixture503',503)) as call:
            result=api.post('/api/hybrid/personal/providers/mistral/evaluate',headers=self.headers,json=dict(approved=True,model='opaque-chat',free_account_confirmed=True,request_id='public-catalog'))
        self.assertEqual(result.status_code,200,result.text)
        catalog=call.call_args.args[2]['catalog']
        self.assertIn('security',{c['id'] for c in catalog});self.assertTrue(all('observed_types' not in c for c in catalog))

class AdapterTests(unittest.TestCase):
    def test_groq_uses_vendor_schema_subset_without_relaxing_local_schema(self):
        p=OpenAICompatibleProvider('groq','fixture-private');p.http=Mock();p.http.request.return_value={'model':'exact','choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
        p.analyze(ModelCapability('groq','exact',64000),'System',{'messages':[{'id':'m0'}],'catalog':[]},SCHEMA)
        payload=p.http.request.call_args.args[1]
        self.assertIn('max_completion_tokens',payload);self.assertNotIn('uniqueItems',json.dumps(payload['response_format']));self.assertNotIn('maxItems',json.dumps(payload['response_format']));self.assertIn('maxItems',json.dumps(SCHEMA))
    def test_error_diagnostics_never_echo_prompt_or_credentials(self):
        response=MagicMock();response.__enter__.return_value=response;response.status_code=400;response.headers={};response.iter_content.return_value=iter([json.dumps({'error':{'type':'invalid_request_error','param':'response_format','message':'Secret body fixture-private uniqueItems is unsupported'}}).encode()])
        session=MagicMock();session.__enter__.return_value=session;session.request.return_value=response
        with patch('requests.Session',return_value=session):
            with self.assertRaises(ModelFailure) as caught:JSONTransport('https://api.groq.com','fixture-private').request('/v1',{})
        d=json.dumps(caught.exception.diagnostic());self.assertIn('uniqueItems',d);self.assertNotIn('Secret body',d);self.assertNotIn('fixture-private',d)
