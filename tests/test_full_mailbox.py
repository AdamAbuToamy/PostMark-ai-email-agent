"""Whole-mailbox continuations, MIME recovery and independent cleanup authority."""
import base64
import hashlib
import json
import unittest
from unittest.mock import Mock,patch
from email_agent.infrastructure.gmail.content_reader import parse_evidence_message,GmailEvidenceReader,MIMECharsetError
from email_agent.infrastructure.gmail.client import RawMessageTooLarge
from email_agent.infrastructure.database.continuous_store import ContinuousStore
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.infrastructure.database.automation_ai_store import AutomationAIStore
from email_agent.application.promotional_cleanup import PromotionalCleanup
from email_agent.application.mail_management import MailManagement
import test_local_organization_agent as fixture


class FullMailboxTests(unittest.TestCase):
    setUp=fixture.LocalAgentTests.setUp
    add=fixture.LocalAgentTests.add
    seed=fixture.LocalAgentTests.seed
    prepare=fixture.LocalAgentTests.prepare

    def work(self):
        SQLiteHistoryStore(self.path)
        work=ContinuousStore(self.path,self.messages);work.initialize();work.configure('personal',True)
        return work

    def test_mixed_utf8_punctuation_and_declared_html_charset_preserve_unicode(self):
        raw=b'Content-Type: text/plain; charset=utf-8\r\n\r\n'+('עברית العربية: payment received '.encode()+b'\x96 thank you')
        value=parse_evidence_message(raw)
        self.assertIn('עברית العربية',value['body']);self.assertIn('–',value['body']);self.assertTrue(value['complete'])
        self.assertIn('utf-8+windows-punctuation',value['decodings'])
        html=b'Content-Type: text/html; charset=utf-8\r\n\r\n'+('<meta charset="windows-1255"><p>הסיסמה שונתה</p>'.encode('cp1255'))
        self.assertIn('הסיסמה שונתה',parse_evidence_message(html)['body'])
        with self.assertRaises(MIMECharsetError):parse_evidence_message(b'Content-Type: text/plain; charset=utf-8\r\n\r\n\xff\xfe\x81')

    def test_oversize_binary_original_fetches_text_only_not_binary_attachment(self):
        client=Mock();client.get_raw_message.side_effect=RawMessageTooLarge('large')
        text='Your parcel is ready for pickup. החבילה ממתינה לאיסוף.'
        client.get_full_message.return_value={'payload':{'mimeType':'multipart/mixed','parts':[
            {'mimeType':'text/plain','body':{'attachmentId':'text-only','size':len(text.encode())}},
            {'mimeType':'application/pdf','filename':'sensitive.pdf','body':{'attachmentId':'do-not-read','size':20_000_000}}]}}
        client.get_attachment.return_value={'data':base64.urlsafe_b64encode(text.encode()).decode()}
        evidence=GmailEvidenceReader(client).read('x')
        self.assertEqual(evidence['body'],text);self.assertEqual(evidence['source'],'gmail_full_text_parts')
        client.get_attachment.assert_called_once_with('x','text-only');self.assertTrue(evidence['complete'])

    def test_parser_migration_preserves_attempts_and_runs_once(self):
        self.add('bad','Account update');work=self.work();work.seed('personal')
        with work.connect() as db:
            db.execute("DELETE FROM automation_versions WHERE version=4")
            db.execute("UPDATE mail_work SET fingerprint='mime-full-1',status='failed',attempts=1,wire_attempts=3,error_kind='parsing_failure' WHERE stage='body'")
        work.initialize()
        with work.connect() as db:
            row=db.execute("SELECT * FROM mail_work WHERE stage='body'").fetchone()
            self.assertEqual((row['status'],row['attempts'],row['wire_attempts']),('retry',1,3))
            db.execute("UPDATE mail_work SET status='failed' WHERE stage='body'")
        work.initialize()
        with work.connect() as db:self.assertEqual(db.execute("SELECT status FROM mail_work WHERE stage='body'").fetchone()[0],'failed')

    def test_account_transport_is_reused_with_current_item_budget_and_isolation(self):
        from email_agent.workers.continuous_mail import AccountReaders
        store=Mock();one={'stage':'metadata','message_id':'one'};two={'stage':'body','message_id':'two'}
        with patch('email_agent.workers.continuous_mail.client',return_value=Mock()) as factory:
            pool=AccountReaders(self.root,'personal',store,'run')
            first=pool('personal',one);second=pool('personal',two)
            self.assertIs(first,second);factory.assert_called_once()
            factory.call_args.args[2]('gmail')
            store.reserve_read.assert_called_once_with('run','personal','body_gmail',two)
            with self.assertRaises(ValueError):pool('university',two)

    def test_coverage_counts_discovered_unindexed_bodies_not_just_local_cache(self):
        self.add('one','Hello');work=self.work()
        SQLiteHistoryStore(self.path).commit_observed_page('personal',None,[],None,['one','remote-only'])
        work.seed('personal');coverage=work.progress('personal')['coverage']
        self.assertEqual(coverage['discovered'],2);self.assertEqual(coverage['bodies_remaining'],2)
        self.assertGreater(coverage['waiting_gmail'],0)

    def test_taxonomy_walk_and_sections_reach_beyond_old_preview_ceiling(self):
        self.add('zz-tail','Your package is ready for pickup')
        with self.store.connect() as db,db:
            db.executemany("INSERT INTO messages(account_id,message_id,sender,subject,label_ids,labels_known) VALUES('personal',?,'x@example.test','Order confirmation','[\"INBOX\"]',1)",[(f'a{i:05}',) for i in range(10001)])
            db.executemany("INSERT INTO message_category_assignments VALUES('personal',?,'orders','automatic','classified','confirmed order','test')",[(f'a{i:05}',) for i in range(10001)])
        page=self.management.mailbox('personal','orders','all',section='pickup')
        self.assertEqual(page['total'],1);self.assertFalse(page['discovery']['truncated'])
        self.assertEqual(page['discovery']['analyzed'],10002)
        with self.store.connect() as db,db:
            db.execute("INSERT OR REPLACE INTO organizer_walk VALUES('personal','local','a09999')")
        self.assertIn('zz-tail',[r['message_id'] for r in self.store.walk('personal',3)])
        self.assertEqual(self.engine.overview('personal')['indexed'],10002)

    def test_taxonomy_candidates_use_complete_semantics_not_lexical_groups(self):
        for i in range(3):
            self.add(str(i),'Same unknown repeated concept')
            self.messages.record_search_content('personal',str(i),'Unknown complete text',complete=True)
        self.assertEqual(self.store.planning_groups('personal'),[])
        self.assertEqual(self.engine.organize_local('personal')['proposals'],[])

    def test_impossible_ai_budget_does_not_create_more_scopes_or_model_requests(self):
        work=self.work();ai=AutomationAIStore(self.path,self.messages);ai.initialize_ai()
        ai.grant('g','personal','test-model',200,50,300000,60000,approval_reference='test')
        ai.reserve_ai('g','used','count',273833,0,[])
        self.assertFalse(ai.minimum_scope_fits(ai.current_grant('personal')))

    def test_unknown_cloud_attempt_does_not_resubmit_same_message_after_restart(self):
        self.add('held','Unfamiliar complete message')
        self.messages.record_search_content('personal','held','Independent evidence awaiting interpretation.',complete=True)
        work=self.work();work.seed('personal')
        ai=AutomationAIStore(self.path,self.messages);ai.initialize_ai()
        with ai.connect() as db:
            db.execute("INSERT INTO mail_work(account_id,message_id,stage,fingerprint) VALUES('personal','held','semantic','fixture')")
        scope=self.engine.preview(['personal'],1,1,content_mode='full')
        ai.grant('g','personal','test-model',20,10,300000,60000,approval_reference='fixture')
        ai.reserve_ai('g',scope['id'],'generate',1000,1000,['held'])
        restarted=AutomationAIStore(self.path,self.messages)
        self.assertEqual(restarted.select_semantic('personal'),[])
        # A known failed attempt may enter the separately bounded retry policy;
        # an interrupted request with unknown provider outcome may not replay.
        restarted.record_ai(scope['id'],'generate','failed')
        self.assertEqual(restarted.select_semantic('personal'),['held'])

    def test_historical_candidates_never_requeue_automatic_cleanup_or_bypass_user_keep(self):
        message=self.add('old','Special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        self.messages.record_search_content('personal','old','Special discount sale on clothing. Shop now.',complete=True)
        self.classifier.classify(message)
        with self.cleanup.connect() as db,db:
            db.execute("INSERT INTO cleanup_protections(account_id,scope,identity,reason) VALUES('personal','message','old','Historical discovery; no historical cleanup approval')")
            db.execute("INSERT INTO cleanup_policy VALUES('personal',1,'automatic','[]','r',0,?,1,NULL)",(message.internal_date_ms+100,))
        cleanup=PromotionalCleanup(self.messages,self.classifier.assignments,self.cleanup,Mock(),lambda a:True)
        service=MailManagement(self.messages,self.classifier,cleanup,Mock(),Mock())
        self.assertEqual(service.evaluate('personal','old')['verdict'],'eligible')
        self.assertFalse(cleanup.reconsider('personal','old'))
        self.cleanup.management.protect('personal','sender','store@example.com','User protection: sender')
        self.assertEqual(service.evaluate('personal','old')['verdict'],'protected')
        self.assertEqual(service.evaluate('personal','old')['protection_kind'],'user')

    def test_future_skipped_ad_is_reconsidered_after_complete_body_without_executing_gmail(self):
        message=self.add('future','Special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        body='Special discount sale on clothing. Shop now.'
        self.messages.record_search_content('personal','future',body,complete=True);self.classifier.classify(message)
        with self.cleanup.connect() as db,db:
            db.execute("INSERT INTO cleanup_policy VALUES('personal',1,'automatic','[]','r',0,0,1,NULL)")
            db.execute("INSERT INTO cleanup_items(account_id,message_id,revision,status) VALUES('personal','future','r','skipped')")
            db.execute("INSERT INTO cleanup_protections(account_id,scope,identity,reason) VALUES('personal','message','future','Semantic sensitivity or unresolved meaning; retain for organization')")
        provider=Mock();cleanup=PromotionalCleanup(self.messages,self.classifier.assignments,self.cleanup,provider,lambda a:True)
        self.assertTrue(cleanup.reconsider('personal','future'));self.assertEqual(self.cleanup.item('personal','future')['status'],'queued')
        self.assertIsNone(self.cleanup.protection('personal',message));provider.assert_not_called()
        self.assertFalse(cleanup.reconsider('personal','future'))

        with self.cleanup.connect() as db,db:db.execute("UPDATE cleanup_items SET status='succeeded' WHERE message_id='future'")
        self.assertFalse(cleanup.reconsider('personal','future'))

    def test_approved_full_body_pattern_organizes_new_mail_without_model_or_outcome_copy(self):
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        p=self.prepare();self.store.local_policy('personal',True,True)
        agent=LocalOrganizationAgent(self.engine,self.store);result=agent.step('personal')
        target=result['results'][0]['category_ids'][0]
        self.add('repeat','A new observation',sender='different@example.test')
        self.messages.record_search_content('personal','repeat','Independent river field survey 99. Habitat sampling report from this expedition.',complete=True)
        self.classifier.classify(self.messages.get('personal','repeat'))
        row=self.store.message_row('personal','repeat')
        self.assertEqual(row['decision']['category_id'],target);self.assertTrue(row['semantic']['understood'])
        self.assertTrue(row['semantic']['pattern_source']);self.assertIsNone(row['semantic']['status'])
        self.assertEqual(self.model.generate_organization.call_count,1)
        self.store.local_policy('personal',False)
        self.classifier.classify(self.messages.get('personal','repeat'))
        self.assertEqual(self.store.message_row('personal','repeat')['decision']['category_id'],target)
        self.store.local_policy('personal',True,True)
        self.add('negated','A different observation')
        self.messages.record_search_content('personal','negated','Independent river field survey 99. This is NOT a habitat sampling report from this expedition.',complete=True)
        self.classifier.classify(self.messages.get('personal','negated'))
        self.assertEqual(self.store.message_row('personal','negated')['decision']['category_id'],'other')

    def test_two_independent_strong_body_interpretations_can_publish_a_new_purpose(self):
        for i in range(2):
            self.add(str(i),'Independent unfamiliar report',sender=f'participant{i}@example.test')
            self.messages.record_search_content('personal',str(i),'The expedition specimen archive is available for independent scientific review.',complete=True)
        def result(system,data,schema):
            return json.dumps(dict(frames=[dict(id=m['id'],category_id=None,primary_intent='Experimental archives',message_type='Specimen note',status=None,entity=None,sensitivities=['personal'],confidence=.94,evidence=[dict(field='excerpt',quote='The expedition specimen archive is available')],attributes=[],related_ids=[]) for m in data['messages']],changes=[]))
        self.model.generate_organization.side_effect=result
        self.store.local_policy('personal',True,True)
        scope=self.engine.preview(['personal'],1,2,content_mode='full');self.engine.run(scope['id'],True)
        report=self.engine.organize_local('personal')
        self.assertEqual(report['actions']['applied'],1)
        category=next(c for c in self.management.categories('personal') if c['name']=='Experimental archives')
        self.assertEqual(category['counts']['all'],2)

    def test_promotional_calls_to_action_do_not_override_actual_financial_protection(self):
        from email_agent.application.promotional_cleanup import eligible_ad
        from email_agent.application.mail_management import assess
        ad=self.add('cta','Order now: special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        body='Special discount sale on clothing. Shop now and order online today.'
        self.messages.record_search_content('personal','cta',body,complete=True);self.classifier.classify(ad)
        decision=self.classifier.assignments.message_details('personal','cta')
        self.assertTrue(eligible_ad(ad,decision,[],-1,body)[0])
        self.assertEqual(assess(ad,decision,[],body=body)['verdict'],'eligible')
        for sensitive in ['Receipt. Total paid: $12.00. Shop now for a discount.','Your password has been changed. Shop now for a discount.']:
            self.assertFalse(eligible_ad(ad,decision,[],-1,sensitive)[0])
        self.assertFalse(eligible_ad(ad,decision,[],-1,None)[0])

    def test_changed_body_does_not_keep_old_organized_count_or_semantic_ui_badge(self):
        from email_agent.application.continuous_mail import ContinuousMail
        self.classifier.registry.propose('security','Security','Verified authentication notices')
        self.classifier.registry.approve(self.classifier.registry.proposals()[0]['id'])
        self.add('changed','Account notice')
        self.messages.record_search_content('personal','changed','Your password has been changed.',complete=True)
        work=self.work();pipeline=ContinuousMail(work,self.messages,self.classifier,Mock())
        pipeline.step('personal',0,0,5)
        self.assertEqual(work.progress('personal')['organized'],1)
        self.messages.record_search_content('personal','changed','The content is now genuinely ambiguous and needs a new interpretation.',complete=True)
        self.assertEqual(work.progress('personal')['organized'],0)
        page=self.management.mailbox('personal','security','all')
        self.assertIsNone(page['items'][0].get('semantic'))

    def test_legacy_approved_publication_is_scoped_from_creation_audit_and_membership(self):
        p=self.prepare();self.engine.decide('personal',p['id'],p['fingerprint'],True)
        target=next(c['id'] for c in self.store.catalog('personal') if c['name']=='Field research')
        with self.store.connect() as db,db:
            db.execute('DELETE FROM taxonomy.local_category_accounts WHERE category_id=?',(target,))
            db.execute('DELETE FROM organizer_schema_versions WHERE version=7')
        self.assertIn(target,{c['id'] for c in self.store.catalog('university')})
        self.store.initialize()
        self.assertNotIn(target,{c['id'] for c in self.store.catalog('university')})
        self.assertEqual(self.management.mailbox('personal',target,'all')['total'],4)
