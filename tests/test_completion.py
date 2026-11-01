"""Completion regressions: isolated real Chromium with mocked HTTPS, no live providers."""
import json
from pathlib import Path
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
import test_core_features as core
from email_agent.api.batch3 import register_batch3_routes
from email_agent.application.model_errors import ModelFailure,classify_error
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.infrastructure.http.website_browser import PlaywrightUnsubscribeBrowser,form_scope
from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
from scripts.core_browser_fixture import SiteTransport,OriginalReader

ROOT=Path(__file__).resolve().parents[1]

class CompletionTests(Base):
    def setUp(self):
        super().setUp();self.messages.initialize_discovery()
        for account in ('personal','university'):
            for case in ('customerio','constantcontact','cc-multi','ambiguous','visible-error'):
                self.add('web-'+case,'Newsletter discount sale',account)
                self.messages.record_list_headers(account,'web-'+case,{'list-id':'<'+case+'.website.example>'})
        self.transport=SiteTransport();self.browser=PlaywrightUnsubscribeBrowser(ROOT,self.transport,keep_sessions=True);self.addCleanup(self.browser.close)
        verifier=Mock();verifier.verify.side_effect=ValueError('No independent signature')
        self.identity=Mock(side_effect=lambda account:account+'@example.test')
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock(),unsubscribe_verifier=verifier,unsubscribe_browser=self.browser,unsubscribe_reader=OriginalReader(self.messages),unsubscribe_recipient=self.identity)
        self.api=TestClient(app,client=('127.0.0.1',1));self.addCleanup(self.api.close)
    def start(self,case):
        group=next(g for g in observed_subscriptions(self.messages,'personal') if 'web-'+case in g['message_ids'])
        preview=self.api.post('/api/subscriptions/personal/cancellation',headers=self.headers,json={'subscription_id':group['id']}).json()
        return self.confirm(preview).json()
    def confirm(self,preview,account='personal',approved=True):
        return self.api.post('/api/subscriptions/'+account+'/browser/'+preview['id']+'/confirm',headers=self.headers,json={'approved':approved})
    def resume(self,previous,**flags):
        response=self.api.post('/api/subscriptions/personal/browser/'+previous['id']+'/resume-preview',headers=self.headers,json=flags)
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def test_customerio_individual_topic_needs_exact_choice_no_broadening(self):
        paused=self.start('customerio');self.assertEqual(paused['result']['reason'],'preference_choice_required')
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),0)
        fields=paused['result']['page_state']['fields'];topic=next(f['id'] for f in fields if f['label']=='Newsletter emails')
        preview=self.resume(paused,topic_ids=[topic]);self.assertEqual(preview['payload']['approved_topic_labels'],['Newsletter emails'])
        self.assertEqual(self.confirm(preview,approved=False).status_code,400)
        value=self.confirm(preview).json();self.assertEqual(value['result']['state'],'requested',value)
        post=next(e for e in self.transport.events if e['method']=='POST')
        self.assertNotIn('newsletter',post['form']);self.assertIn('product',post['form']);self.assertNotIn('all',post['form'])
        self.confirm(preview);self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),1)

    def test_customerio_all_requires_separate_scoped_approval(self):
        paused=self.start('customerio');preview=self.resume(paused,allow_all=True)
        self.assertTrue(preview['payload']['allow_all']);self.assertIn('all marketing topics',preview['payload']['scope'])
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),0)
        result=self.confirm(preview).json();self.assertEqual(result['result']['state'],'cancelled',result)
        self.assertEqual(next(e for e in self.transport.events if e['method']=='POST')['form']['all'],['all'])
        audit=self.store.audit('personal',preview['id']);self.assertIn('website_final_request_started',[item['event'] for item in audit])

    def test_constant_contact_recipient_is_account_bound_and_explicit(self):
        paused=self.start('constantcontact');self.assertEqual(paused['result']['reason'],'recipient_consent_required');self.identity.assert_not_called()
        preview=self.resume(paused,submit_recipient=True);self.identity.assert_called_once_with('personal')
        self.assertEqual(preview['payload']['recipient_email'],'personal@example.test')
        self.assertEqual(self.confirm(preview,'university').status_code,404)
        result=self.confirm(preview).json();self.assertEqual(result['result']['state'],'cancelled',result)
        self.assertEqual(next(e for e in self.transport.events if e['method']=='POST')['form']['email'],['personal@example.test'])
        self.assertEqual(sum(e['method']=='GET' for e in self.transport.events),1) # Resume does not reopen a private link.

    def test_multi_step_email_then_confirmation(self):
        paused=self.start('cc-multi');preview=self.resume(paused,submit_recipient=True)
        result=self.confirm(preview).json();self.assertEqual(result['result']['state'],'cancelled',result)
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),2)
        self.assertTrue(any(e['path']=='/confirm' for e in self.transport.events))
        confirm_get=next(e for e in self.transport.events if e['path']=='/confirm')
        self.assertIn('unsubscribe-session=isolated',confirm_get['cookie']);self.assertIn('fixture-csrf=nonce',confirm_get['cookie'])

    def test_ambiguous_result_retains_page_readonly_no_duplicate(self):
        paused=self.start('ambiguous');self.assertEqual(paused['result']['state'],'needs_action');self.assertTrue(paused['result']['resumable'])
        self.assertIn('processed your selection',paused['result']['page_state']['excerpt'])
        self.assertTrue(paused['result']['inspect_only'])
        preview=self.resume(paused,inspect_only=True);after=self.confirm(preview).json();self.assertEqual(after['result']['state'],'needs_action')
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),1)
        bad=self.api.post('/api/subscriptions/personal/browser/'+after['id']+'/resume-preview',headers=self.headers,json={'allow_all':True})
        self.assertEqual(bad.status_code,400)

    def test_visible_error_is_exposed_and_not_submitted(self):
        paused=self.start('visible-error');self.assertEqual(paused['result']['reason'],'visible_form_error')
        self.assertIn('expired',paused['result']['page_state']['errors'][0]);self.assertFalse(any(e['method']=='POST' for e in self.transport.events))

    def test_expired_session_never_reopens_url(self):
        paused=self.start('constantcontact');closed=self.api.post('/api/subscriptions/personal/browser/'+paused['id']+'/close-session',headers=self.headers,json={'approved':True});self.assertEqual(closed.status_code,200);self.assertFalse(closed.json()['result']['resumable']);before=len(self.transport.events)
        response=self.api.post('/api/subscriptions/personal/browser/'+paused['id']+'/resume-preview',headers=self.headers,json={'submit_recipient':True})
        self.assertEqual(response.status_code,400);self.assertIn('expired',response.text);self.assertEqual(len(self.transport.events),before)

    def test_closed_pre_submission_form_requires_new_explicit_restart_preview(self):
        paused=self.start('constantcontact');self.browser.close();before=len(self.transport.events)
        preview=self.api.post('/api/subscriptions/personal/browser/'+paused['id']+'/recovery-preview',headers=self.headers,json={'approved':True})
        self.assertEqual(preview.status_code,200,preview.text);self.assertEqual(len(self.transport.events),before)
        self.assertNotIn('session_id',preview.json()['payload'])
        result=self.confirm(preview.json()).json();self.assertEqual(result['result']['reason'],'recipient_consent_required')
        self.assertFalse(any(e['method']=='POST' for e in self.transport.events))

    def test_form_scope_rejects_foreign_recipient_and_requires_all_opt_in(self):
        fields={'content-type':'application/x-www-form-urlencoded'}
        for body,kw in ((b'email=another%40example.test',{'recipient_email':'personal@example.test'}),(b'all=all',{}),(b'deleteAccount=1',{'allow_all':True})):
            with self.assertRaises(ValueError):form_scope('/preferences',fields,body,**kw)
        form_scope('/preferences',fields,b'email=personal%40example.test&all=all',allow_all=True,recipient_email='personal@example.test')


