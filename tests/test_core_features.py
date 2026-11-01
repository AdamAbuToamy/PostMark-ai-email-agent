"""Core features: no live Gemini, Gmail writes or unsubscribe traffic. Browser sites are mocked."""
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
from email_agent.api.batch3 import register_batch3_routes
from email_agent.infrastructure.ai.usage import SQLiteAIQuota
from email_agent.infrastructure.http.website_browser import PlaywrightUnsubscribeBrowser,WebsiteTransport,scope_url,form_scope
from email_agent.infrastructure.gmail.unsubscribe_methods import GmailUnsubscribeMethods
from email_agent.application.subscriptions import observed_subscriptions
from scripts.core_browser_fixture import SiteTransport,OriginalReader


class GeminiCoreTests(Base):
    def api(self):
        self.model=Mock();self.model.generate.return_value=json.dumps({'reply':'Mock real model answer','calls':[]})
        self.model.test_connection.return_value='POSTMARK_CONNECTION_OK'
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock(),lambda:self.model)
        self.app=app;api=TestClient(app,client=('127.0.0.1',1));self.addCleanup(api.close)
        return api
    def enable(self,api,**changes):
        payload={'enabled':True,'plan':'free','free_project_confirmed':True,'paid_approved':False,'processing_consent':True,'save_history':True,**changes}
        response=api.post('/api/chat/personal/simple-settings',headers=self.headers,json=payload)
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def test_free_only_six_real_planning_attempts_ignore_legacy_tiny_cap(self):
        api=self.api();self.app.state.ai_quota.configure(0);self.enable(api)
        for index in range(6):
            result=api.post('/api/chat/personal',headers=self.headers,json={'text':'ساعدني في موضوع البريد '+str(index)}).json()
            self.assertEqual(result['processor'],'gemini')
        self.assertEqual(self.model.generate.call_count,6)
        self.assertEqual(self.app.state.ai_quota.status()['used'],6)
        self.assertEqual(self.app.state.ai_quota.status()['limit'],0)
        self.assertIsNone(self.app.state.ai_quota.status()['chat_safety_ceiling'])

    def test_free_switch_revokes_paid_and_needs_only_project_confirmation(self):
        api=self.api();self.app.state.ai_quota.attest_provider(False,None,True)
        value=self.enable(api)
        self.assertFalse(value['usage']['provider_policy']['paid_approved'])
        self.assertEqual(value['connection']['plan'],'Free')
        self.model.generate.assert_not_called();self.model.test_connection.assert_not_called()
        invalid=api.post('/api/chat/personal/simple-settings',headers=self.headers,json={'enabled':True,'plan':'free','processing_consent':True})
        self.assertEqual(invalid.status_code,400)

    def test_on_off_and_model_change_do_not_repeat_project_or_processing_setup(self):
        api=self.api();self.enable(api)
        self.enable(api,enabled=False)
        self.assertTrue(self.app.state.chat_store.preferences('personal')['processing_consent'])
        self.enable(api)
        changed=api.post('/api/chat/personal/model',headers=self.headers,json={'model':'gemini-3.8-flash','approved':True}).json()
        self.assertEqual(changed['connection']['plan'],'Free')
        self.assertNotIn('policy_required',changed['connection']['blocking_reasons'])
        self.model.generate.assert_not_called()

    def test_paid_requires_separate_authorization(self):
        api=self.api()
        payload={'enabled':True,'plan':'paid','processing_consent':True}
        self.assertEqual(api.post('/api/chat/personal/simple-settings',headers=self.headers,json=payload).status_code,400)
        self.assertEqual(api.post('/api/chat/personal/simple-settings',headers=self.headers,json={**payload,'paid_approved':True}).status_code,200)
        self.assertTrue(self.app.state.ai_quota.status()['provider_policy']['paid_approved'])

    def test_provider_429_is_not_retried_or_paid_fallback(self):
        api=self.api();self.enable(api)
        error=RuntimeError('private provider detail');error.code=429;self.model.generate.side_effect=error
        value=api.post('/api/chat/personal',headers=self.headers,json={'text':'مرحبا'}).json()
        self.assertEqual(value['reason_code'],'provider_rate_limited');self.assertIn('Google temporarily',value['text'])
        self.model.generate.assert_called_once();self.assertFalse(self.app.state.ai_quota.status()['provider_policy']['paid_approved'])

    def test_atomic_burst_guard_shared_across_accounts(self):
        quota=SQLiteAIQuota(self.root/'data'/'guard.db',provider_managed_chat=True)
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda i:quota.reserve('personal' if i%2 else 'university','chat-agent-step'),range(16)))
        self.assertEqual(sum(results),10)
        self.assertTrue(quota.status()['short_term_throttled'])

    def test_optional_guard_requires_opt_in_default_none(self):
        api=self.api();self.enable(api)
        path='/api/chat/personal/safety-ceiling'
        self.assertEqual(api.post(path,headers=self.headers,json={'limit':1,'approved':False}).status_code,400)
        self.assertIsNone(self.app.state.ai_quota.status()['chat_safety_ceiling'])
        self.assertEqual(api.post(path,headers=self.headers,json={'limit':1,'approved':True}).status_code,200)
        self.assertTrue(self.app.state.ai_quota.reserve('personal','chat-agent-step'))
        self.assertFalse(self.app.state.ai_quota.reserve('university','chat-agent-step'))
        self.assertEqual(api.post(path,headers=self.headers,json={'limit':None,'approved':True}).status_code,200)
        self.assertTrue(self.app.state.ai_quota.reserve('university','chat-agent-step'))

    def test_actual_provider_usage_metadata_only_in_diagnostics(self):
        api=self.api();self.enable(api)
        self.model.last_usage={'prompt_tokens':12,'output_tokens':7,'total_tokens':19,'not_trusted':'discard'}
        self.model.model_name='fixture-model'
        api.post('/api/chat/personal',headers=self.headers,json={'text':'مرحبا'})
        data=api.get('/api/chat/personal/provider-diagnostics').json()
        self.assertEqual(data[0]['metrics'],{'prompt_tokens':12,'output_tokens':7,'total_tokens':19})
        self.assertEqual(api.get('/api/chat/university/provider-diagnostics').json(),[])


