"""Batch 3.3: local routing, independent DKIM evidence and mocked live readiness."""
import base64
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch, call
from test_batch3 import Base
from email_agent.application.chat_assistant import ChatAssistant
from email_agent.application.unsubscribe import Unsubscribe
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.ai.usage import SQLiteAIQuota
from email_agent.infrastructure.gmail.capabilities import GmailActionAdapter, MODIFY_SCOPE
from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier


class ArabicRoutingTests(Base):
    def setUp(self):
        super().setUp()
        self.model=Mock()
        self.quota=SQLiteAIQuota(self.root/'data'/'ai_usage.db')
        self.quota.configure(0)
        self.quota.attest_provider(True,100)
        self.store.save_preferences('personal',True,False)
        self.assistant=ChatAssistant(self.chat,self.store,self.quota,lambda:self.model,True)

    def test_exact_user_sentence_bypasses_quota_and_model_even_in_explicit_mode(self):
        with patch.object(self.quota,'reserve',side_effect=AssertionError('Local search must not reserve AI')),patch.object(self.quota,'status',side_effect=AssertionError('Local search must not need quota status')):
            for mode in ('auto','gemini','local'):
                result=self.assistant.send('personal','اعطني ميلات الي اكسبرس',mode=mode)
                self.assertEqual(result['processor'],'local')
                self.assertFalse(result['ai_needed'])
                self.assertEqual(result['results'][0]['matched_count'],3)
                self.assertEqual({m['message_id'] for m in result['results'][0]['messages']},{'same','sale1','sale2'})
        self.model.generate.assert_not_called()
        self.assertEqual(self.quota.status()['used'],0)

    def test_arabic_brand_variants_and_mixed_english_are_equivalent(self):
        for text in ('اعرض رسائل علي اكسبرس','ابحث عن AliExpress','اعطيني إيميلات على إكسبريس','هاتلي ميلات Ali Express','وريني ايميلات ALIEXPRESS'):
            result=self.assistant.send('personal',text)
            self.assertEqual(result['processor'],'local',text)
            self.assertEqual(result['results'][0]['matched_count'],3,text)
        self.model.generate.assert_not_called()

    def test_university_search_and_category_source_move_require_no_model(self):
        self.add('campus','Course timetable','university')
        self.classifier.manual('university','campus','university','Known course fixture')
        result=self.assistant.send('university','ورجيني ايميلات الجامعة')
        self.assertEqual([m['message_id'] for m in result['results'][0]['messages']],['campus'])
        self.assertEqual(self.assistant.send('personal','ورجيني ايميلات الجامعة')['results'][0]['matched_count'],0)
        result=self.assistant.send('personal','حط رسائل الطلبات في Orders')
        self.assertEqual([m['message_id'] for m in result['results'][0]['preview']['payload']['items']],['same'])
        self.assertEqual(self.management.details('personal','same')['source'],'automatic')
        self.model.generate.assert_not_called()

    def test_brand_bulk_move_is_exact_pending_account_isolated_and_manual_after_confirm(self):
        result=self.assistant.send('personal','انقل رسائل علي اكسبرس إلى Orders')
        preview=result['results'][0]['preview']
        self.assertEqual(len(preview['payload']['items']),3)
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'promotions')
        self.chat.confirm('personal',preview['id'],True)
        self.classifier.classify(self.messages.get('personal','sale1'))
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'orders')
        self.assertEqual(self.management.details('personal','sale1')['source'],'manual')
        self.assertEqual(self.management.details('university','sale1')['category_id'],'promotions')
        self.factory.assert_not_called()

    def test_local_search_has_no_quota_warning_after_actual_ai_exhaustion(self):
        blocked=self.assistant.send('personal','Could you help me plan?')
        self.assertTrue(blocked['ai_needed']);self.assertIn('exhausted',blocked['text'])
        local=self.assistant.send('personal','اعطني ميلات الي اكسبرس')
        self.assertFalse(local['ai_needed']);self.assertNotIn('exhausted',local['text'])
        self.assertEqual(local['results'][0]['matched_count'],3)
        self.assertFalse(self.store.history('personal')[-1]['content']['ai_needed'])

    def test_normalized_indexed_body_is_searchable_but_never_returned_or_sent_to_ai(self):
        self.add('body-only','Opaque subject')
        self.messages.record_search_content('personal','body-only','هذه رسالة من علي إكسبرس: body-only-marker')
        result=self.assistant.send('personal','اعطني ميلات الي اكسبرس')
        self.assertEqual(result['results'][0]['matched_count'],4)
        self.assertIn('body-only',{m['message_id'] for m in result['results'][0]['messages']})
        self.assertNotIn('body-only-marker',json.dumps(result,ensure_ascii=False))
        self.model.generate.assert_not_called()

    def test_literal_parameters_unicode_and_same_id_content_isolation(self):
        self.messages.record_search_content('personal','same',"literal %_ ' OR 1=1 --")
        self.messages.record_search_content('university','same','university-private-marker')
        self.assertEqual(self.messages.search_local_page('personal',"%_ ' OR 1=1 --")['matched_count'],1)
        self.assertEqual(self.messages.search_local_page('personal','university-private-marker')['matched_count'],0)
        self.assertEqual(self.messages.search_local_page('university','university-private-marker')['matched_count'],1)
        with self.assertRaises(LookupError):self.messages.record_search_content('personal','missing','body')

    def test_pagination_counts_stable_order_and_index_migration_preserves_mail(self):
        for index in range(57):self.add('page'+str(index).zfill(3),'Page marker')
        first=self.chat.execute('personal','search_messages',{'search':'Page marker'})
        second=self.chat.execute('personal','search_messages',{**first['query'],'offset':first['next_offset']})
        self.assertEqual(first['matched_count'],57);self.assertEqual(len(first['messages']),50)
        self.assertEqual(len(second['messages']),7);self.assertIsNone(second['next_offset'])
        self.assertFalse({m['message_id'] for m in first['messages']}&{m['message_id'] for m in second['messages']})
        self.messages.record_search_content('personal','same','retained-index')
        self.messages.initialize();self.messages.initialize()
        self.assertEqual(self.messages.search_local_page('personal','retained-index')['matched_count'],1)
        self.assertEqual(self.messages.get('personal','same').label_ids,('INBOX',))
        with self.assertRaises(ValueError):self.chat.execute('personal','search_messages',{'offset':-1})