class ModelCompatibilityTests(Base):
    api=core.GeminiCoreTests.api
    enable=core.GeminiCoreTests.enable
    def test_model_list_only_on_explicit_request_and_no_generation(self):
        api=self.api();self.enable(api)
        self.model.list_models.return_value={'models':[{'id':'gemini-3.5-flash-lite','actions':['generateContent']}],'pages':1,'complete':True}
        path='/api/chat/personal/validate-model'
        self.assertEqual(api.post(path,headers=self.headers,json={'approved':False,'request_id':'metadata'}).status_code,400);self.model.list_models.assert_not_called()
        value=api.post(path,headers=self.headers,json={'approved':True,'request_id':'metadata'}).json()
        self.assertEqual(value['state'],'generate_content_supported');self.assertEqual(value['generation_requests'],0)
        self.assertTrue(api.post(path,headers=self.headers,json={'approved':True,'request_id':'metadata'}).json()['deduplicated'])
        self.model.list_models.assert_called_once();self.model.generate.assert_not_called()

    def test_unavailable_or_unsupported_model_not_claimed_compatible(self):
        api=self.api();self.enable(api)
        api.post('/api/chat/personal/model',headers=self.headers,json={'model':'gemini-3.8-flash','approved':True})
        for index,actions in enumerate(([],None)):
            self.model.list_models.return_value={'models':[{'id':'gemini-3.8-flash','actions':actions}] if actions is not None else [],'pages':1,'complete':True}
            result=api.post('/api/chat/personal/validate-model',headers=self.headers,json={'approved':True,'request_id':'invalid-'+str(index)}).json()
            self.assertEqual(result['state'],'generate_content_unsupported' if actions is not None else 'model_unavailable')
        self.assertEqual(api.get('/api/chat/personal/settings').json()['model'],'gemini-3.8-flash')

    def test_provider_404_auth_429_and_internal_parser_separated(self):
        api=self.api();self.enable(api)
        for index,(status,kind) in enumerate(((404,'model_unavailable'),(401,'authentication_failed'),(429,'provider_rate_limited'),(400,'provider_request_rejected'))):
            error=RuntimeError('model error key=AIzaPRIVATE https://example.test/?token=private');error.code=status;error.status='NOT_FOUND'
            self.model.generate.side_effect=error
            value=api.post('/api/chat/personal',headers=self.headers,json={'text':'Help with conversation '+str(index)}).json()
            self.assertEqual(value['reason_code'],kind);self.assertEqual(value['diagnostic']['http_status'],status)
            self.assertEqual(value['diagnostic']['provider_code'],status);self.assertNotIn('AIza',json.dumps(value));self.assertNotIn('token=private',json.dumps(value))
        self.model.generate.side_effect=None;self.model.generate.return_value='not JSON'
        malformed=api.post('/api/chat/personal',headers=self.headers,json={'text':'A new request for invalid output'}).json();self.assertEqual(malformed['reason_code'],'malformed_output')
        self.model.generate.side_effect=TypeError('Internal response mapping error')
        harness=api.post('/api/chat/personal',headers=self.headers,json={'text':'A different request for harness failure'}).json();self.assertEqual(harness['reason_code'],'harness_error')

    def test_confirmed_return_to_exact_last_working_model_preserves_free(self):
        api=self.api();self.enable(api)
        api.post('/api/chat/personal',headers=self.headers,json={'text':'Hello verified model'})
        api.post('/api/chat/personal/model',headers=self.headers,json={'model':'gemini-3.8-flash','approved':True})
        path='/api/chat/personal/return-verified-model'
        self.assertEqual(api.post(path,headers=self.headers,json={'approved':False,'model':'gemini-3.5-flash-lite'}).status_code,400)
        self.assertEqual(api.post(path,headers=self.headers,json={'approved':True,'model':'gemini-other'}).status_code,400)
        value=api.post(path,headers=self.headers,json={'approved':True,'model':'gemini-3.5-flash-lite'}).json()
        self.assertEqual(value['model'],'gemini-3.5-flash-lite');self.assertTrue(value['usage']['provider_policy']['free_tier_attested']);self.assertFalse(value['usage']['provider_policy']['paid_approved'])
        self.model.generate.assert_called_once()

    def test_plain_probe_does_not_replace_last_working_chat_model(self):
        api=self.api();self.enable(api)
        api.post('/api/chat/personal',headers=self.headers,json={'text':'Establish successful structured conversation'})
        api.post('/api/chat/personal/model',headers=self.headers,json={'model':'gemini-3.8-flash','approved':True})
        api.post('/api/chat/personal/connection-test',headers=self.headers,json={'approved':True,'request_id':'plain-probe'})
        value=api.get('/api/chat/personal/settings').json()
        self.assertEqual(value['connection']['last_verified']['model'],'gemini-3.5-flash-lite')
        self.assertEqual(value['connection']['last_verified']['source'],'chat_request')

    def test_non_http_sdk_code_and_http_status_are_both_preserved(self):
        error=RuntimeError('Project model unavailable');error.code='NOT_FOUND';error.status_code=404
        failure=classify_error(error)
        self.assertEqual(failure.state,'model_unavailable');self.assertEqual(failure.code,'NOT_FOUND');self.assertEqual(failure.http_status,404)

    def test_sdk_model_list_pagination_is_bounded_and_uses_supported_actions(self):
        model=GeminiTextModel.__new__(GeminiTextModel);model._client=Mock()
        item=Mock();item.name='models/gemini-3.5-flash-lite';item.supported_actions=['generateContent']
        pager=Mock();pager.page=[item];pager.config={'page_token':'more'};model._client.models.list.return_value=pager
        reserve=Mock(return_value=True);result=model.list_models(reserve)
        self.assertEqual(result['pages'],3);self.assertFalse(result['complete']);self.assertEqual(pager.next_page.call_count,2);self.assertEqual(reserve.call_count,3)
        self.assertEqual(result['models'][0]['actions'],['generateContent']);model._client.models.generate_content.assert_not_called()
