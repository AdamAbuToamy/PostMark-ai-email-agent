import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from email_agent.application.classify_messages import decide
from email_agent.application.local_understanding import understand, cluster_identity
from email_agent.application.mail_management import assess
from email_agent.application.organization import Organization
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.intelligence import build_classifier


class UnderstandingTests(unittest.TestCase):
    def msg(self, subject, sender='news@github.com', labels=('INBOX',), headers=None):
        return EmailMessage('1','personal',sender,subject,100,labels,headers)

    def test_realistic_message_level_cases_and_cleanup_protections(self):
        cases = [
            ('GitHub Actions workflow failed','postmark-synthetic-2@github.com','other','classified',False),
            ('Successful CI workflow','postmark-synthetic-2@github.com','other','classified',False),
            ('GitHub security alert','postmark-synthetic-2@github.com','security','classified',False),
            ('New pull request (#42)','postmark-synthetic-2@github.com','other','classified',False),
            ('AliExpress discount sale','news@aliexpress.com','promotions','classified',True),
            ('AliExpress order confirmation','news@aliexpress.com','orders','classified',False),
            ('Payment receipt - discount sale','billing@store.com','bills','classified',False),
            ('University course registration','admin@college.ac.il','university','classified',False),
            ('Re: dinner tomorrow','friend@example.com','other','needs_review',False),
            ('Thank you for applying','hr@company.com','job_applications','classified',False),
            ('Security alert: sale discount','news@store.com','security','classified',False),
        ]
        for subject,sender,expected,status,eligible in cases:
            with self.subTest(subject=subject):
                msg=self.msg(subject,sender,('INBOX','CATEGORY_PROMOTIONS'))
                category,result,reason=decide(msg)
                self.assertEqual((category,result),(expected,status))
                evaluation=assess(msg,dict(category_id=category,status=result,source='automatic'),[],body=subject)
                self.assertEqual(evaluation['verdict']=='eligible',eligible)

    def test_github_subtypes_never_share_security_or_pr_cluster(self):
        keys=[]
        for subject in ('workflow failed','workflow succeeded','Security alert','Pull request #32'):
            msg=self.msg(subject);category,status,reason=decide(msg)
            keys.append(cluster_identity(msg,category,reason)[0])
        self.assertEqual(len(set(keys)),4)
        self.assertIsNone(understand(self.msg('workflow failed','fakegithub.com@example.com')))

    def test_list_identity_does_not_mix_transaction_and_advertisement(self):
        headers=(('list-id','mixed-list'),)
        keys=[]
        for subject in ('Order confirmation','Big discount sale'):
            msg=self.msg(subject,'mixed@example.com',headers=headers)
            c,_,r=decide(msg);keys.append(cluster_identity(msg,c,r)[0])
        self.assertNotEqual(*keys)

    def test_unknown_labels_allow_classification_but_never_cleanup(self):
        message=self.msg('Discount sale','news@example.com',None,(('list-id','offers.example'),))
        c,s,r=decide(message)
        self.assertEqual((c,s),('promotions','classified'))
        self.assertEqual(assess(message,dict(category_id=c,status=s,source='automatic'),[])['verdict'],'needs_review')


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'mail.db'
        self.messages=SQLiteMessageRepository(self.path);self.messages.initialize()
        self.classifier=build_classifier(self.messages)
        cleanup=SQLiteCleanupStore(self.path,Path(self.tmp.name)/'worker.db')
        self.store=SQLiteOrganizationStore(self.path,cleanup.management)
        self.service=Organization(self.store,self.classifier.registry,lambda _:[])
        for i,subject in enumerate(('workflow failed','workflow failed','workflow succeeded','workflow succeeded','Security alert','Security alert','Pull request #3','Pull request #4')):
            self.add(str(i),subject)

    def add(self,id,subject,account='personal'):
        msg=EmailMessage(id,account,'postmark-synthetic-2@github.com',subject,1,('INBOX','CATEGORY_PROMOTIONS'))
        self.messages.save(msg);self.classifier.classify(msg)
        return msg

    def group(self,key):
        return next(g for g in self.service.overview('personal')['groups'] if g['key']==key)

    def test_group_preview_approval_future_matches_and_account_isolation(self):
        group=self.group('github_ci_failed')
        self.assertEqual(group['count'],2);self.assertEqual(group['eligible'],0)
        result=self.service.decide_group('personal',group['key'],group['fingerprint'],'approve')
        self.assertEqual(result['affected'],2)
        self.add('future','workflow failed')
        self.add('university','workflow failed','university')
        self.assertEqual(self.classifier.assignments.message_details('personal','future')['category_id'],'github_ci')
        self.assertEqual(self.classifier.assignments.message_details('university','university')['category_id'],'other')
        self.assertEqual(self.classifier.assignments.message_details('personal','2')['category_id'],'other')
        self.add('future-security','Security alert')
        self.assertEqual(self.classifier.assignments.message_details('personal','future-security')['category_id'],'security')

    def test_stale_preview_rejected_and_manual_corrections_preserved(self):
        group=self.group('github_ci_failed')
        self.classifier.manual('personal','0','orders','User verified actual purchase')
        with self.assertRaises(ValueError):self.service.decide_group('personal',group['key'],group['fingerprint'],'approve')
        self.classifier.run('personal')
        self.assertEqual(self.classifier.assignments.message_details('personal','0')['category_id'],'orders')

    def test_bulk_protection_is_durable_and_audited(self):
        group=self.group('github_ci_failed')
        self.service.decide_group('personal',group['key'],group['fingerprint'],'exclude')
        row=self.store.snapshot('personal',100)[0][0]
        self.assertEqual(row.protection,'User group decision: exclude')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('select count(*) from organization_audit').fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from cleanup_protections').fetchone()[0],2)

    def test_commit_rollback_on_concurrent_manual_change(self):
        items,_=self.store.snapshot('personal',100)
        self.classifier.manual('personal','1','orders','User purchase')
        with self.assertRaises(ValueError):self.store.apply('personal','github_ci_failed','approve','other',items[:2])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('select count(*) from organization_patterns').fetchone()[0],0)

    def test_group_pagination_and_truncated_approval_fail_closed(self):
        first=self.service.overview('personal',0,1)
        self.assertEqual(len(first['groups']),1);self.assertEqual(first['next_offset'],1)
        self.service.LIMIT=2
        group=self.group('github_ci_failed')
        with self.assertRaises(ValueError):self.service.decide_group('personal',group['key'],group['fingerprint'],'approve')