class SearchApiTests(Base):
    def test_paginated_read_endpoint_and_opened_message_index_use_real_account_context(self):
        from test_api import create_app
        from fastapi.testclient import TestClient
        gateway=Mock()
        payload={'id':'same','labelIds':['INBOX'],'payload':{'mimeType':'text/plain','headers':[{'name':'Subject','value':'Opened fixture'}],'body':{'data':base64.urlsafe_b64encode(b'opened-only-search-term').decode()}}}
        gateway.client.return_value.get_full_message.return_value=payload
        with TestClient(create_app(self.messages,gmail_gateway=gateway),client=('127.0.0.1',1234)) as api:
            self.assertEqual(api.get('/api/messages/university/same/content').status_code,200)
            query='?search=opened-only-search-term&limit=1'
            self.assertEqual(api.get('/api/chat/university/messages'+query).json()['matched_count'],1)
            self.assertEqual(api.get('/api/chat/personal/messages'+query).json()['matched_count'],0)
            self.assertEqual(api.get('/api/chat/unknown/messages').status_code,400)
            self.assertEqual(api.get('/api/chat/personal/messages?limit=101').status_code,422)
            self.assertEqual(api.get('/api/chat/personal/messages?offset=-1').status_code,422)
            self.assertEqual(api.get('/api/chat/personal/messages?category=not_active').status_code,400)
        gateway.client.assert_called_once_with('university')


