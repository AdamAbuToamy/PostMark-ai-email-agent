"""Batch 3.1 behavior and security contracts. Only temp databases and mocks."""
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import Mock, patch
from test_batch3 import Base
from email_agent.application.review_messages import ReviewMessages
from email_agent.application.unsubscribe import Unsubscribe
from email_agent.infrastructure.gmail.capabilities import GmailActionAdapter
from email_agent.infrastructure.http.dkim_unsubscribe import DKIMUnsubscribeVerifier
from email_agent.domain.message import EmailMessage


class BilingualChatTests(Base):
    def setUp(self):
        super().setUp()
        registry = self.classifier.registry
        registry.propose('security','Security','Account security')
        registry.approve(registry.proposals()[0]['id'])

    def test_arabic_search_returns_real_local_messages_only(self):
        result=self.chat.run('personal','اعرض رسائل AliExpress')
        self.assertEqual(len(result['results'][0]['messages']),3)
        self.assertTrue(all(r['account_id']=='personal' for r in result['results'][0]['messages']))

    def test_arabic_matching_bulk_requires_confirmation_and_preserves_other_account(self):
        result=self.chat.run('personal','انقل رسائل AliExpress إلى Orders')
        preview=result['results'][0]['preview']
        self.assertEqual(len(preview['payload']['items']),3)
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'promotions')
        self.chat.confirm('personal',preview['id'],True)
        self.assertEqual(self.management.details('personal','sale1')['category_id'],'orders')
        self.assertEqual(self.management.details('university','sale1')['category_id'],'promotions')
        self.classifier.classify(self.messages.get('personal','sale1'))
        self.assertEqual(self.management.details('personal','sale1')['source'],'manual')

    def test_arabic_create_category_and_selected_move(self):
        result=self.chat.run('personal','أنشئ قسمًا اسمه Shopping')
        preview=result['results'][0]['preview']
        self.chat.confirm('personal',preview['id'],True)
        self.assertIn('shopping',[c['id'] for c in self.management.categories('personal')])
        moved=self.chat.run('personal','انقل هذه الرسالة إلى Security',['same'])
        self.assertEqual(moved['results'][0]['preview']['payload']['args']['category_id'],'security')

    def test_arabic_unclassified_and_new_promotions(self):
        self.messages.save(EmailMessage('unknown','personal','Someone','Unclassified'))
        result=self.chat.run('personal','اعرض الرسائل غير المصنفة')
        self.assertEqual([r['message_id'] for r in result['results'][0]['messages']],['unknown'])
        self.management.set_status('personal','sale2','done')
        result=self.chat.run('personal','اعرض إعلانات لم أنتهِ منها')
        self.assertEqual([r['message_id'] for r in result['results'][0]['messages']],['sale1'])

    def test_arabic_trash_preview_and_subscriptions(self):
        result=self.chat.run('personal','احذف هذه الرسالة إلى المهملات',['same'])
        self.assertEqual(result['results'][0]['preview']['kind'],'gmail:trash')
        self.factory.assert_not_called()
        self.assertEqual(self.chat.run('personal','اعرض الاشتراكات التي يمكن إلغاؤها')['results'][0]['subscriptions'][0]['kind'],'promotional_sender')

    def test_missing_context_and_unknown_category_clarify_without_mutation(self):
        for text in ('انقل هذه الرسالة إلى Security','انقل هذه الرسالة إلى Imaginary','احذف هذه الرسالة إلى المهملات'):
            self.assertEqual(self.chat.run('personal',text)['status'],'clarification')
        self.assertEqual(self.store.list('personal'),[])

    def test_provider_envelope_is_normalized_but_not_arbitrary_arguments(self):
        self.assertEqual(len(self.chat.run('personal','search',plan=[{'name':'search_messages','arguments':{'search':'AliExpress'}}])['results'][0]['messages']),3)
        with self.assertRaises(ValueError):
            self.chat.run('personal','x',plan=[{'name':'search_messages','arguments':{'shell':'rm'}}])
        with self.assertRaises(ValueError):
            self.chat.run('personal','x',plan=[{'tool':'list_categories','args':{},'approved':True}])

    def test_natural_bulk_is_bounded_and_search_read_intent_does_not_mark(self):
        for index in range(21): self.add('extra'+str(index),'Sale event')
        self.assertEqual(self.chat.run('personal','Move messages AliExpress to Orders')['status'],'clarification')
        result=self.chat.run('personal',"Find university emails I haven't marked Done")
        self.assertEqual(result['results'][0]['tool'],'search_messages')

    def test_english_equivalents_and_unsupported_actions(self):
        for text in ('Move AliExpress emails to Orders','Move emails from AliExpress to Orders'):
            preview=self.chat.run('personal',text)['results'][0]['preview']
            self.assertEqual(len(preview['payload']['items']),3)
        result=self.chat.run('personal','Create a category named Shopping')
        self.assertEqual(result['results'][0]['preview']['payload']['args']['category_id'],'shopping')
        self.assertEqual(len(self.chat.run('personal','Show AliExpress emails')['results'][0]['messages']),3)
        for text in ('Send an email','احذف هذه الرسالة نهائيًا'):
            self.assertEqual(self.chat.run('personal',text)['status'],'clarification')


class ClassificationReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.add('uncertain','Your application update')
        self.messages.save(EmailMessage('unknown','personal','Unknown','Just a note'))
        self.review=ReviewMessages(self.assignments,self.management)

    def test_unclassified_and_ambiguous_queue_and_persistent_decision(self):
        rows=self.review.listing('personal')['items']
        self.assertEqual({r['message_id'] for r in rows},{'uncertain','unknown'})
        row=next(r for r in rows if r['message_id']=='uncertain')
        self.review.decide('personal','uncertain',row['fingerprint'],'reviewed')
        again=ReviewMessages(self.assignments,self.management)
        self.assertEqual([r['message_id'] for r in again.listing('personal')['items']],['unknown'])
        self.assertEqual(self.management.details('personal','uncertain')['status'],'needs_review')
        self.assertEqual(len(again.listing('personal')['history']),1)

    def test_changed_candidate_reappears_stale_and_account_decisions_rejected(self):
        row=self.assignments.classification_candidate('personal','uncertain')
        self.review.decide('personal','uncertain',row['fingerprint'],'rejected')
        self.add('uncertain','Your order has shipped - discount sale')
        self.assertIsNotNone(self.assignments.classification_candidate('personal','uncertain'))
        with self.assertRaises(ValueError): self.review.decide('personal','uncertain',row['fingerprint'],'reviewed')
        with self.assertRaises(LookupError): self.review.decide('university','unknown',row['fingerprint'],'reviewed')

    def test_assign_override_and_invalid_categories(self):
        row=self.assignments.classification_candidate('personal','unknown')
        for category in ('missing','job_applications'):
            with self.assertRaises(ValueError): self.review.decide('personal','unknown',row['fingerprint'],'assigned',category)
        self.review.decide('personal','unknown',row['fingerprint'],'assigned','orders')
        self.classifier.classify(self.messages.get('personal','unknown'))
        self.assertEqual(self.management.details('personal','unknown')['category_id'],'orders')
        self.assertEqual(self.management.details('personal','unknown')['source'],'manual')

    def test_coverage_distinguishes_unknown_labels_and_cursor_pages(self):
        for index in range(101):
            self.messages.save(EmailMessage('q'+str(index).zfill(3),'personal','Unknown','Note'))
        result=self.review.listing('personal')
        self.assertEqual(len(result['items']),100)
        self.assertIsNotNone(result['next_after'])
        self.assertEqual(len(self.review.listing('personal',result['next_after'])['items']),3)
        self.assertGreater(result['coverage']['unknown_labels'],0)
        self.assertIn('incomplete historical',result['coverage_note'])

    def test_manual_assignment_and_review_audit_rollback_together(self):
        row=self.assignments.classification_candidate('personal','unknown')
        original=self.assignments._put_in_transaction
        def fail_after_assignment(db,assignment):
            original(db,assignment)
            raise RuntimeError('Synthetic audit interruption')
        with patch.object(self.assignments,'_put_in_transaction',side_effect=fail_after_assignment):
            with self.assertRaises(RuntimeError): self.review.decide('personal','unknown',row['fingerprint'],'assigned','orders')
        self.assertIsNone(self.management.details('personal','unknown')['category_id'])
        self.assertEqual(self.assignments.classification_history('personal'),[])
        self.assertIsNotNone(self.assignments.classification_candidate('personal','unknown'))

    def test_placement_tabs_filter_before_pagination_and_refresh_counts(self):
        self.add('first-spam','Order confirmation',labels=('SPAM',))
        proposal=self.assignments.reviews('personal')[0]
        self.assignments.review('personal',proposal['id'],'dismissed','User checked sender and location')
        self.add('second-trash','Order confirmation',labels=('TRASH',))
        self.assertEqual(self.assignments.reviews('personal',limit=1,queue='spam'),[])
        self.assertEqual(self.assignments.reviews('personal',limit=1,queue='trash')[0]['message_id'],'second-trash')
        self.assertEqual(self.assignments.reviews('personal',limit=1,queue='history')[0]['status'],'dismissed')
        self.assertEqual(self.assignments.review_counts('personal')['trash'],1)


