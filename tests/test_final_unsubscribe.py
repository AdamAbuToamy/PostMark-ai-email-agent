"""Simple cancellation and explicit external-browser fallback; mocked sites only."""
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
import test_completion as completion
from email_agent.api.batch3 import register_batch3_routes
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.infrastructure.http.website_browser import PlaywrightUnsubscribeBrowser
from scripts.core_browser_fixture import SiteTransport,OriginalReader

class FinalUnsubscribeTests(Base):
    confirm=completion.CompletionTests.confirm
    resume=completion.CompletionTests.resume
    def setUp(self):
        super().setUp();self.messages.initialize_discovery()
        for case in ('or-direct','or-stage','or-explainer','or-completed','or-uncertain','captcha','login','redirect','unsafe','constantcontact'):
            self.add('web-'+case,'Newsletter discount sale')
            self.messages.record_list_headers('personal','web-'+case,{'list-id':'<'+case+'.website.example>'})
        self.transport=SiteTransport();self.browser=PlaywrightUnsubscribeBrowser(completion.ROOT,self.transport,keep_sessions=True);self.addCleanup(self.browser.close)
        self.identity=Mock(return_value='personal@example.test');verifier=Mock();verifier.verify.side_effect=ValueError('No independent signature')
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock(),unsubscribe_verifier=verifier,unsubscribe_browser=self.browser,unsubscribe_reader=OriginalReader(self.messages),unsubscribe_recipient=self.identity,manual_validator=self.transport.validate)
        self.api=TestClient(app,client=('127.0.0.1',1));self.addCleanup(self.api.close)
    def group(self,case):return next(g for g in observed_subscriptions(self.messages,'personal') if 'web-'+case in g['message_ids'])
    def start(self,case):
        preview=self.api.post('/api/subscriptions/personal/cancellation',headers=self.headers,json={'subscription_id':self.group(case)['id']}).json()
        value=self.api.post('/api/subscriptions/personal/browser/'+preview['id']+'/confirm-flow',headers=self.headers,json={'approved':True})
        self.assertEqual(value.status_code,200,value.text);return value.json()

    def test_exact_openrouter_question_and_named_final_button_complete_once(self):
        value=self.start('or-direct');self.assertEqual(value['flow']['kind'],'unsubscribed',value)
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),1)
        self.assertNotIn('welcome',next(e for e in self.transport.events if e['method']=='POST')['form'])
        self.confirm(value);self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),1);self.identity.assert_not_called()

    def test_intermediate_post_is_not_mistaken_for_final_cancellation(self):
        value=self.start('or-stage');self.assertEqual(value['flow']['kind'],'unsubscribed',value)
        self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),2)
        events=self.store.audit('personal',value['id'])
        self.assertEqual(sum(e['event']=='website_final_request_started' for e in events),1)
        self.assertEqual(sum(e['event']=='website_step_request_started' for e in events),1)

    def test_explanatory_text_alone_is_never_success(self):
        value=self.start('or-explainer');self.assertNotEqual(value['result']['state'],'cancelled');self.assertFalse(value['result']['submitted'])
        self.assertEqual(value['flow']['kind'],'manual');self.assertFalse(any(e['method']=='POST' for e in self.transport.events))

    def test_completed_page_is_recognized_without_new_submission(self):
        value=self.start('or-completed');self.assertEqual(value['flow']['kind'],'unsubscribed');self.assertFalse(any(e['method']=='POST' for e in self.transport.events))

    def test_unchanged_final_question_after_post_never_duplicates(self):
        value=self.start('or-uncertain');self.assertEqual(value['flow']['kind'],'pending',value)
        self.assertTrue(value['result']['submitted']);self.assertTrue(value['result']['inspect_only'])
        preview=self.resume(value,inspect_only=True);result=self.confirm(preview).json()
        self.assertEqual(result['result']['state'],'needs_action');self.assertEqual(sum(e['method']=='POST' for e in self.transport.events),1)

    def test_captcha_login_manual_fallback_is_not_a_fake_session_transfer(self):
        for case in ('captcha','login'):
            with self.subTest(case=case):
                value=self.start(case);self.assertEqual(value['flow']['kind'],'challenge')
                before=len(self.transport.events)
                response=self.api.post('/api/subscriptions/personal/'+self.group(case)['id']+'/manual-page',headers=self.headers,json={'action_id':value['id']})
                self.assertEqual(response.status_code,200,response.text)
                self.assertFalse(response.json()['same_session']);self.assertFalse(response.json()['can_resume'])
                self.assertIn('separate session',response.json()['message']);self.assertEqual(len(self.transport.events),before)
                with self.assertRaises(ValueError):self.browser.state(value['result']['session_id'],'personal')
                # There is no Continue after external transfer; the API rejects resume too.
                resume=self.api.post('/api/subscriptions/personal/browser/'+value['id']+'/resume-preview',headers=self.headers,json={})
                self.assertEqual(resume.status_code,400)

    def test_manual_handoff_account_and_list_must_match(self):
        value=self.start('captcha');before=len(self.transport.events)
        for path in ('/api/subscriptions/university/'+self.group('captcha')['id']+'/manual-page','/api/subscriptions/personal/'+self.group('login')['id']+'/manual-page'):
            response=self.api.post(path,headers=self.headers,json={'action_id':value['id']});self.assertIn(response.status_code,(400,404))
        self.assertEqual(len(self.transport.events),before);self.browser.state(value['result']['session_id'],'personal')

    def test_unknown_network_submission_is_not_reported_sent_or_retried(self):
        original=self.transport.fetch;attempts=[]
        def fetch(url,method,headers,body):
            if method=='POST':attempts.append(url);raise TimeoutError('No response; write outcome unknown')
            return original(url,method,headers,body)
        self.transport.fetch=fetch
        value=self.start('or-direct');self.assertFalse(value['result']['submitted']);self.assertTrue(value['result']['submission_attempted'])
        self.assertEqual(value['flow']['kind'],'manual');self.assertTrue(value['result']['inspect_only'])
        response=self.api.post('/api/subscriptions/personal/browser/'+value['id']+'/resume-preview',headers=self.headers,json={'inspect_only':True})
        self.assertEqual(response.status_code,400) # Failed navigation has no safe retained page.
        self.assertEqual(len(attempts),1)

    def test_dismiss_releases_local_session_without_a_website_request(self):
        value=self.start('or-explainer');before=len(self.transport.events)
        response=self.api.post('/api/subscriptions/personal/browser/'+value['id']+'/dismiss-flow',headers=self.headers,json={'approved':True})
        self.assertEqual(response.status_code,200);self.assertEqual(len(self.transport.events),before)
        with self.assertRaises(ValueError):self.browser.state(value['result']['session_id'],'personal')

    def test_manual_first_click_keeps_selected_list_identity_and_does_not_visit(self):
        group=self.group('or-direct');before=group['id']
        response=self.api.post('/api/subscriptions/personal/'+before+'/manual-page',headers=self.headers,json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.group('or-direct')['id'],before);self.assertEqual(self.transport.events,[])
        self.identity.assert_not_called();self.assertFalse(response.json()['can_resume'])

    def test_email_needs_explicit_review_then_submission_consent(self):
        value=self.start('constantcontact');self.assertEqual(value['flow']['kind'],'email');self.identity.assert_not_called()
        self.assertFalse(any(e['method']=='POST' for e in self.transport.events))
        preview=self.resume(value,submit_recipient=True);self.assertEqual(preview['payload']['recipient_email'],'personal@example.test')
        self.assertFalse(any(e['method']=='POST' for e in self.transport.events))
        result=self.confirm(preview).json();self.assertEqual(result['result']['state'],'cancelled')

    def test_unsafe_destination_never_navigated(self):
        value=self.api.post('/api/subscriptions/personal/cancellation',headers=self.headers,json={'subscription_id':self.group('unsafe')['id']}).json()
        self.assertEqual(value['status'],'needs_action');self.assertEqual(self.transport.events,[])
