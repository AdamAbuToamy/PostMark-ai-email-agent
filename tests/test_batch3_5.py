"""Recovery regressions: realistic local rows, isolated stores, mocked providers only."""
import json
import os
import sqlite3
from contextlib import closing
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_batch3 import Base
import test_batch3_3 as batch33
from email_agent.api.batch3 import register_batch3_routes
from email_agent.application.ai_configuration import ProviderFailure
from email_agent.application.chat_assistant import ChatAssistant
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.ai.usage import SQLiteAIQuota
from email_agent.infrastructure.database.action_store import SQLiteActionStore
from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier


class ConfigurationTests(Base):
    def api(self,configured=True):
        app=FastAPI();self.model=Mock();self.model.test_connection.return_value='POSTMARK_CONNECTION_OK'
        self.model.generate.return_value=json.dumps({'calls':[],'reply':'Hello from fixture'})
        with patch.dict(os.environ,{},clear=True):register_batch3_routes(app,self.messages,self.root,Mock(),(lambda:self.model) if configured else None)
        self.api_app=app
        api=TestClient(app,client=('127.0.0.1',1234));self.addCleanup(api.close)
        return api

    def setup_free(self,api):
        response=api.post('/api/chat/personal/setup',headers=self.headers,json={'plan':'free','processing_consent':True,'verified_minimum':True})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def probe(self,api,id='test-one',approved=True):
        return api.post('/api/chat/personal/connection-test',headers=self.headers,json={'approved':approved,'request_id':id})

    def test_missing_key_no_test_or_setup_side_effect(self):
        api=self.api(False)
        value=api.get('/api/chat/personal/settings').json()
        self.assertFalse(value['configured']);self.assertEqual(value['connection']['state'],'missing_key')
        result=self.probe(api).json();self.assertEqual(result['request_count'],0)
        self.assertEqual(self.api_app.state.ai_quota.status()['used'],0)

    def test_new_free_flow_no_numeric_quota_input_no_automatic_request(self):
        api=self.api()
        before=api.get('/api/chat/personal/settings').json()
        self.assertIsNone(before['usage']['provider_policy']['verified_rpd'])
        self.assertFalse(before['connection']['key_configured'] is False)
        result=self.setup_free(api)
        self.assertEqual(result['usage']['limit'],20)
        self.assertEqual(result['connection']['plan'],'Free')
        self.assertFalse(result['usage']['provider_policy']['paid_approved'])
        self.assertEqual(result['connection']['state'],'test_required')
        self.model.test_connection.assert_not_called();self.model.generate.assert_not_called()

    def test_setup_requires_processing_and_checked_project_threshold(self):
        api=self.api()
        for payload in ({'processing_consent':False,'verified_minimum':True},{'processing_consent':True,'verified_minimum':False}):
            self.assertEqual(api.post('/api/chat/personal/setup',headers=self.headers,json=payload).status_code,400)
        self.assertEqual(self.api_app.state.ai_quota.status()['used'],0)
        self.model.test_connection.assert_not_called()

    def test_saved_one_is_preserved_and_paid_policy_is_not_mislabelled_free(self):
        api=self.api();quota=self.api_app.state.ai_quota
        quota.attest_provider(True,1,True)
        value=api.get('/api/chat/personal/settings').json()
        self.assertEqual(value['usage']['provider_policy']['verified_rpd'],1)
        self.assertEqual(value['connection']['plan'],'Paid')
        api.get('/api/chat/personal/settings')
        self.assertEqual(quota.status()['provider_policy']['verified_rpd'],1)
        self.setup_free(api)
        self.assertFalse(quota.status()['provider_policy']['paid_approved'])

    def test_existing_higher_verified_quota_not_silently_overwritten(self):
        api=self.api();self.api_app.state.ai_quota.attest_provider(True,500)
        self.setup_free(api)
        self.assertEqual(self.api_app.state.ai_quota.status()['provider_policy']['verified_rpd'],500)

    def test_test_exactly_one_attempt_explicit_and_idempotent(self):
        api=self.api();self.setup_free(api)
        self.assertEqual(self.probe(api,approved=False).status_code,400)
        self.model.test_connection.assert_not_called()
        result=self.probe(api).json();self.assertEqual(result['state'],'connected');self.assertEqual(result['response'],'POSTMARK_CONNECTION_OK')
        self.assertEqual(result['request_count'],1)
        self.assertTrue(self.probe(api).json()['deduplicated'])
        self.model.test_connection.assert_called_once()
        self.assertEqual(self.api_app.state.ai_quota.status()['used'],1)

    def test_cap_exhaustion_no_provider_and_actionable_status(self):
        api=self.api();self.setup_free(api);self.api_app.state.ai_quota.set_chat_ceiling(1,True);self.api_app.state.ai_quota.reserve('personal','connection-test')
        result=self.probe(api).json();self.assertEqual(result['state'],'internal_safety_ceiling');self.assertEqual(result['request_count'],0)
        settings=api.get('/api/chat/personal/settings').json();self.assertIn('internal_safety_ceiling',settings['connection']['blocking_reasons'])
        self.model.test_connection.assert_not_called()

    def test_provider_429_and_failed_model_not_local_success(self):
        api=self.api();self.setup_free(api)
        for code,state in [(429,'provider_rate_limited'),(404,'model_request_failed')]:
            self.model.test_connection.side_effect=ProviderFailure(state,'Specific fixture provider error',code)
            result=self.probe(api,id='request-'+str(code)).json()
            self.assertEqual(result['state'],state);self.assertEqual(result['provider_code'],code)
            self.assertEqual(result['request_count'],1)
        self.assertEqual(self.api_app.state.ai_quota.status()['used'],2)
        self.assertEqual(self.model.test_connection.call_count,2)

    def test_model_change_rechecks_policy_preserves_prior_attestation(self):
        api=self.api();self.setup_free(api)
        result=api.post('/api/chat/personal/model',headers=self.headers,json={'model':'gemini-3.8-flash','approved':True}).json()
        self.assertEqual(result['model'],'gemini-3.8-flash')
        self.assertNotIn('policy_required',result['connection']['blocking_reasons'])
        self.assertEqual(result['usage']['provider_policy']['model'],'gemini-3.8-flash')
        self.assertEqual(self.probe(api).json()['request_count'],1)
        self.model.test_connection.assert_called_once()

    def test_model_choice_unknown_or_unapproved_rejected(self):
        api=self.api()
        for model,approved in [('untrusted-model',True),('gemini-3.8-flash',False)]:
            self.assertEqual(api.post('/api/chat/personal/model',headers=self.headers,json={'model':model,'approved':approved}).status_code,400)

    def test_paid_requires_separate_approval_and_never_inferred_from_free(self):
        api=self.api()
        base={'plan':'paid','processing_consent':True,'verified_minimum':False}
        self.assertEqual(api.post('/api/chat/personal/setup',headers=self.headers,json=base).status_code,400)
        self.assertEqual(api.post('/api/chat/personal/setup',headers=self.headers,json={**base,'paid_approved':True}).status_code,200)
        self.assertTrue(self.api_app.state.ai_quota.status()['provider_policy']['paid_approved'])
        self.model.test_connection.assert_not_called()

    def test_processing_consent_disabled_separately_from_model(self):
        api=self.api();self.setup_free(api)
        result=api.post('/api/chat/personal/settings',headers=self.headers,json={'enabled':True,'processing_consent':False}).json()
        self.assertEqual(result['connection']['state'],'consent_required')
        reply=api.post('/api/chat/personal',headers=self.headers,json={'text':'مرحبا'}).json()
        self.assertEqual(reply['processor'],'not_processed');self.assertEqual(reply['results'],[])
        self.assertLess(len(reply['text']),150);self.model.generate.assert_not_called()

    def test_probe_strict_auth_account_isolation_and_no_email_payload(self):
        api=self.api();self.setup_free(api)
        self.assertEqual(api.post('/api/chat/personal/connection-test',json={'approved':True,'request_id':'x'}).status_code,403)
        self.assertEqual(self.probe(api,id='one',approved='true').status_code,422)
        self.assertEqual(api.post('/api/chat/unknown/connection-test',headers=self.headers,json={'approved':True,'request_id':'one'}).status_code,400)
        self.assertEqual(api.post('/api/chat/university/connection-test',headers=self.headers,json={'approved':True,'request_id':'one'}).json()['request_count'],0)
        self.model.test_connection.assert_not_called()

    def test_sdk_probe_fixed_bounded_payload_and_redacts_key_errors(self):
        from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
        with patch('email_agent.infrastructure.ai.gemini_model.genai.Client') as client:
            client.return_value.models.generate_content.return_value.text='POSTMARK_CONNECTION_OK'
            model=GeminiTextModel('fake-key','gemini-3.5-flash-lite')
            self.assertEqual(model.test_connection(),'POSTMARK_CONNECTION_OK')
            kwargs=client.return_value.models.generate_content.call_args.kwargs
            self.assertEqual(kwargs['config'].max_output_tokens,256)
            self.assertNotIn('messages',kwargs['contents']);self.assertNotIn('tools',kwargs)
            client.return_value.models.generate_content.side_effect=RuntimeError('key=AIzaFAKEPRIVATE&invalid')
            with self.assertRaises(ProviderFailure) as raised:model.test_connection()
            self.assertNotIn('AIzaFAKEPRIVATE',raised.exception.detail)

    def test_switching_back_to_prior_model_still_requires_rechecking(self):
        api=self.api();self.setup_free(api)
        for model in ('gemini-3.8-flash','gemini-3.5-flash-lite'):
            result=api.post('/api/chat/personal/model',headers=self.headers,json={'model':model,'approved':True}).json()
            self.assertNotIn('policy_required',result['connection']['blocking_reasons'])
        result=self.probe(api).json();self.assertEqual(result['request_count'],1)
        self.model.test_connection.assert_called_once()

    def test_empty_provider_response_is_failure_and_attempt_remains_counted(self):
        api=self.api();self.setup_free(api);self.model.test_connection.return_value=''
        result=self.probe(api).json();self.assertEqual(result['state'],'model_request_failed')
        self.assertEqual(result['request_count'],1);self.assertIsNone(result['response'])


