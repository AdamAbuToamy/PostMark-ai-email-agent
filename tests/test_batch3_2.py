"""Batch 3.2: all providers mocked; no real mailbox, AI or unsubscribe effects."""
import json
import sqlite3
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from test_batch3 import Base
from email_agent.application.chat_assistant import ChatAssistant
from email_agent.application.subscriptions import subscriptions, observed_subscriptions, RefreshSubscriptions, subscription_identity
from email_agent.infrastructure.ai.usage import SQLiteAIQuota, reserve_call
from email_agent.domain.message import EmailMessage


class AssistantTests(Base):
    def setUp(self):
        super().setUp()
        self.model=Mock()
        self.model.generate.return_value=json.dumps({'reply':'A useful AI response','calls':[]})
        self.quota=SQLiteAIQuota(self.root/'data'/'ai_usage.db')
        self.quota.attest_provider(True,100)
        self.assistant=ChatAssistant(self.chat,self.store,self.quota,lambda:self.model,True)

    def enable(self):
        return self.assistant.configure('personal',True,False,None,True)

    def test_consent_precedes_default_gemini_and_persists_per_account(self):
        self.assertFalse(self.assistant.settings('personal')['enabled'])
        result=self.assistant.send('personal','ساعدني في تنظيم بريدي')
        self.assertEqual(result['processor'],'not_processed');self.model.generate.assert_not_called()
        with self.assertRaises(ValueError):self.assistant.configure('personal',True,False,None,False)
        self.assertEqual(self.enable()['default_mode'],'gemini')
        self.assertEqual(self.assistant.send('personal','ساعدني في تنظيم بريدي')['processor'],'gemini')
        self.assertEqual(self.quota.status()['used'],1)
        self.assertFalse(self.assistant.settings('university')['enabled'])
        self.assertEqual(self.store.preferences('personal')['enabled'],True)

    def test_deterministic_commands_do_not_spend_ai_and_bulk_preview_is_exact(self):
        self.enable()
        for id,subject,labels in [('reolink-sale','Discount sale',('INBOX',)),('reolink-important','Discount sale',('IMPORTANT',)),('reolink-order','Your order has shipped',('INBOX',))]:
            message=EmailMessage(id,'personal','Reolink <offers@reolink.example>',subject,100,labels)
            self.messages.save(message);self.classifier.classify(message)
        result=self.assistant.send('personal','اعرض رسائل AliExpress')
        self.assertEqual(result['processor'],'local');self.assertEqual(len(result['results'][0]['messages']),3)
        moved=self.assistant.send('personal','انقل رسائل AliExpress إلى Orders')
        self.assertEqual(len(moved['results'][0]['preview']['payload']['items']),3)
        trashed=self.assistant.send('personal','احذف إعلانات Reolink إلى المهملات')
        self.assertEqual([item['message_id'] for item in trashed['results'][0]['preview']['payload']['items']],['reolink-sale'])
        self.factory.assert_not_called();self.model.generate.assert_not_called()
        self.assertEqual(self.quota.status()['used'],0)

    def test_arabic_new_university_messages_remain_account_scoped(self):
        self.add('uni','Course update',account='university')
        self.classifier.manual('university','uni','university','Verified course fixture')
        result=self.assistant.send('university','اعرض رسائل الجامعة الجديدة')
        self.assertEqual([m['message_id'] for m in result['results'][0]['messages']],['uni'])
        self.management.set_status('university','uni','done')
        self.assertEqual(self.assistant.send('university','اعرض رسائل الجامعة الجديدة')['results'][0]['messages'],[])
        self.assertEqual(self.assistant.send('personal','اعرض رسائل الجامعة الجديدة')['results'][0]['messages'],[])

    def test_request_dedup_prevents_duplicate_preview_and_ai_call(self):
        self.enable()
        first=self.assistant.send('personal','Move this message to Orders',['sale1'],request_id='same-request')
        second=self.assistant.send('personal','Move this message to Orders',['sale1'],request_id='same-request')
        self.assertEqual(first['results'][0]['preview']['id'],second['results'][0]['preview']['id'])
        self.assertTrue(second['deduplicated']);self.assertEqual(len(self.store.history('personal')),2)
        with self.assertRaises(ValueError):self.assistant.send('personal','different',request_id='same-request')
        self.assistant.send('personal','What can you help me with?',request_id='ai-request')
        self.assistant.send('personal','What can you help me with?',request_id='ai-request')
        self.model.generate.assert_called_once()

    def test_cached_read_plan_requeries_current_rows_without_new_ai(self):
        self.enable()
        self.model.generate.return_value=json.dumps({'calls':[{'tool':'search_messages','args':{'search':'BrandNew'}}]})
        first=self.assistant.send('personal','Could you locate BrandNew correspondence?')
        self.assertEqual(first['results'][0]['messages'],[])
        self.add('new-match','BrandNew sale')
        again=self.assistant.send('personal','Could you locate BrandNew correspondence?')
        self.assertEqual(again['processor'],'cached_gemini')
        self.assertEqual(again['results'][0]['messages'][0]['message_id'],'new-match')
        self.model.generate.assert_called_once()

    def test_identical_pure_ai_answers_reuse_cache_without_new_charge(self):
        self.enable()
        first=self.assistant.send('personal','Could you explain how to organize mail?')
        again=self.assistant.send('personal','Could you explain how to organize mail?')
        self.assertEqual(first['processor'],'gemini')
        self.assertEqual(again['processor'],'cached_gemini')
        self.model.generate.assert_called_once()
        self.assertEqual(self.quota.status()['used'],1)

    def test_quota_exhaustion_local_alternative_and_settings_cost_guard(self):
        self.enable()
        self.assistant.configure('personal',True,False,1,False)
        self.assistant.send('personal','How can I plan my week?')
        exhausted=self.assistant.send('personal','Can you suggest another approach?')
        self.assertEqual(exhausted['processor'],'not_processed');self.assertIn('exhausted',exhausted['text'])
        self.assertEqual(self.quota.status()['remaining'],0)
        self.store.change_conversation('personal','clear_all')
        self.assertEqual(self.quota.status()['used'],1)
        self.assertIn('+00:00',self.quota.status()['resets_at'])
        self.model.generate.assert_called_once()
        self.assertFalse(reserve_call(self.root/'data'/'ai_usage.db','university','analysis'))
        with self.assertRaises(ValueError):self.assistant.configure('personal',True,False,10,False)
        self.assistant.configure('personal',True,False,2,True)
        self.assertTrue(reserve_call(self.root/'data'/'ai_usage.db','university','analysis'))
        self.assertFalse(reserve_call(self.root/'data'/'ai_usage.db','personal','analysis'))

    def test_invalid_model_never_executes_and_failure_is_metered(self):
        self.enable();self.model.generate.return_value=json.dumps({'calls':[{'tool':'shell','args':{}}]})
        result=self.assistant.send('personal','Could you assist?')
        self.assertEqual(result['processor'],'gemini_failed')
        self.assertEqual(self.store.list('personal'),[]);self.assertEqual(self.quota.status()['used'],1)

    def test_late_model_response_after_conversation_clear_cannot_create_orphaned_tools(self):
        self.enable()
        def late(prompt):
            with closing(sqlite3.connect(self.messages.database_path)) as db,db:
                db.execute("UPDATE chat_requests SET expires=0 WHERE account_id='personal'")
            self.store.change_conversation('personal','clear_all')
            return json.dumps({'calls':[{'tool':'assign_messages','args':{'message_ids':['sale1'],'category_id':'orders'}}]})
        self.model.generate.side_effect=late
        with self.assertRaises(ValueError):self.assistant.send('personal','Could you organize these?')
        self.assertEqual(self.store.history('personal'),[])
        self.assertEqual(self.store.list('personal'),[])

    def test_disable_configuration_and_developer_local_fail_safely(self):
        self.enable();self.assistant.configure('personal',True,True,None,False)
        self.assertEqual(self.assistant.send('personal','Some prose')['processor'],'local')
        self.model.generate.assert_not_called()
        unconfigured=ChatAssistant(self.chat,self.store,self.quota,lambda:self.model,False)
        with self.assertRaises(ValueError):unconfigured.configure('personal',True,False,None,True)

    def test_concurrent_reservations_enforce_configured_global_limit(self):
        self.quota.configure(2)
        with ThreadPoolExecutor(max_workers=6) as executor:
            outcomes=list(executor.map(lambda i:reserve_call(self.root/'data'/'ai_usage.db','personal',str(i)),range(8)))
        self.assertEqual(sum(outcomes),2)


