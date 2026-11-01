import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from email_agent.application.classify_messages import decide
from email_agent.application.smart_sections import section_for,attention_state
from email_agent.application.message_management import MessageManagement
from email_agent.application.organization import Organization
from email_agent.application.manual_subscription import ManualSubscriptions
from email_agent.application.subscriptions import observed_subscriptions
from email_agent.application.subscription_tracking import tracking
from email_agent.domain.message import EmailMessage
from email_agent.intelligence import build_classifier
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
from email_agent.infrastructure.database.action_store import SQLiteActionStore


class ProductPolicyTests(unittest.TestCase):
    def test_order_sections_multilingual_positive_negative_and_cached_body(self):
        for subject,expected in [('Your package is ready for pickup','pickup'),('Your parcel arrived at the pickup point','pickup'),('Your parcel arrived at the sorting hub','other'),('החבילה שלך ממתינה לאיסוף','pickup'),('Your order has shipped','shipped'),('Your parcel was delivered','delivered'),('Your order has not been delivered','processing'),('Estimated delivery tomorrow','processing'),('Order cancelled','other'),('Refund issued','refunds'),('Payment receipt','receipts'),('Order confirmation','processing'),('Questions about the order','other')]:
            with self.subTest(subject=subject):self.assertEqual(section_for('orders',subject),expected)
        self.assertEqual(section_for('orders','Order details',body='Your parcel was delivered'),'delivered')
        self.assertEqual(section_for('orders','Order pending',body='Your parcel was delivered'),'processing')

    def test_other_category_sections(self):
        cases=[('bills','Payment overdue','overdue'),('bills','Payment received','paid'),('bills','Invoice receipt','receipts'),('bills','Payment due tomorrow','due'),('job_applications','Interview invitation','interview'),('job_applications','We are not moving forward','rejected'),('security','Verification code 12','codes'),('security','Your password changed','password'),('university','ציונים בקורס','grades'),('university','Registration deadline','deadlines'),('promotions','Hotel offers','travel'),('promotions','Developer API update','tools'),('other','Re: Hello','personal')]
        for category,subject,expected in cases:
            with self.subTest(category=category,subject=subject):self.assertEqual(section_for(category,subject),expected)

    def test_plain_unknown_is_retained_without_claiming_confidence_or_demanding_review(self):
        message=EmailMessage('x','personal','someone@example.com','An ordinary unclear note')
        category,status,reason=decide(message)
        self.assertEqual(status,'needs_review')
        self.assertEqual(attention_state(message,dict(category_id=category,status=status,reason=reason)),'untouched')
        self.assertEqual(attention_state(message,dict(category_id='other',status='needs_review',reason='submitted_application_not_verified')),'review')
        self.assertEqual(attention_state(message,dict(category_id='orders',status='needs_review',reason='possible_transaction')),'protected')

    def test_academic_domain_assignment_and_job_advertisement_boundary(self):
        for subject in ['עדכון מאת מזכירות','General student correspondence']:
            msg=EmailMessage('x','personal','office@college.ac.il',subject)
            self.assertEqual(decide(msg)[:2],('university','classified'))
        msg=EmailMessage('x','personal','hr@college.ac.il','Thank you for applying - interview')
        self.assertEqual(decide(msg)[:2],('job_applications','classified'))
        self.assertEqual(decide(EmailMessage('x','personal','news@college.ac.il','We are hiring - apply now'))[0],'promotions')


class ProductStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'data'/'email_agent.db'
        self.messages=SQLiteMessageRepository(self.path);self.messages.initialize()
        self.classifier=build_classifier(self.messages)
        self.cleanup=SQLiteCleanupStore(self.path,self.root/'data'/'worker_state.db')
        self.organization=Organization(SQLiteOrganizationStore(self.path,self.cleanup.management),self.classifier.registry,lambda _:[])
        self.management=MessageManagement(self.messages,self.classifier.assignments,self.classifier.registry,self.classifier)
        self.actions=SQLiteActionStore(self.path)

    def add(self,id,subject,account='personal',sender='store@example.com',labels=('INBOX',),headers=None):
        msg=EmailMessage(id,account,sender,subject,int(time.time()*1000),labels,headers)
        self.messages.save(msg);self.classifier.classify(msg)
        return msg

    def test_sections_count_full_category_and_paginate_before_display(self):
        for i in range(53):self.add(str(i),'Your order has shipped')
        self.add('pickup','Your package is ready for pickup')
        self.add('delivered','Your parcel was delivered')
        self.add('other-account','Your parcel was delivered','university')
        page=self.management.mailbox('personal','orders',section='shipped')
        self.assertEqual(page['total'],53);self.assertEqual(len(page['items']),50);self.assertEqual(page['next_offset'],50)
        counts={s['id']:s['count'] for s in page['sections']}
        self.assertEqual((counts['shipped'],counts['pickup'],counts['delivered']),(53,1,1))
        self.assertEqual(len(self.management.mailbox('personal','orders',offset=50,section='shipped')['items']),3)
        self.management.set_status('personal','pickup','done')
        self.assertEqual(self.management.mailbox('personal','orders',state='done',section='pickup')['total'],1)
        self.assertEqual(self.management.mailbox('university','orders',section='shipped')['total'],0)
        with self.assertRaises(ValueError):self.management.mailbox('personal','orders',section='not_a_section')

    def test_review_queue_separates_required_retained_and_protected(self):
        self.add('plain','An unclear note')
        self.add('order','Order update')
        self.add('job','Thank you for applying - interview')
        store=self.classifier.assignments
        self.assertEqual([r['message_id'] for r in store.classification_queue('personal',attention='required')],['job'])
        self.assertEqual([r['message_id'] for r in store.classification_queue('personal',attention='untouched')],['plain'])
        self.assertEqual(len(store.classification_queue('personal',attention='all')),3)
        self.assertEqual(store.progress('personal')['review_required'],1)
        row=store.classification_candidate('personal','job')
        store.resolve_classification_review('personal','job',row['fingerprint'],'reviewed','Reviewed genuine uncertainty')
        self.assertEqual(store.progress('personal')['review_required'],0)

    def test_approval_and_dismissal_survive_reclassification_and_disappear_from_pending(self):
        for i in range(2):self.add('ci'+str(i),'Workflow failed',sender='postmark-synthetic-2@github.com')
        group=self.organization.overview('personal',state='pending')['groups'][0]
        self.organization.decide_group('personal',group['key'],group['fingerprint'],'approve')
        self.classifier.run('personal')
        self.assertEqual(self.organization.overview('personal',state='pending')['total'],0)
        applied=self.organization.overview('personal',state='applied')['groups'][0]
        self.assertFalse(applied['can_approve'])
        self.add('ci-new','Workflow failed',sender='postmark-synthetic-2@github.com')
        self.assertEqual(self.classifier.assignments.message_details('personal','ci-new')['category_id'],'github_ci')
        for i in range(2):self.add('pr'+str(i),'Pull request #42',sender='postmark-synthetic-2@github.com')
        group=self.organization.overview('personal',state='pending')['groups'][0]
        self.organization.decide_group('personal',group['key'],group['fingerprint'],'dismiss')
        self.classifier.run('personal')
        self.assertEqual(self.organization.overview('personal',state='pending')['total'],0)
        self.assertEqual(self.organization.overview('personal',state='dismissed')['total'],1)
        self.assertEqual(self.organization.overview('university',state='dismissed')['total'],0)

    def test_group_keep_resolves_current_attention_without_creating_cleanup_permission(self):
        for i in range(2):self.add('job'+str(i),'Thank you for applying - interview')
        group=self.organization.overview('personal',state='pending')['groups'][0]
        self.organization.decide_group('personal',group['key'],group['fingerprint'],'keep')
        self.classifier.run('personal')
        self.assertEqual(self.classifier.assignments.review_counts('personal')['category'],0)
        for item in self.organization.store.snapshot('personal',20)[0]:self.assertIsNotNone(item.protection)

    def test_manual_completion_is_explicit_durable_not_web_confirmation_and_not_reset_by_mail(self):
        self.add('promo','Discount sale',headers=(('list-id','news.example'),))
        group=observed_subscriptions(self.messages,'personal')[0]
        manual=ManualSubscriptions(self.messages,self.management,self.actions,Mock(side_effect=AssertionError('No network')))
        with self.assertRaises(ValueError):manual.complete('personal',group['id'],False,'Done')
        manual.handled('personal',group['id'],True,'Reviewed only')
        group['manual_state']=self.actions.manual_states('personal')[group['id']]
        self.assertEqual(tracking(self.actions,'personal',group,None)['state'],'active')
        manual.complete('personal',group['id'],True,'Finished on sender website')
        manual.complete('personal',group['id'],True,'Repeated save')
        manual.handled('personal',group['id'],True,'Later manual review must not reset completion')
        state=SQLiteActionStore(self.path).manual_states('personal')[group['id']]
        group['manual_state']=state;group['latest_message_ms']=int(time.time()*1000)+100000
        facts=tracking(self.actions,'personal',group,None)
        self.assertEqual(facts['state'],'manual_completed');self.assertTrue(facts['later_message'])
        self.assertIn('not independently verified',facts['evidence'])
        self.assertEqual(len([a for a in self.actions.manual_audit('personal',group['id']) if a['event']=='manual_completed']),1)
        self.classifier.manual('personal','promo','orders','Reclassified later')
        self.assertEqual(observed_subscriptions(self.messages,'personal'),[])
        self.assertEqual(self.actions.manual_snapshots('personal')[group['id']]['identity'],group['identity'])
        with self.assertRaises(LookupError):manual.complete('university',group['id'],True,'Done')

    def test_spam_listing_and_checkpoints_are_independent_from_trash(self):
        self.add('spam','Invoice receipt',labels=('SPAM',))
        self.add('trash','Old notice',labels=('TRASH',))
        self.add('unknown','Unknown',labels=None)
        store=self.cleanup.management
        self.assertEqual([i['message_id'] for i in store.trash('personal','',0,25,'SPAM')['items']],['spam'])
        self.assertEqual([i['message_id'] for i in store.trash('personal','',0,25)['items']],['trash'])
        spam=store.start_label_sync('personal','SPAM');trash=store.start_label_sync('personal')
        store.commit_label_page('personal',spam,[],None,store.label_rechecks('personal',spam),0)
        self.assertTrue(store.label_state('personal','SPAM')['completed'])
        self.assertFalse(store.label_state('personal')['completed'])
        self.assertEqual(store.label_state('personal')['id'],trash['id'])

    def test_privacy_and_manual_completion_api_are_scoped_and_do_not_call_providers(self):
        from test_api import create_app
        from fastapi.testclient import TestClient
        self.add('promo','Discount sale',headers=(('list-id','news.example'),))
        group=observed_subscriptions(self.messages,'personal')[0]
        headers={'X-Email-Agent-Action':'message-management'}
        with TestClient(create_app(self.messages),client=('127.0.0.1',12345)) as client:
            before=client.get('/api/chat/personal/settings').json()
            self.assertEqual(client.post('/api/chat/personal/history-preference',json={'save_history':False}).status_code,403)
            result=client.post('/api/chat/personal/history-preference',headers=headers,json={'save_history':False})
            self.assertEqual(result.status_code,200);self.assertFalse(result.json()['save_history'])
            for key in ('enabled','processing_consent','model'):self.assertEqual(result.json()[key],before[key])
            self.assertEqual(client.post('/api/chat/personal/history-preference',headers=headers,json={'save_history':False,'enabled':True}).status_code,422)
            self.assertEqual(client.post(f"/api/subscriptions/university/{group['id']}/manual-complete",headers=headers,json={'approved':True,'note':'Done'}).status_code,404)
            result=client.post(f"/api/subscriptions/personal/{group['id']}/manual-complete",headers=headers,json={'approved':True,'note':'Completed manually'})
            self.assertEqual(result.status_code,200)
            item=client.get('/api/subscriptions/personal').json()['items'][0]
            self.assertEqual(item['tracking']['state'],'manual_completed')
            self.assertEqual(client.get('/api/mail-management/personal/messages?view=spam').status_code,200)
            self.assertEqual(client.post('/api/mail-management/personal/refresh-spam',headers=headers,json={'approved':False}).status_code,400)

    def test_reviewed_facts_survive_version_only_changes_but_new_evidence_reopens(self):
        from email_agent.domain.message_category import MessageCategoryAssignment
        message=self.add('job','Thank you for applying - interview')
        store=self.classifier.assignments
        current=store.classification_candidate('personal','job')
        store.resolve_classification_review('personal','job',current['fingerprint'],'reviewed','Already checked')
        decision=store.message_details('personal','job')
        store.put(MessageCategoryAssignment('personal','job',decision['category_id'],'automatic',decision['status'],decision['reason'],'future-version'))
        store.update_attention(message)
        self.assertEqual(store.progress('personal')['review_required'],0)
        store.put(MessageCategoryAssignment('personal','job',decision['category_id'],'automatic','needs_review','new contradictory evidence','future-version'))
        store.update_attention(message)
        self.assertEqual(store.progress('personal')['review_required'],1)

    def test_job_feedback_matches_only_a_verified_submitted_application_in_the_same_account_thread(self):
        headers=(('thread-id','verified-thread'),)
        self.add('submitted','Thank you for applying',sender='hr@employer.example',headers=headers)
        self.classifier.manual('personal','submitted','job_applications','Matched actual application','Employer role submitted by user')
        self.add('reply','Interview invitation',sender='hr@employer.example',headers=headers)
        self.assertEqual(self.classifier.assignments.message_details('personal','reply')['category_id'],'job_applications')
        self.add('ad','Job alert - interview training sale',sender='hr@employer.example',headers=headers)
        self.assertNotEqual(self.classifier.assignments.message_details('personal','ad')['category_id'],'job_applications')
        self.add('other-thread','Interview invitation',sender='hr@employer.example',headers=(('thread-id','different'),))
        self.assertEqual(self.classifier.assignments.message_details('personal','other-thread')['status'],'needs_review')
        self.add('other-account','Interview invitation',account='university',sender='hr@employer.example',headers=headers)
        self.assertEqual(self.classifier.assignments.message_details('university','other-account')['status'],'needs_review')

    def test_job_thread_without_user_submitted_reference_never_becomes_verified(self):
        from email_agent.domain.message_category import MessageCategoryAssignment
        headers=(('thread-id','unknown-thread'),)
        self.add('prior','Application received',headers=headers)
        self.classifier.assignments.put(MessageCategoryAssignment('personal','prior','job_applications','manual','classified','Legacy unverified import'))
        self.add('reply','Interview invitation',headers=headers)
        self.assertEqual(self.classifier.assignments.message_details('personal','reply')['status'],'needs_review')