class SearchApprovalRecoveryTests(Base):
    def assistant(self):
        model=Mock();quota=SQLiteAIQuota(self.root/'data'/'usage.db');quota.configure(0)
        return ChatAssistant(self.chat,self.store,quota,lambda:model,True),model

    def test_realistic_persisted_sender_shapes_unknown_labels_and_unclassified(self):
        rows=[('a91','personal','AliExpress <postmark-synthetic-4@notice.aliexpress.com>','Order update',None),('a92','personal','=?UTF-8?Q?AliExpress?= <news@ae.aliexpress.com>','عرض لفترة محدودة',None),('a93','personal','Store <newsletter@service.aliexpress.us>','A plain note',('TRASH',))]
        for id,account,sender,subject,labels in rows:self.messages.save(EmailMessage(id,account,sender,subject,1793000000000,labels))
        assistant,model=self.assistant()
        for text in ['اعطني ميلات الي اكسبرس','اعرض رسائل علي اكسبرس','ابحث عن AliExpress','اعطني ميلات الي اكس​برس']:
            result=assistant.send('personal',text);self.assertEqual(result['processor'],'local');self.assertEqual(result['results'][0]['matched_count'],6)
        self.assertEqual(assistant.send('university','اعطني ميلات الي اكسبرس')['results'][0]['matched_count'],3)
        model.generate.assert_not_called()

    def test_long_recent_search_not_lost_to_model_context_budget_and_selection_not_ordinal(self):
        for i in range(60):self.messages.save(EmailMessage('realistic-'+str(i),'personal','Long sender '*30+'<news@ae.aliexpress.com>','Long multilingual subject '*20,1000+i,None))
        assistant,model=self.assistant();rows=assistant.send('personal','اعطني ميلات الي اكسبرس')['results'][0]['messages']
        result=assistant.send('personal','انقل الثانية إلى Orders',context=['sale1'])
        preview=result['results'][0]['preview']
        self.assertEqual([r['message_id'] for r in preview['payload']['items']],[rows[1]['message_id']])
        self.assertEqual(self.management.details('personal',rows[1]['message_id'])['category_id'],None)
        self.chat.confirm('personal',preview['id'],True)
        self.assertEqual(self.management.details('personal',rows[1]['message_id'])['category_id'],'orders')
        model.generate.assert_not_called()

    def test_reject_and_account_deactivation_prevent_mutation(self):
        app=FastAPI();register_batch3_routes(app,self.messages,self.root,Mock())
        with TestClient(app,client=('127.0.0.1',1)) as api:
            preview=api.post('/api/chat/personal',headers=self.headers,json={'text':'Move this message to Orders','context_ids':['sale1']}).json()['results'][0]['preview']
            self.assertEqual(api.post('/api/actions/university/'+preview['id']+'/reject',headers=self.headers,json={'approved':True}).status_code,404)
            result=api.post('/api/actions/personal/'+preview['id']+'/reject',headers=self.headers,json={'approved':True}).json()
            self.assertEqual(result['status'],'rejected')
            api.post('/api/chat/personal/actions/'+preview['id']+'/confirm',headers=self.headers,json={'approved':True})
            self.assertEqual(self.management.details('personal','sale1')['category_id'],'promotions')
            second=api.post('/api/chat/personal',headers=self.headers,json={'text':'Move this message to Orders','context_ids':['sale2']}).json()['results'][0]['preview']
            self.assertEqual(api.post('/api/chat/personal/deactivate',headers=self.headers,json={}).json()['cancelled'],1)
            self.assertEqual(self.store.get('personal',second['id'])['status'],'cancelled')

    def test_empty_latest_search_does_not_reuse_older_ordinal(self):
        assistant,_=self.assistant();assistant.send('personal','اعرض رسائل AliExpress');assistant.send('personal','ابحث عن no-such-sender')
        result=assistant.send('personal','انقل الثانية إلى Orders')
        self.assertEqual(result['results'],[])

    def test_expired_local_approval_cannot_write(self):
        preview=self.chat.run('personal','Move this message to Orders',['sale1'])['results'][0]['preview']
        with closing(sqlite3.connect(self.store.path)) as db,db:db.execute('UPDATE action_previews SET expires=0 WHERE id=?',(preview['id'],))
        with self.assertRaises(ValueError):self.chat.confirm('personal',preview['id'],True)
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'promotions')