class ConversationTests(Base):
    def test_new_archives_history_and_cancels_pending_approval(self):
        preview=self.chat.run('personal','Move this message to Orders',['sale1'])['results'][0]['preview']
        old=self.store.current_conversation('personal')
        self.store.change_conversation('personal','new')
        self.assertEqual(self.store.history('personal'),[])
        self.assertEqual(self.store.get('personal',preview['id'])['status'],'cancelled')
        self.chat.confirm('personal',preview['id'],True)
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'promotions')
        self.store.change_conversation('personal','select',old)
        self.assertEqual(len(self.store.history('personal')),2)

    def test_clear_removes_local_pending_records_only_and_replay_is_impossible(self):
        preview=self.chat.run('personal','Move this message to Orders',['sale1'])['results'][0]['preview']
        self.chat.run('university','List categories')
        count=self.messages.count_by_account('personal')
        self.store.change_conversation('personal','clear')
        with self.assertRaises(LookupError):self.chat.confirm('personal',preview['id'],True)
        self.assertEqual(self.store.history('personal'),[])
        self.assertEqual(len(self.store.history('university')),2)
        self.assertEqual(self.messages.count_by_account('personal'),count)
        self.provider.apply.assert_not_called()

    def test_clear_all_removes_archives_and_cache_but_keeps_completed_business_audit(self):
        preview=self.chat.run('personal','Move this message to Orders',['sale1'])['results'][0]['preview']
        self.chat.confirm('personal',preview['id'],True)
        self.store.change_conversation('personal','new')
        self.chat.run('personal','List categories')
        self.store.save_plan('personal','fingerprint',{'calls':[]})
        self.store.change_conversation('personal','clear_all')
        self.assertEqual(len(self.store.sessions('personal')),1)
        self.assertEqual(self.store.history('personal'),[])
        self.assertIsNone(self.store.cached_plan('personal','fingerprint'))
        self.assertEqual(self.store.get('personal',preview['id'])['status'],'succeeded')
        self.assertTrue(self.store.audit('personal',preview['id']))

    def test_running_actions_and_requests_protect_clear_and_expired_request_can_be_cleared(self):
        self.store.begin_request('personal','running','fp')
        with self.assertRaises(ValueError):self.store.change_conversation('personal','clear_all')
        with closing(sqlite3.connect(self.messages.database_path)) as db,db:
            db.execute("UPDATE chat_requests SET expires=0 WHERE account_id='personal'")
        self.store.change_conversation('personal','clear_all')
        self.assertFalse(self.store.request_active('personal','running'))
        preview=self.chat.run('personal','Move this message to Orders',['sale1'])['results'][0]['preview']
        self.store.claim('personal',preview['id'])
        with self.assertRaises(ValueError):self.store.change_conversation('personal','new')

    def test_chat_remote_approval_is_owned_and_cleared_without_orphan_or_gmail_call(self):
        preview=self.chat.run('personal','احذف هذه الرسالة إلى المهملات',['same'])['results'][0]['preview']
        self.assertEqual(preview['conversation_id'],self.store.current_conversation('personal'))
        self.store.change_conversation('personal','clear')
        with self.assertRaises(LookupError):self.remote.confirm('personal',preview['id'],True)
        self.factory.assert_not_called()
        with self.assertRaises(ValueError):self.store.preview('personal','local:set_status',{'items':[]},conversation_id=self.store.current_conversation('university'))
        self.assertEqual(self.store.list('personal'),[])

    def test_conversation_selection_checks_ownership(self):
        id=self.store.current_conversation('university')
        with self.assertRaises(LookupError):self.store.change_conversation('personal','select',id)