class HistoryObservationTests(unittest.TestCase):
    def test_unique_counts_atomic_checkpoint_restart_and_missing_message(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'mail.db';messages=SQLiteMessageRepository(path);messages.initialize()
            classifier=build_classifier(messages);store=SQLiteHistoryStore(path)
            msg=EmailMessage('1','personal','s','Order confirmation',1,('TRASH',))
            store.commit_observed_page('personal',None,[msg],'page2',['1','gone'])
            classifier.classify(msg)
            coverage=store.coverage('personal')
            self.assertEqual((coverage['discovered'],coverage['fetched'],coverage['indexed'],coverage['classified']),(2,1,1,1))
            self.assertEqual(coverage['unavailable_observed'],1);self.assertIsNone(coverage['remaining_pages'])
            with self.assertRaises(RuntimeError):store.commit_observed_page('personal',None,[],None,['bad'])
            self.assertEqual(store.coverage('personal')['discovered'],2)
            store.commit_observed_page('personal','page2',[msg],None,['1'])
            self.assertEqual(store.coverage('personal')['discovered'],2)
            self.assertEqual(store.coverage('personal')['remaining_pages'],0)
            store.restart('personal')
            self.assertEqual(store.coverage('personal')['discovered'],0)
            self.assertIsNotNone(messages.get('personal','1'))
            self.assertEqual(store.coverage('university')['discovered'],0)

    def test_legacy_checkpoint_metrics_are_explicitly_partial(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'mail.db';store=SQLiteHistoryStore(path)
            store.commit_page('personal',None,[],'next')
            reopened=SQLiteHistoryStore(path)
            self.assertTrue(reopened.coverage('personal')['metrics_partial'])

class SemanticTests(unittest.TestCase):
    def setUp(self):
        from email_agent.application.semantic_organization import SemanticOrganization
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'mail.db'
        self.messages=SQLiteMessageRepository(self.path);self.messages.initialize()
        build_classifier(self.messages)
        cleanup=SQLiteCleanupStore(self.path,Path(self.tmp.name)/'worker.db')
        self.store=SQLiteOrganizationStore(self.path,cleanup.management)
        for id in ('1','2'):
            self.messages.save(EmailMessage(id,'personal','Private Person <person@example.com>','Unclear recurring update',1,None))
        self.settings=dict(model='mock-model',configured=True,enabled=True,processing_consent=True,
            usage=dict(provider_policy=dict(free_tier_attested=True,paid_approved=False,model='mock-model')),connection=dict(blocking_reasons=[]))
        self.model=Mock()
        self.response=dict(proposed_category='other',confidence=.7,reasons=['Insufficient context'],protected_evidence=[],suggested_new_category=None,requires_review=False)
        self.model.generate_semantic.return_value=json.dumps(self.response)
        self.model.last_usage={}
        self.reserve=Mock(return_value=True);self.usage=Mock()
        self.service=SemanticOrganization(self.messages,self.store,lambda _:self.settings,self.reserve,lambda:self.model,self.usage)

    def test_preview_has_no_calls_and_omits_bodies_and_addresses(self):
        plan=self.service.preview('personal',['1'],1)
        self.assertEqual(plan['message_count'],1);self.assertEqual(plan['max_output_tokens_total'],600)
        self.assertNotIn('person@',json.dumps(plan));self.assertNotIn('Private Person',json.dumps(plan))
        self.model.generate_semantic.assert_not_called();self.reserve.assert_not_called()
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],False)
        self.model.generate_semantic.assert_not_called()

    def test_cache_dedup_shared_reservation_replay_and_account_isolation(self):
        plan=self.service.preview('personal',['1','2'],2)
        with self.assertRaises(ValueError):self.service.run('university',plan['id'],True)
        result=self.service.run('personal',plan['id'],True)
        self.assertTrue(result['completed']);self.assertEqual(result['requests'],1)
        self.assertEqual([i['cached'] for i in result['items']],[False,True])
        self.assertTrue(all(i['requires_review'] for i in result['items']))
        self.reserve.assert_called_once_with('personal','organization-semantic')
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],True)
        self.assertEqual(self.model.generate_semantic.call_count,1)

    def test_free_only_consent_model_and_quota_fail_closed(self):
        plan=self.service.preview('personal',['1'],1)
        self.settings['processing_consent']=False
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],True)
        self.settings['processing_consent']=True
        self.settings['usage']['provider_policy']['paid_approved']=True
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],True)
        self.settings['usage']['provider_policy']['paid_approved']=False
        self.reserve.return_value=False
        result=self.service.run('personal',plan['id'],True)
        self.assertFalse(result['completed']);self.assertEqual(result['requests'],0)
        self.model.generate_semantic.assert_not_called()

    def test_failure_consumes_cap_without_retry_or_success_claim(self):
        plan=self.service.preview('personal',['1','2'],2)
        self.model.generate_semantic.side_effect=TimeoutError()
        result=self.service.run('personal',plan['id'],True)
        self.assertFalse(result['completed']);self.assertEqual(result['errors'],1)
        self.assertEqual(self.model.generate_semantic.call_count,1)
        self.assertEqual(self.store.semantic_cache('personal','missing'),None)

    def test_summary_changes_invalidate_preview(self):
        plan=self.service.preview('personal',['1'],1)
        self.messages.save(EmailMessage('1','personal','s','Changed subject'))
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],True)
        self.reserve.assert_not_called()

    def test_schema_rejects_tool_injection_nan_and_unbounded_fields(self):
        from email_agent.application.semantic_organization import SemanticAdvice
        for changes in (dict(confidence=True),dict(confidence=float('nan')),dict(reasons=['x'*241]),dict(proposed_category='execute_shell'),dict(requires_review='false'),dict(tool='trash')):
            with self.subTest(changes=changes),self.assertRaises(ValueError):SemanticAdvice.parse(json.dumps({**self.response,**changes}))

    def test_budget_and_classified_messages_are_not_submitted(self):
        for ids,cap in [(['1','2'],1),(['1','1'],2),(['1'],11)]:
            with self.assertRaises(ValueError):self.service.preview('personal',ids,cap)
        self.messages.save(EmailMessage('order','personal','s','Order confirmation'))
        with self.assertRaises(ValueError):self.service.preview('personal',['order'],1)
        self.model.generate_semantic.assert_not_called()