class GmailVerificationTests(unittest.TestCase):
    def test_mutation_response_does_not_replace_authoritative_readback(self):
        client=Mock()
        client.trash.return_value={'labelIds':['TRASH']}
        client.get_message.return_value={'label_ids':('INBOX',)}
        with self.assertRaises(ValueError): GmailActionAdapter(client).apply('trash','same',('INBOX',))
        client.get_message.assert_called_once_with('same',max_attempts=2)
        client.get_message.return_value={'label_ids':('TRASH',)}
        self.assertEqual(GmailActionAdapter(client).apply('trash','same',('INBOX',)),('TRASH',))

    def test_restore_is_verified_and_never_permanently_deleted(self):
        client=Mock()
        client.get_message.return_value={'label_ids':('SPAM',)}
        with self.assertRaises(ValueError): GmailActionAdapter(client).apply('restore','same',('SPAM',))
        client.delete.assert_not_called()
        client.get_message.return_value={'label_ids':('INBOX',)}
        self.assertEqual(GmailActionAdapter(client).apply('restore','same',('SPAM',)),('INBOX',))


class UnsubscribeTests(Base):
    def setUp(self):
        super().setUp()
        self.messages.record_list_headers('personal','sale1',{'list-id':'Offers <offers.example.org>','list-unsubscribe':'<https://example.org/u>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        self.verifier=Mock()
        self.verifier.verify.return_value={'identity':'offers.example.org','message_id':'sale1','host':'example.org','url':'https://example.org/u','raw_sha256':'abc','signer':'example.org','author_domain':'example.org','alignment':'exact','author_authorization':'signed_author_domain','signature_index':0,'policy_version':'rfc8058-human-third-party-v1'}
        self.sender=Mock(return_value={'state':'requested'})
        self.service=Unsubscribe(self.messages,self.management,self.store,self.verifier,self.sender)
        from email_agent.application.subscriptions import subscriptions
        self.id=subscriptions(self.messages.list_header_rows('personal'))[0]['id']

    def test_preview_never_posts_exact_target_confirmation_and_replay(self):
        preview=self.service.preview('personal',[self.id])
        self.sender.assert_not_called()
        for targets in ([],['other']):
            with self.assertRaises(ValueError): self.service.confirm('personal',preview['id'],True,targets)
        with self.assertRaises(ValueError): self.service.confirm('personal',preview['id'],False,[self.id])
        result=self.service.confirm('personal',preview['id'],True,[self.id])
        self.assertEqual(result['result']['items'][0]['status'],'requested')
        self.assertIn('not confirmed',result['result']['items'][0]['message'])
        self.service.confirm('personal',preview['id'],True,[self.id])
        self.sender.assert_called_once_with('https://example.org/u',verified_dkim_coverage=True)
        self.assertTrue(any(row['event']=='unsubscribe_started' for row in self.store.audit('personal',preview['id'])))

    def test_unverified_and_account_isolation_fail_closed(self):
        self.verifier.verify.side_effect=ValueError('DKIM failed')
        preview=self.service.preview('personal',[self.id])
        self.assertFalse(preview['payload']['items'][0]['eligible'])
        with self.assertRaises(ValueError): self.service.confirm('personal',preview['id'],True,[self.id])
        with self.assertRaises(LookupError): self.service.preview('university',[self.id])
        self.sender.assert_not_called()

    def test_expired_stale_changed_classification_and_unknown_outcome(self):
        preview=self.service.preview('personal',[self.id])
        self.verifier.verify.return_value={**self.verifier.verify.return_value,'url':'https://example.org/changed'}
        result=self.service.confirm('personal',preview['id'],True,[self.id])
        self.assertEqual(result['result']['items'][0]['status'],'skipped')
        self.sender.assert_not_called()
        preview=self.service.preview('personal',[self.id])
        self.sender.side_effect=TimeoutError()
        result=self.service.confirm('personal',preview['id'],True,[self.id])
        self.assertEqual(result['result']['items'][0]['status'],'uncertain')
        self.service.confirm('personal',preview['id'],True,[self.id]);self.sender.assert_called_once()
        preview=self.service.preview('personal',[self.id])
        with closing(sqlite3.connect(self.messages.database_path)) as db:
            with db: db.execute('UPDATE action_previews SET expires=0 WHERE id=?',(preview['id'],))
        with self.assertRaises(ValueError): self.service.confirm('personal',preview['id'],True,[self.id])

    def test_protected_messages_and_batch_bounds(self):
        self.messages.save(EmailMessage('sale1','personal','AliExpress','Your order has shipped',100,('IMPORTANT',)))
        with self.assertRaises(LookupError): self.service.preview('personal',[self.id])
        self.verifier.verify.assert_not_called()
        with self.assertRaises(ValueError): self.service.preview('personal',[self.id]*6)

    def test_cross_preview_claim_prevents_duplicate_list_requests(self):
        first=self.service.preview('personal',[self.id])
        second=self.service.preview('personal',[self.id])
        self.service.confirm('personal',first['id'],True,[self.id])
        result=self.service.confirm('personal',second['id'],True,[self.id])
        self.assertEqual(result['result']['items'][0]['status'],'skipped')
        self.sender.assert_called_once()
        self.assertEqual(self.store.subscription_states('personal')[self.id]['status'],'requested')
        self.assertEqual(self.store.subscription_states('university'),{})
        third=self.service.preview('personal',[self.id])
        self.assertFalse(third['payload']['items'][0]['eligible'])

    def test_bulk_partial_approval_and_failure_records_individual_outcomes(self):
        from email_agent.application.subscriptions import subscriptions
        self.messages.record_list_headers('personal','sale2',{'list-id':'Another <other.example.org>','list-unsubscribe':'<https://example.org/sale2>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        ids=[g['id'] for g in subscriptions(self.messages.list_header_rows('personal'))]
        self.verifier.verify.side_effect=lambda account,id,identity: {'identity':identity,'message_id':id,'host':'example.org','url':'https://example.org/'+id,'raw_sha256':'abc','signer':'example.org','author_domain':'example.org','alignment':'exact','author_authorization':'signed_author_domain','signature_index':0,'policy_version':'rfc8058-human-third-party-v1'}
        preview=self.service.preview('personal',ids)
        self.sender.side_effect=[{'state':'requested'},TimeoutError()]
        result=self.service.confirm('personal',preview['id'],True,ids)
        self.assertEqual(result['status'],'partial')
        self.assertEqual([r['status'] for r in result['result']['items']],['requested','uncertain'])
        self.assertEqual(self.sender.call_count,2)


class Batch31ApiTests(Base):
    def api(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from email_agent.api.batch3 import register_batch3_routes
        app=FastAPI()
        verifier=Mock(); sender=Mock(return_value={'state':'requested'})
        verifier.verify.return_value={'identity':'offers.example.org','message_id':'sale1','host':'example.org','url':'https://example.org/u','raw_sha256':'abc','signer':'example.org','author_domain':'example.org','alignment':'exact','author_authorization':'signed_author_domain','signature_index':0,'policy_version':'rfc8058-human-third-party-v1'}
        register_batch3_routes(app,self.messages,self.root,Mock(),unsubscribe_verifier=verifier,unsubscribe_sender=sender)
        return TestClient(app,client=('127.0.0.1',1234)),sender

    def test_classification_api_persists_account_scoped_decision_and_counts(self):
        self.add('ambiguous','Your application update')
        with self.api()[0] as api:
            row=api.get('/api/classification-review/personal').json()['items'][0]
            payload={'fingerprint':row['fingerprint'],'decision':'reviewed'}
            path='/api/classification-review/personal/ambiguous/decision'
            self.assertEqual(api.post(path,json=payload).status_code,403)
            self.assertEqual(api.post(path,json=payload,headers=self.headers).status_code,200)
            self.assertEqual(api.get('/api/classification-review/personal').json()['items'],[])
            self.assertEqual(api.get('/api/review/personal').json()['counts']['category'],0)
            self.assertEqual(api.post(path,json=payload,headers=self.headers).status_code,400)
            self.assertEqual(api.post('/api/classification-review/university/ambiguous/decision',json=payload,headers=self.headers).status_code,404)

    def test_unsubscribe_api_strict_payload_csrf_and_persisted_requested_state(self):
        self.messages.record_list_headers('personal','sale1',{'list-id':'Offers <offers.example.org>','list-unsubscribe':'<https://example.org/u>','list-unsubscribe-post':'List-Unsubscribe=One-Click'})
        api,sender=self.api()
        with api:
            id=next(item['id'] for item in api.get('/api/subscriptions/personal').json()['items'] if item['kind']=='mailing_list')
            self.assertEqual(api.post('/api/subscriptions/personal/preview',json={'subscription_ids':[id]}).status_code,403)
            self.assertEqual(api.post('/api/subscriptions/personal/preview',json={'subscription_ids':[id],'url':'https://evil.invalid'},headers=self.headers).status_code,422)
            preview=api.post('/api/subscriptions/personal/preview',json={'subscription_ids':[id]},headers=self.headers).json()
            path='/api/subscriptions/personal/actions/'+preview['id']+'/confirm'
            self.assertEqual(api.post(path,json={'approved':'yes','approved_targets':[id]},headers=self.headers).status_code,422)
            self.assertEqual(api.post(path,json={'approved':True,'approved_targets':[id]},headers=self.headers).status_code,200)
            self.assertEqual(next(item for item in api.get('/api/subscriptions/personal').json()['items'] if item['id']==id)['request_state']['status'],'requested')
            self.assertEqual(api.post(path.replace('/personal/','/university/'),json={'approved':True,'approved_targets':[id]},headers=self.headers).status_code,404)
            sender.assert_called_once()


class UnsubscribeTransportTests(unittest.TestCase):
    def test_ipv6_mapped_private_and_dns_rebinding_are_blocked(self):
        import socket
        from email_agent.infrastructure.http.safe_unsubscribe import public_destination
        for address in ('::1','::ffff:127.0.0.1','fc00::1','fe80::1','169.254.169.254','192.168.1.2'):
            resolver=Mock(return_value=[(socket.AF_INET6,socket.SOCK_STREAM,6,'',(address,443))])
            with self.assertRaises(ValueError): public_destination('https://example.org/u',resolver)
            resolver.assert_called_once()

    def test_https_connection_uses_checked_ip_and_original_tls_hostname(self):
        from email_agent.infrastructure.http.safe_unsubscribe import PinnedHTTPSConnection
        with patch('email_agent.infrastructure.http.safe_unsubscribe.socket.create_connection') as connect:
            connection=PinnedHTTPSConnection('example.org','8.8.8.8')
            connection._context=Mock()
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8',443),timeout=10)
            connection._context.wrap_socket.assert_called_once_with(connect.return_value,server_hostname='example.org')
            connection.close()

    def test_verified_post_is_fixed_payload_no_cookies_and_no_redirect_following(self):
        from email_agent.infrastructure.http.safe_unsubscribe import post_verified_one_click
        with patch('email_agent.infrastructure.http.safe_unsubscribe.public_destination',return_value=('example.org','8.8.8.8','/u')),patch('email_agent.infrastructure.http.safe_unsubscribe.PinnedHTTPSConnection') as factory:
            response=factory.return_value.getresponse.return_value
            response.status=204
            self.assertEqual(post_verified_one_click('https://example.org/u',verified_dkim_coverage=True)['state'],'requested')
            args=factory.return_value.request.call_args
            self.assertEqual(args.args,('POST','/u'))
            self.assertEqual(args.kwargs['body'],b'List-Unsubscribe=One-Click')
            self.assertNotIn('Cookie',args.kwargs['headers'])
            factory.return_value.request.assert_called_once()
            factory.return_value.close.assert_called_once()


class DKIMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try: import dkim
        except ImportError: raise unittest.SkipTest('Install requirements-unsubscribe.txt to verify cryptographic tests')
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import base64
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption())
        cls.dns=b'v=DKIM1; k=rsa; p='+base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo))
        cls.body=b'From: Offers <offers@example.org>\r\nSubject: Offers\r\nList-ID: Offers <offers.example.org>\r\nList-Unsubscribe: <https://example.org/u>,\r\n <mailto:leave@example.org>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\nA promotion\r\n'

    def signed(self, headers=None, **kwargs):
        import dkim
        return dkim.sign(self.body,b'selector',b'example.org',self.key,include_headers=headers or [b'from',b'subject',b'list-id',b'list-unsubscribe',b'list-unsubscribe-post'],**kwargs)+self.body

    def verify(self, raw, identity='offers.example.org', dns=None):
        client=Mock(); client.get_raw_message.return_value=raw
        return DKIMUnsubscribeVerifier(lambda account:client,lambda *a,**kw:self.dns if dns is None else dns).verify('personal','sale1',identity)

    def test_independent_valid_signature_and_folded_headers(self):
        proof=self.verify(self.signed())
        self.assertEqual(proof['url'],'https://example.org/u')
        self.assertEqual(proof['signer'],'example.org')
        self.assertEqual(len(proof['raw_sha256']),64)

    def test_tampered_body_header_and_wrong_dns_key_fail(self):
        for raw in (self.signed().replace(b'A promotion',b'A transaction'),self.signed().replace(b'https://example.org/u',b'https://evil.example/u')):
            with self.assertRaises(ValueError): self.verify(raw)
        with self.assertRaises(ValueError): self.verify(self.signed(),dns=b'v=DKIM1; p=')

    def test_missing_coverage_body_length_and_identity_fail(self):
        with self.assertRaises(ValueError): self.verify(self.signed([b'from',b'subject']))
        with self.assertRaises(ValueError): self.verify(self.signed(length=True))
        with self.assertRaises(ValueError): self.verify(self.signed(),'other.example.org')
        with self.assertRaises(ValueError): self.verify(self.body)

    def test_duplicate_headers_and_fake_authentication_results_fail(self):
        with self.assertRaises(ValueError): self.verify(b'List-Unsubscribe: <https://evil.example/u>\r\n'+self.signed())
        with self.assertRaises(ValueError): self.verify(b'Authentication-Results: mx.google.com; dkim=pass\r\n'+self.body)


if __name__=='__main__': unittest.main()