class DiscoveryTests(Base):
    def setUp(self):
        super().setUp();self.messages.initialize_discovery()
        self.reader=Mock();self.reader.read_list_headers.return_value={}
        self.discovery=RefreshSubscriptions(self.messages,self.reader)

    def test_no_list_id_offers_and_unverified_senders_are_separate(self):
        self.messages.record_list_headers('personal','sale1',{'list-unsubscribe':'<https://example.org/leave?list=one>'})
        groups=observed_subscriptions(self.messages,'personal')
        self.assertEqual({g['kind'] for g in groups},{'header_offer','promotional_sender'})
        self.assertFalse(any(g['automatic_available'] for g in groups))
        self.assertTrue(all('same' not in g['message_ids'] for g in groups))

    def test_same_company_different_lists_not_merged_and_folded_headers_parse(self):
        rows=[('one',{'list-id':'One <one.example.org>','list-unsubscribe':'<https://example.org/one>,\r\n <mailto:leave@example.org>'}),('two',{'list-id':'Two <two.example.org>','list-unsubscribe':'<https://example.org/two>'})]
        groups=subscriptions(rows)
        self.assertEqual(len(groups),2);self.assertEqual(len(groups[0]['methods']),2)
        self.assertNotEqual(subscription_identity({'list-unsubscribe':'<https://example.org/u?list=1>'},'news@example.org')[0],subscription_identity({'list-unsubscribe':'<https://example.org/u?list=2>'},'news@example.org')[0])

    def test_list_id_comments_and_case_insensitive_https_scheme_are_parsed(self):
        groups=subscriptions([('one',{'list-id':'Offers <one.example.org> (marketing list)','list-unsubscribe':'<HTTPS://example.org/u>'}),('two',{'list-id':'Other <two.example.org> (same company)','list-unsubscribe':'<HTTPS://example.org/u>'})])
        self.assertEqual(len(groups),2)
        self.assertTrue(all(group['kind']=='mailing_list' for group in groups))
        self.assertEqual(len(groups[0]['methods']),1)

    def test_empty_metadata_is_cached_and_bounded_resume_reads_only_missing(self):
        for i in range(23):self.add('missing'+str(i),'Discount sale')
        result=self.discovery.execute('personal')
        self.assertEqual(len(result['items']),20)
        self.assertEqual(result['progress']['indexed'],20)
        again=self.discovery.execute('personal')
        self.assertEqual(len(again['items']),5)
        self.assertTrue(again['progress']['complete'])
        self.assertEqual(self.discovery.execute('personal')['items'],[])
        self.assertEqual(self.reader.read_list_headers.call_count,25)
        self.assertEqual(self.messages.discovery_status('university')['indexed'],0)

    def test_error_backoff_and_retry_cap_do_not_block_other_messages(self):
        self.reader.read_list_headers.side_effect=TimeoutError()
        self.discovery.execute('personal')
        self.assertEqual(self.messages.discovery_status('personal')['failed'],2)
        self.assertEqual(self.messages.discovery_candidates('personal'),[])
        for _ in range(2):
            with closing(sqlite3.connect(self.messages.database_path)) as db,db:db.execute('UPDATE subscription_metadata_scan SET retry_at=0')
            self.discovery.execute('personal')
        self.assertEqual(self.messages.discovery_status('personal')['exhausted'],2)
        with closing(sqlite3.connect(self.messages.database_path)) as db,db:db.execute('UPDATE subscription_metadata_scan SET retry_at=0')
        self.assertEqual(self.messages.discovery_candidates('personal'),[])

    def test_interrupted_page_commits_each_success_and_resumes_remainder(self):
        self.reader.read_list_headers.side_effect=[{'list-id':'One <one.example.org>'},KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):self.discovery.execute('personal')
        self.assertEqual(self.messages.discovery_status('personal')['indexed'],1)
        self.reader.read_list_headers.side_effect=None;self.reader.read_list_headers.return_value={}
        result=self.discovery.execute('personal')
        self.assertEqual(len(result['items']),1)
        self.assertEqual(self.messages.discovery_status('personal')['indexed'],2)

    def test_concurrent_scan_lease_and_protected_transactions(self):
        owner=self.messages.claim_discovery('personal')
        with self.assertRaises(ValueError):self.discovery.execute('personal')
        self.messages.release_discovery('personal','wrong-owner')
        self.assertFalse(self.messages.claim_discovery('personal'))
        self.messages.release_discovery('personal',owner)
        self.messages.save(EmailMessage('sale1','personal','AliExpress','Your order has shipped',100,('INBOX',)))
        result=self.discovery.execute('personal')
        self.assertEqual(result['progress']['skipped'],1)
        self.assertNotIn('sale1',[call.args[0] for call in self.reader.read_list_headers.call_args_list])

    def test_cached_manual_offer_never_fetches_original_or_sends_request(self):
        from email_agent.application.unsubscribe import Unsubscribe
        self.messages.record_list_headers('personal','sale1',{'list-unsubscribe':'<mailto:leave@example.org>'})
        group=next(item for item in observed_subscriptions(self.messages,'personal') if item['kind']=='header_offer')
        verifier,sender=Mock(),Mock()
        preview=Unsubscribe(self.messages,self.management,self.store,verifier,sender).preview('personal',[group['id']])
        self.assertFalse(preview['payload']['items'][0]['eligible'])
        verifier.verify.assert_not_called();sender.assert_not_called()

    def test_cached_headers_no_network_and_account_isolation(self):
        self.messages.record_list_headers('personal','sale1',{})
        self.messages.record_list_headers('personal','sale2',{'list-id':'Offers <offers.example.org>'})
        self.assertEqual(self.discovery.execute('personal')['items'],[])
        self.reader.read_list_headers.assert_not_called()
        self.assertEqual(self.messages.discovery_status('university')['missing'],2)

    def test_dkim_signed_unsubscribe_without_list_id_is_supported_and_tampering_fails(self):
        import dkim
        from test_batch3_1 import DKIMTests
        from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier
        DKIMTests.setUpClass()
        fixture=DKIMTests()
        raw=fixture.body.replace(b'List-ID: Offers <offers.example.org>\r\n',b'')
        signed=dkim.sign(raw,b'selector',b'example.org',fixture.key,include_headers=[b'from',b'list-unsubscribe',b'list-unsubscribe-post'])+raw
        identity,kind=subscription_identity({'list-unsubscribe':'<https://example.org/u>, <mailto:leave@example.org>'},'Offers <offers@example.org>')
        self.assertEqual(kind,'header_offer')
        client=Mock();client.get_raw_message.return_value=signed
        verifier=DKIMUnsubscribeVerifier(lambda account:client,lambda *a,**kw:fixture.dns)
        self.assertEqual(verifier.verify('personal','sale1',identity)['url'],'https://example.org/u')
        client.get_raw_message.return_value=signed.replace(b'https://example.org/u',b'https://evil.example/u')
        with self.assertRaises(ValueError):verifier.verify('personal','sale1',identity)