class DKIMPolicyTests(Base):
    @classmethod
    def setUpClass(cls):
        from test_batch3_1 import DKIMTests
        DKIMTests.setUpClass()
        cls.key,cls.public=DKIMTests.key,DKIMTests.dns

    def signed(self, signer='example.org', author='example.org', target='https://example.org/u', cover=None):
        import dkim
        body=('From: Offers <welcome@'+author+'>\r\nSubject: Discount sale\r\nList-ID: Offers <offers.example.org>\r\nList-Unsubscribe: <'+target+'>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nMarketing fixture\r\n').encode()
        signature=dkim.sign(body,b'unit',signer.encode(),self.key,include_headers=cover or [b'from',b'subject',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])
        return signature+body

    def verifier(self, raw):
        client=Mock();client.get_raw_message.return_value=raw
        dns=Mock(return_value=self.public)
        return DKIMUnsubscribeVerifier(lambda account:client,dns),client,dns

    def test_exact_signature_is_not_a_claim_of_sender_legitimacy_or_dmarc_pass(self):
        verifier,_,_=self.verifier(self.signed())
        proof=verifier.verify('personal','sale1','offers.example.org')
        self.assertEqual(proof['alignment'],'exact')
        self.assertEqual(proof['author_authorization'],'signed_author_domain')
        self.assertNotIn('dmarc_pass',proof)

    def test_genuine_third_party_signature_is_valid_but_author_delegation_is_unestablished(self):
        verifier,_,dns=self.verifier(self.signed(signer='mailer.example.net',author='openrouter.ai'))
        proof=verifier.verify('personal','sale1','offers.example.org')
        self.assertEqual(proof['signer'],'mailer.example.net')
        self.assertEqual(proof['author_domain'],'openrouter.ai')
        self.assertEqual(proof['alignment'],'not_exact')
        self.assertEqual(proof['author_authorization'],'not_established')
        dns.assert_called_once()

    def test_subdomain_relationship_is_not_guessed_as_dmarc_relaxed_alignment(self):
        verifier,_,_=self.verifier(self.signed(signer='mail.example.org'))
        proof=verifier.verify('personal','sale1','offers.example.org')
        self.assertEqual(proof['alignment'],'not_exact')
        self.assertEqual(proof['author_authorization'],'not_established')

    def test_invalid_signature_and_fake_gmail_summary_never_establish_evidence(self):
        verifier,_,_=self.verifier(b'Authentication-Results: mx.google.com; dkim=pass\r\n'+self.signed(signer='mailer.example.net',author='openrouter.ai').replace(b'Marketing fixture',b'Tampered content'))
        with self.assertRaisesRegex(ValueError,'No independently valid'):verifier.verify('personal','sale1','offers.example.org')
        verifier,_,_=self.verifier(b'Authentication-Results: mx.google.com; dkim=pass\r\n'+self.signed().split(b'From:',1)[1])
        with self.assertRaises(ValueError):verifier.verify('personal','sale1','offers.example.org')

    def test_unsigned_unsubscribe_header_or_post_is_rejected_before_dns(self):
        for missing in (b'list-unsubscribe',b'list-unsubscribe-post'):
            cover=[h for h in (b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post') if h!=missing]
            verifier,_,dns=self.verifier(self.signed(cover=cover))
            with self.assertRaisesRegex(ValueError,'required headers not signed'):verifier.verify('personal','sale1','offers.example.org')
            dns.assert_not_called()

    def test_multiple_signatures_accept_applicable_later_signature_and_prefer_exact_author(self):
        import dkim
        raw=self.signed()
        body=raw[raw.index(b'From:'):]
        noncover=dkim.sign(body,b'unit',b'relay.example.net',self.key,include_headers=[b'from',b'subject'])
        verifier,_,_=self.verifier(noncover+raw)
        self.assertEqual(verifier.verify('personal','sale1','offers.example.org')['signature_index'],1)
        external=dkim.sign(body,b'unit',b'mailer.example.net',self.key,include_headers=[b'from',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'])
        verifier,_,_=self.verifier(external+raw)
        proof=verifier.verify('personal','sale1','offers.example.org')
        self.assertEqual(proof['alignment'],'exact');self.assertEqual(proof['signature_index'],1)
        verifier,_,dns=self.verifier(noncover*3+raw)
        self.assertEqual(verifier.verify('personal','sale1','offers.example.org')['signature_index'],3)
        dns.assert_called_once()

    def test_unsafe_links_and_incorrect_post_signal_are_ineligible(self):
        for target in ('http://example.org/u','https://user:password@example.org/u','https://127.0.0.1/u','https://[::1]/u','https://169.254.169.254/u','https://localhost/u','https://example.org:8443/u','https://example.org/u#fragment'):
            verifier,_,dns=self.verifier(self.signed(target=target))
            with self.assertRaises(ValueError):verifier.verify('personal','sale1','offers.example.org')
            dns.assert_not_called()
        verifier,_,_=self.verifier(self.signed().replace(b'List-Unsubscribe=One-Click',b'List-Unsubscribe=Other'))
        with self.assertRaises(ValueError):verifier.verify('personal','sale1','offers.example.org')

    def service(self):
        raw=self.signed(signer='mailer.example.net',author='openrouter.ai')
        verifier,client,_=self.verifier(raw)
        self.messages.record_list_headers('personal','sale1',{'list-id':'Offers <offers.example.org>','list-unsubscribe':'<https://example.org/u>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        group=next(group for group in observed_subscriptions(self.messages,'personal') if group['kind']=='mailing_list')
        sender=Mock(return_value={'state':'requested'})
        return Unsubscribe(self.messages,self.management,self.store,verifier,sender),group['id'],sender,client

    def test_external_signer_needs_additional_per_target_ack_and_is_audited(self):
        service,id,sender,_=self.service()
        preview=service.preview('personal',[id])
        self.assertTrue(preview['payload']['items'][0]['requires_third_party_ack'])
        for acknowledged in (None,[],['other']):
            with self.assertRaises(ValueError):service.confirm('personal',preview['id'],True,[id],acknowledged)
        self.assertEqual(self.store.get('personal',preview['id'])['status'],'pending')
        sender.assert_not_called()
        result=service.confirm('personal',preview['id'],True,[id],[id])
        self.assertEqual(result['result']['items'][0]['status'],'requested')
        service.confirm('personal',preview['id'],True,[id],[id]);sender.assert_called_once()
        audit=json.dumps(self.store.audit('personal',preview['id']))
        self.assertIn('external_signers_acknowledged',audit);self.assertIn('not_established',audit)
        with self.assertRaises(LookupError):service.confirm('university',preview['id'],True,[id],[id])

    def test_old_policy_evidence_and_changed_signature_fail_closed(self):
        service,id,sender,client=self.service()
        preview=service.preview('personal',[id])
        client.get_raw_message.return_value=client.get_raw_message.return_value.replace(b'Marketing fixture',b'Changed original')
        result=service.confirm('personal',preview['id'],True,[id],[id])
        self.assertEqual(result['result']['items'][0]['status'],'skipped');sender.assert_not_called()
        fake=Mock();fake.verify.return_value={'identity':'offers.example.org','message_id':'sale1','url':'https://example.org/u','host':'example.org','signer':'example.org','raw_sha256':'old-proof'}
        old=Unsubscribe(self.messages,self.management,self.store,fake,sender).preview('personal',[id])
        self.assertFalse(old['payload']['items'][0]['eligible'])


class GmailLiveReadinessTests(Base):
    def test_one_mocked_trash_then_restore_has_verified_labels_audit_and_no_replay(self):
        client=Mock()
        state={'labels':('INBOX',)}
        client.get_message.side_effect=lambda *a,**kw:{'label_ids':state['labels']}
        client.trash.side_effect=lambda id:state.update(labels=('TRASH',)) or {'labelIds':['TRASH']}
        client.restore.side_effect=lambda id,labels:state.update(labels=('INBOX',)) or {'labelIds':['INBOX']}
        self.factory.return_value=GmailActionAdapter(client)
        trash=self.remote.preview('personal','trash',['sale1'])
        with self.assertRaises(ValueError):self.remote.confirm('personal',trash['id'],'yes')
        with self.assertRaises(LookupError):self.remote.confirm('university',trash['id'],True)
        self.factory.assert_not_called()
        done=self.remote.confirm('personal',trash['id'],True)
        self.assertEqual(self.messages.get('personal','sale1').label_ids,('TRASH',))
        self.assertEqual(done['result']['items'][0]['labels_before'],['INBOX'])
        self.assertEqual(done['result']['items'][0]['labels_after'],['TRASH'])
        self.remote.confirm('personal',trash['id'],True)
        restore=self.remote.preview('personal','restore',['sale1'])
        self.assertEqual(self.remote.confirm('personal',restore['id'],True)['status'],'succeeded')
        self.remote.confirm('personal',restore['id'],True)
        self.assertEqual(self.messages.get('personal','sale1').label_ids,('INBOX',))
        client.trash.assert_called_once_with('sale1');client.restore.assert_called_once_with('sale1',('TRASH',))
        client.delete.assert_not_called()
        self.assertIn('authoritative_label_readback',json.dumps(self.store.audit('personal',restore['id'])))
        self.assertEqual(self.messages.get('university','sale1').label_ids,('INBOX',))

    def test_unverified_result_is_uncertain_and_preserves_valid_local_labels(self):
        self.provider.apply.return_value=('INBOX',)
        preview=self.remote.preview('personal','trash',['sale1'])
        result=self.remote.confirm('personal',preview['id'],True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['result']['items'][0]['status'],'uncertain')
        self.assertEqual(self.messages.get('personal','sale1').label_ids,('INBOX',))

    def test_oauth_requests_modify_only_and_preserves_separate_readonly_files(self):
        from email_agent.workers.upgrade_oauth import upgrade
        from email_agent.infrastructure.gmail.capabilities import AccountGmail
        old_path=self.root/'tokens'/'personal.json';old_path.parent.mkdir(exist_ok=True)
        old_path.write_text('preserved-personal-readonly');university=old_path.with_name('university.json');university.write_text('preserved-university-readonly')
        old,new=Mock(),Mock();old.expired=False;new.has_scopes.return_value=True;new.to_json.return_value='{"scopes":["https://www.googleapis.com/auth/gmail.modify"]}'
        with patch('email_agent.workers.upgrade_oauth.Credentials.from_authorized_user_file',return_value=old),patch('email_agent.workers.upgrade_oauth.InstalledAppFlow') as flow,patch('email_agent.workers.upgrade_oauth.GmailClient') as provider:
            flow.from_client_secrets_file.return_value.run_local_server.return_value=new
            provider.return_value.get_profile.return_value={'emailAddress':'owner@example.org'}
            upgrade(self.root,'personal')
            flow.from_client_secrets_file.assert_called_once_with(str(self.root/'credentials.json'),[MODIFY_SCOPE])
        self.assertEqual(old_path.read_text(),'preserved-personal-readonly');self.assertEqual(university.read_text(),'preserved-university-readonly')
        self.assertTrue(old_path.with_name('personal.modify.json').is_file())
        self.assertFalse(old_path.with_name('university.modify.json').exists())
        with patch('email_agent.infrastructure.gmail.capabilities.Credentials.from_authorized_user_file',return_value=new):
            self.assertTrue(AccountGmail(self.root).capabilities('personal')['gmail_modify'])
            self.assertFalse(AccountGmail(self.root).capabilities('university')['gmail_modify'])

    def test_spam_restore_requires_inbox_readback_and_wrong_oauth_identity_saves_nothing(self):
        client=Mock();client.get_message.return_value={'label_ids':()}
        with self.assertRaises(ValueError):GmailActionAdapter(client).apply('restore','sale1',('SPAM',))
        from email_agent.workers.upgrade_oauth import upgrade
        old_path=self.root/'tokens'/'personal.json';old_path.parent.mkdir(exist_ok=True);old_path.write_text('unchanged')
        old,new=Mock(),Mock();old.expired=False;new.has_scopes.return_value=True
        with patch('email_agent.workers.upgrade_oauth.Credentials.from_authorized_user_file',return_value=old),patch('email_agent.workers.upgrade_oauth.InstalledAppFlow') as flow,patch('email_agent.workers.upgrade_oauth.GmailClient') as provider:
            flow.from_client_secrets_file.return_value.run_local_server.return_value=new
            provider.return_value.get_profile.side_effect=[{'emailAddress':'owner@example.org'},{'emailAddress':'other@example.org'}]
            with self.assertRaises(ValueError):upgrade(self.root,'personal')
        self.assertEqual(old_path.read_text(),'unchanged');self.assertFalse(old_path.with_name('personal.modify.json').exists())


if __name__=='__main__':unittest.main()