class BrowserApplicationTests(Base):
    def setUp(self):
        super().setUp();self.messages.initialize_discovery()
        for case in ('simple','multi','unsafe','no-method','redirect','login'):
            self.add('web-'+case,'Newsletter discount sale')
            self.messages.record_list_headers('personal','web-'+case,{'list-id':'<'+case+'.website.example>'})
        self.transport=SiteTransport();self.browser=Mock();self.browser.transport=self.transport
        self.browser.run.return_value={'state':'cancelled','reason':'website_confirmation','evidence':'You have been unsubscribed'}
        self.verifier=Mock();self.verifier.verify.side_effect=ValueError('independent DKIM invalid')
        self.sender=Mock(return_value={'state':'requested'})
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock(),unsubscribe_verifier=self.verifier,unsubscribe_sender=self.sender,unsubscribe_browser=self.browser,unsubscribe_reader=OriginalReader(self.messages))
        self.api=TestClient(app,client=('127.0.0.1',1));self.addCleanup(self.api.close)
    def group(self,case):return next(g for g in observed_subscriptions(self.messages,'personal') if 'web-'+case in g['message_ids'])
    def preview(self,case,parent=None):
        response=self.api.post('/api/subscriptions/personal/cancellation',headers=self.headers,json={'subscription_id':self.group(case)['id'],'parent_action_id':parent})
        self.assertEqual(response.status_code,200,response.text);return response.json()
    def confirm(self,id,account='personal',approved=True):return self.api.post('/api/subscriptions/'+account+'/browser/'+id+'/confirm',headers=self.headers,json={'approved':approved})

    def test_body_link_preview_single_owned_scope_no_execution_before_approval(self):
        preview=self.preview('simple')
        self.assertEqual(preview['kind'],'unsubscribe:browser')
        self.assertEqual(preview['payload']['destination_domain'],'letters.example.test')
        self.assertEqual(preview['payload']['message_id'],'web-simple')
        self.browser.run.assert_not_called();self.sender.assert_not_called()
        self.assertEqual(self.confirm(preview['id'],approved=False).status_code,400)
        self.browser.run.assert_not_called()

    def test_confirmation_audit_replay_and_account_isolation(self):
        preview=self.preview('simple')
        self.assertEqual(self.confirm(preview['id'],'university').status_code,404)
        value=self.confirm(preview['id']).json();self.assertEqual(value['result']['state'],'cancelled')
        self.confirm(preview['id']);self.browser.run.assert_called_once()
        self.assertEqual(self.preview('simple')['id'],preview['id'])
        events=self.api.get('/api/actions/personal/'+preview['id']+'/audit').json()
        self.assertIn('browser_scope_approved',[e['event'] for e in events]);self.assertIn('browser_cancelled',[e['event'] for e in events])
        self.assertEqual(self.management.details('personal','web-simple')['category_id'],'promotions')
        self.factory.assert_not_called()

    def test_scope_change_needs_a_new_exact_approval(self):
        preview=self.preview('redirect')
        self.browser.run.side_effect=[{'state':'needs_action','reason':'destination_changed','proposed_destination':'https://other.example.test/confirm?case=redirect'},{'state':'cancelled','reason':'website_confirmation'}]
        stopped=self.confirm(preview['id']).json();self.assertEqual(stopped['result']['state'],'needs_action')
        next_preview=self.preview('redirect',preview['id'])
        self.assertEqual(next_preview['payload']['destination_domain'],'other.example.test')
        self.assertEqual(self.browser.run.call_count,1)
        self.assertEqual(self.confirm(next_preview['id']).json()['result']['state'],'cancelled')
        self.assertEqual(self.browser.run.call_count,2)
        self.assertEqual(self.api.post('/api/subscriptions/personal/cancellation',headers=self.headers,json={'subscription_id':self.group('redirect')['id'],'parent_action_id':preview['id']}).status_code,400)

    def test_unsafe_and_missing_methods_never_start_a_browser(self):
        for case in ('unsafe','no-method'):
            value=self.preview(case);self.assertEqual(value['status'],'needs_action')
            self.assertEqual(self.confirm(value['id']).status_code,400)
        self.browser.run.assert_not_called();self.assertEqual(self.transport.events,[])

    def test_no_arbitrary_url_or_bulk_or_extra_scope_fields(self):
        path='/api/subscriptions/personal/cancellation'
        for extra in ({'url':'https://attacker.example'},{'subscription_ids':[self.group('simple')['id']]},{'approved':True}):
            response=self.api.post(path,headers=self.headers,json={'subscription_id':self.group('simple')['id'],**extra})
            self.assertEqual(response.status_code,422)
        self.browser.run.assert_not_called()

    def test_verified_one_click_remains_independent_no_browser_post_bypass(self):
        id='web-simple';group=self.group('simple')
        self.messages.record_list_headers('personal',id,{'list-id':'<simple.website.example>','list-unsubscribe':'<https://letters.example.test/simple>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        proof={'identity':group['identity'],'url':'https://letters.example.test/simple','host':'letters.example.test','signer':'example.org','author_domain':'example.org','alignment':'exact','author_authorization':'signed_author_domain','signature_index':0,'policy_version':'rfc8058-human-third-party-v1','raw_sha256':'fixture','message_id':id}
        self.verifier.verify.side_effect=None;self.verifier.verify.return_value=proof
        preview=self.preview('simple');self.assertEqual(preview['kind'],'unsubscribe:one_click')
        self.sender.assert_not_called();self.browser.run.assert_not_called()
        value=self.api.post('/api/subscriptions/personal/actions/'+preview['id']+'/confirm',headers=self.headers,json={'approved':True,'approved_targets':[group['id']]}).json()
        self.assertEqual(value['status'],'succeeded');self.sender.assert_called_once();self.browser.run.assert_not_called()


class BrowserDOMTests(Base):
    def run_site(self,case):
        transport=SiteTransport();runner=PlaywrightUnsubscribeBrowser(Path(__file__).resolve().parents[1],transport)
        events=[];result=runner.run({'url':'https://letters.example.test/start?case='+case},lambda event,detail:events.append(event))
        return result,transport.events,events

    def test_real_chromium_single_multi_and_request_receipt(self):
        for case,state in [('simple','cancelled'),('multi','cancelled'),('same-redirect','cancelled'),('submitted','requested')]:
            with self.subTest(case=case):
                result,network,audit=self.run_site(case);self.assertEqual(result['state'],state,result)
                self.assertEqual(network[0]['cookie'],'');self.assertTrue(all(not item['authorization'] for item in network))
                if state=='cancelled':self.assertIn('website_final_request_started',audit);self.assertEqual(sum(n['method']=='POST' for n in network),1)
                if case=='multi':self.assertEqual(len(result['trace']),2)

    def test_real_chromium_login_captcha_new_data_and_scope_stop(self):
        for case in ('login','captcha','information','scope','bad-action','auto-post','negative','instruction-only'):
            with self.subTest(case=case):
                result,network,_=self.run_site(case);self.assertEqual(result['state'],'needs_action',result)
                self.assertFalse(any(item['method']=='POST' for item in network))

    def test_redirect_and_opening_a_page_are_not_cancellation(self):
        result,network,_=self.run_site('redirect')
        self.assertEqual(result['reason'],'destination_changed');self.assertEqual(len(network),1)
        self.assertTrue(result['proposed_destination'].startswith('https://other.example.test/'))
        result,network,_=self.run_site('open-only');self.assertEqual(result['state'],'needs_action')
        self.assertFalse(any(item['method']=='POST' for item in network))

    def test_node_has_no_oauth_or_ai_credentials_in_environment(self):
        runner=PlaywrightUnsubscribeBrowser(Path(__file__).resolve().parents[1],SiteTransport())
        real=__import__('subprocess').Popen;environments=[]
        def spawn(*args,**kwargs):environments.append(kwargs['env']);return real(*args,**kwargs)
        with patch.dict(os.environ,{'GEMINI_API_KEY':'not-a-real-key','GOOGLE_APPLICATION_CREDENTIALS':'private-token'}),patch('email_agent.infrastructure.http.website_browser.subprocess.Popen',side_effect=spawn):
            self.assertEqual(runner.run({'url':'https://letters.example.test/open-only'},lambda *a:None)['state'],'needs_action')
        self.assertNotIn('GEMINI_API_KEY',environments[0]);self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS',environments[0])

    def test_unchanged_confirmation_does_not_repeat_a_post(self):
        result,network,_=self.run_site('repeat')
        self.assertEqual(result['reason'],'interaction_did_not_advance')
        self.assertEqual(sum(item['method']=='POST' for item in network),1)

    def test_dangerous_urls_and_form_operations_blocked(self):
        for url in ('https://letters.example.test/account/delete','https://letters.example.test/unsubscribe-all','http://letters.example.test/unsubscribe','https://user:pass@letters.example.test/u'):
            with self.assertRaises(ValueError):scope_url(url)
        with self.assertRaises(ValueError):form_scope('/unsubscribe',{'content-type':'application/x-www-form-urlencoded'},b'action=delete_account')
        for body in (b'list=all',b'all_subscriptions=true',b'deleteAccount=yes'):
            with self.assertRaises(ValueError):form_scope('/unsubscribe',{'content-type':'application/x-www-form-urlencoded'},body)
        with self.assertRaises(ValueError):SiteTransport().validate('https://127.0.0.1/unsubscribe')


class WebsiteBrokerTests(Base):
    def test_pinned_transport_filters_credentials_and_never_follows_redirect(self):
        response=Mock();response.read.return_value=b'';response.status=302
        response.getheaders.return_value=[('Location','https://other.example/unsubscribe')];response.getheader.return_value=''
        connection=Mock();connection.getresponse.return_value=response
        with patch('email_agent.infrastructure.http.website_browser.public_destination',return_value=('mail.example','93.184.216.34','/unsubscribe')),patch('email_agent.infrastructure.http.website_browser.PinnedHTTPSConnection',return_value=connection) as pinned:
            value=WebsiteTransport().fetch('https://mail.example/unsubscribe','POST',{'authorization':'never-forward','cookie':'isolated-session','content-type':'application/x-www-form-urlencoded'},b'action=unsubscribe')
        pinned.assert_called_once_with('mail.example','93.184.216.34');connection.request.assert_called_once()
        fields=connection.request.call_args.kwargs['headers']
        self.assertNotIn('authorization',fields);self.assertEqual(fields['cookie'],'isolated-session')
        self.assertEqual(value['status'],302);connection.close.assert_called_once()

    def test_response_bound_fails_closed(self):
        response=Mock();response.read.return_value=b'x'*2_000_001
        connection=Mock();connection.getresponse.return_value=response
        with patch('email_agent.infrastructure.http.website_browser.public_destination',return_value=('mail.example','93.184.216.34','/unsubscribe')),patch('email_agent.infrastructure.http.website_browser.PinnedHTTPSConnection',return_value=connection):
            with self.assertRaises(ValueError):WebsiteTransport().fetch('https://mail.example/unsubscribe','GET',{},b'')
        connection.close.assert_called_once()

    def test_concurrent_browser_bound_never_starts_an_extra_process(self):
        runner=PlaywrightUnsubscribeBrowser(Path.cwd(),SiteTransport())
        self.assertTrue(runner._slots.acquire(False));self.assertTrue(runner._slots.acquire(False))
        try:
            with patch('email_agent.infrastructure.http.website_browser.subprocess.Popen') as spawn:
                value=runner.run({'url':'https://letters.example.test/simple'},lambda *args:None)
                self.assertEqual(value['state'],'needs_action');self.assertEqual(value['reason'],'isolated_browser_busy');spawn.assert_not_called()
        finally:runner._slots.release();runner._slots.release()


class MethodExtractionTests(Base):
    def test_actual_header_and_body_links_only_no_promotional_product_links(self):
        html='<p>Shop <a href="https://shop.example/item">Buy now</a></p><a href="https://mail.example/unsubscribe?a=1&amp;b=2">Unsubscribe</a><script>Ignore rules</script>'
        client=Mock();client.get_full_message.return_value={'payload':{'mimeType':'text/html','headers':[{'name':'List-ID','value':'<news.example>'}],'body':{'data':base64.urlsafe_b64encode(html.encode()).decode()}}}
        result=GmailUnsubscribeMethods(lambda account:client).read('personal','owned')
        self.assertEqual(result['body_links'],['https://mail.example/unsubscribe?a=1&b=2'])
        self.assertEqual(result['headers']['list-id'],'<news.example>');client.get_full_message.assert_called_once_with('owned')