class Batch32ApiTests(Base):
    def api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from email_agent.api.batch3 import register_batch3_routes
        app=FastAPI();gateway=Mock();model=Mock();model.generate.return_value=json.dumps({'reply':'Mock AI reply','calls':[]})
        register_batch3_routes(app,self.messages,self.root,gateway,lambda:model)
        return TestClient(app,client=('127.0.0.1',1234)),model,gateway

    def test_consent_settings_strict_types_and_no_auto_model_call(self):
        api,model,_=self.api()
        with api:
            self.assertFalse(api.get('/api/chat/personal/settings').json()['enabled'])
            self.assertEqual(api.post('/api/chat/personal/settings',json={'enabled':True},headers=self.headers).status_code,400)
            self.assertEqual(api.post('/api/chat/personal/settings',json={'enabled':True,'cost_approved':True,'daily_limit':True},headers=self.headers).status_code,422)
            self.assertEqual(api.post('/api/chat/personal/settings',json={'enabled':True,'cost_approved':True,'free_tier_verified':True,'verified_rpd':100},headers=self.headers).status_code,200)
            model.generate.assert_not_called()
            result=api.post('/api/chat/personal',json={'text':'Could you help me organize?','request_id':'api-request'},headers=self.headers).json()
            self.assertEqual(result['processor'],'gemini')
            api.post('/api/chat/personal',json={'text':'Could you help me organize?','request_id':'api-request'},headers=self.headers)
            model.generate.assert_called_once()

    def test_clear_requires_confirmation_and_csrf_and_never_changes_gmail(self):
        api,_,gateway=self.api()
        with api:
            api.post('/api/chat/personal',json={'text':'Move this message to Orders','context_ids':['sale1']},headers=self.headers)
            path='/api/chat/personal/conversations'
            self.assertEqual(api.post(path,json={'operation':'clear'}).status_code,403)
            self.assertEqual(api.post(path,json={'operation':'clear'},headers=self.headers).status_code,400)
            self.assertEqual(api.post(path,json={'operation':'clear','confirmed':True},headers=self.headers).status_code,200)
            self.assertEqual(api.get('/api/chat/personal/history').json(),[])
            gateway.actions.assert_not_called()


if __name__=='__main__':unittest.main()