class IncrementalLabelTests(unittest.TestCase):
    def setUp(self):
        from email_agent.application.incremental_labels import IncrementalLabels
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'mail.db';self.messages=SQLiteMessageRepository(self.path);self.messages.initialize()
        self.store=SQLiteHistoryStore(self.path);self.reader=Mock()
        self.service=IncrementalLabels(self.reader,self.store)
        for i in range(7):self.messages.save(EmailMessage(str(i),'personal','s','Cached mail',1,('TRASH',)))
        self.messages.save(EmailMessage('inbox','personal','s','Inbox',1,('INBOX',)))
        self.messages.save(EmailMessage('university','university','s','Other account',1,('SPAM',)))

    def test_bounded_rotating_refresh_updates_actual_labels_and_isolates_accounts(self):
        self.reader.get_metadata.side_effect=lambda account,id:EmailMessage(id,account,'s','Cached mail',1,('TRASH',))
        self.assertEqual(len(self.service.execute('personal').messages),5)
        self.assertEqual([c.args[1] for c in self.reader.get_metadata.call_args_list],['0','1','2','3','4'])
        self.reader.reset_mock()
        self.service.execute('personal')
        self.assertEqual([c.args[1] for c in self.reader.get_metadata.call_args_list],['5','6'])
        self.reader.reset_mock()
        self.service.execute('personal')
        self.assertEqual(self.reader.get_metadata.call_args_list[0].args[1],'0')
        self.assertEqual(self.messages.get('university','university').label_ids,('SPAM',))

    def test_404_and_transient_failures_preserve_cached_evidence(self):
        def read(account,id):
            if id=='0':raise LookupError()
            if id=='1':raise TimeoutError()
            return EmailMessage(id,account,'s','Cached mail',1,())
        self.reader.get_metadata.side_effect=read
        result=self.service.execute('personal')
        self.assertEqual((result.unavailable,result.errors,len(result.messages)),(1,1,3))
        self.assertEqual(self.messages.get('personal','0').label_ids,('TRASH',))
        self.assertEqual(self.messages.get('personal','1').label_ids,('TRASH',))
        self.assertEqual(self.messages.get('personal','2').label_ids,())

    def test_stale_commit_and_cross_account_metadata_cannot_advance_or_write(self):
        cursor,rows=self.store.label_page('personal',5)
        self.store.commit_labels('personal',cursor,rows,[])
        with self.assertRaises(RuntimeError):self.store.commit_labels('personal',cursor,rows,[])
        self.reader.get_metadata.side_effect=lambda account,id:EmailMessage(id,'university','s','Changed',1,())
        result=self.service.execute('personal')
        self.assertEqual(result.errors,2);self.assertEqual(result.messages,[])
        self.assertEqual(self.messages.get('personal','5').label_ids,('TRASH',))
        with self.assertRaises(ValueError):self.service.execute('personal',6)

    def test_incremental_listing_includes_archived_spam_and_trash(self):
        from unittest.mock import MagicMock
        from email_agent.infrastructure.gmail.client import GmailClient
        client=GmailClient.__new__(GmailClient);client._service=MagicMock();client._gate=MagicMock()
        client._service.users.return_value.messages.return_value.list.return_value.execute.return_value={}
        self.assertEqual(client.list_messages(100),[])
        parameters=client._service.users.return_value.messages.return_value.list.call_args.kwargs
        self.assertTrue(parameters['includeSpamTrash']);self.assertNotIn('labelIds',parameters)

    def test_metadata_refresh_is_bounded_and_keeps_thread_context(self):
        from email_agent.infrastructure.gmail.reader import GmailEmailReader
        client=Mock();client.get_message.return_value=dict(id='x',sender='s',subject='Re: a conversation',label_ids=(),list_headers=(('thread-id','thread'),))
        msg=GmailEmailReader(client).get_metadata('personal','x')
        client.get_message.assert_called_once_with('x',max_attempts=2)
        self.assertEqual(dict(msg.list_headers)['thread-id'],'thread')
        self.assertEqual(msg.label_ids,())