class SignatureRecoveryTests(Base):
    @classmethod
    def setUpClass(cls):
        batch33.DKIMPolicyTests.setUpClass()
        cls.key,cls.public=batch33.DKIMPolicyTests.key,batch33.DKIMPolicyTests.public
    signed=batch33.DKIMPolicyTests.signed
    verifier=batch33.DKIMPolicyTests.verifier
    def test_expired_signature_precise_reason_no_dns_or_sender(self):
        raw=self.signed().replace(b'v=1;',b'v=1; x=1;',1)
        verifier,_,dns=self.verifier(raw)
        with self.assertRaisesRegex(ValueError,'signature expired'):verifier.verify('personal','sale1','offers.example.org')
        dns.assert_not_called()

    def test_low_strength_key_is_local_policy_failure_not_fabricated_crypto_success(self):
        verifier,_,dns=self.verifier(self.signed())
        import dkim
        with patch.object(dkim.DKIM,'verify',side_effect=dkim.KeyFormatError('public key too small')):
            with self.assertRaisesRegex(ValueError,'2048-bit safety policy'):verifier.verify('personal','sale1','offers.example.org')

    def test_more_than_three_unsigned_signatures_dont_hide_valid_later_one(self):
        bad=b'DKIM-Signature: v=1; a=rsa-sha256; d=invalid.example; s=x; h=from; b=AA==; bh=AA==\r\n'
        verifier,_,dns=self.verifier(bad*6+self.signed())
        self.assertEqual(verifier.verify('personal','sale1','offers.example.org')['signature_index'],6)
        dns.assert_called_once()

    def test_dns_failure_is_distinguished_without_exposing_lookup_names(self):
        verifier,_,dns=self.verifier(self.signed())
        dns.side_effect=TimeoutError('private lookup detail')
        with self.assertRaises(ValueError) as raised:verifier.verify('personal','sale1','offers.example.org')
        self.assertIn('DNS lookup failed: TimeoutError',str(raised.exception))
        self.assertNotIn('private lookup detail',str(raised.exception))

    def test_header_scan_is_bounded_and_reports_unverified_remaining_signatures(self):
        bad=b'DKIM-Signature: v=1; a=rsa-sha256; d=invalid.example; s=x; h=from; b=AA==; bh=AA==\r\n'
        verifier,_,dns=self.verifier(bad*32+self.signed())
        with self.assertRaisesRegex(ValueError,'additional signatures remain unverified'):verifier.verify('personal','sale1','offers.example.org')
        dns.assert_not_called()
