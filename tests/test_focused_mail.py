import json
import sqlite3
import unittest
from collections import Counter
from email_agent.application.classify_messages import decide
from email_agent.application.dynamic_sections import discover_sections
from email_agent.application.job_evidence import employment_evidence, job_status
from email_agent.application.semantic_organization import parse_section_advice
from email_agent.domain.message import EmailMessage
import test_ux_product as product
import test_intelligent_organization as organization


class EmploymentTests(unittest.TestCase):
    def test_contextual_outcomes_and_acknowledgment_is_not_offer(self):
        cases = [
            ('Thank you for applying - application received','applied'),
            ('Your application for the Developer role was received','applied'),
            ('Your application for the Engineer position: interview invitation','interview'),
            ('Your application for the Engineer position: we are not moving forward','rejected'),
            ('Your application for the Engineer role: your employment offer letter','offer'),
            ('Your application: you have been selected for the position','offer'),
            ('Your application for the role was accepted for review','unknown'),
            ('Update on your application for the Engineer role','unknown'),
            ('Your application for the role: this is not an offer','unknown'),
        ]
        for subject,state in cases:
            with self.subTest(subject=subject):
                self.assertEqual(employment_evidence(subject,'team@example.com').state,state)
                self.assertEqual(decide(EmailMessage('x','personal','team@example.com',subject))[:2],('job_applications','classified'))

    def test_advertising_training_and_generic_companies_are_not_applications(self):
        for subject in ['Job alerts: software roles for you','We are hiring - apply now','Interview tips for your application','Your university application received','A software application update','Employment offer','Interview with a famous engineer','Application received','Hello from Big Company']:
            with self.subTest(subject=subject):
                self.assertIsNone(employment_evidence(subject,'news@company.com'))
                self.assertNotEqual(decide(EmailMessage('x','personal','news@company.com',subject))[0],'job_applications')
        self.assertEqual(decide(EmailMessage('x','personal','x@example.com','Job alerts: jobs for you'))[0],'promotions')

    def test_cached_body_negation_and_quote_boundaries(self):
        subject='Update on your application for the Engineer position'
        self.assertEqual(job_status(subject,body='We invite you to an interview with our team')['state'],'interview')
        self.assertEqual(job_status(subject,body='We are not moving forward with your application')['state'],'rejected')
        self.assertEqual(job_status(subject,body='A further update soon.\n> Your employment offer letter')['state'],'unknown')
        self.assertEqual(job_status(subject,body='x'*1201+' Your employment offer letter')['state'],'offer')
        self.assertEqual(job_status('Your employment offer letter',verified=True)['state'],'offer')
        self.assertEqual(job_status('We have an offer',verified=True)['state'],'unknown')


class DynamicSectionTests(unittest.TestCase):
    def row(self,id,subject,sender='team@example.com',thread=None):
        return dict(message_id=id,subject=subject,sender=sender,thread_id=thread)

    def test_any_custom_category_recurring_concepts_not_sender_or_singleton(self):
        rows=[self.row('a','River ecology project notes'),self.row('b','River ecology field results','other@example.net'),self.row('c','Budget committee agenda'),self.row('d','Budget committee minutes'),self.row('single','A completely unrelated note')]
        result=discover_sections('user_created_research',rows)
        self.assertEqual(result.assignments['a'],result.assignments['b'])
        self.assertEqual(result.assignments['c'],result.assignments['d'])
        self.assertNotEqual(result.assignments['a'],result.assignments['c'])
        self.assertEqual(result.assignments['single'],'other')
        self.assertEqual(len(result.names),3)
        self.assertTrue(all(n>=2 for key,n in Counter(result.assignments.values()).items() if key!='other'))
        growth=discover_sections('user_created_research',rows+[self.row('e','River ecology sampling')])
        for key,name in result.names.items():self.assertEqual(growth.names[key],name)
        self.assertNotIn('other',discover_sections('custom',rows[:2]).names)

    def test_order_bridge_preserves_current_observed_experience(self):
        rows=[self.row('pickup','Your parcel is ready for pickup'),self.row('ship','Your order has shipped'),self.row('negative','Your order has not been delivered')]
        result=discover_sections('orders',rows)
        self.assertEqual(result.assignments,dict(pickup='pickup',ship='shipped',negative='processing'))
        self.assertNotIn('delivered',result.names)
        generic=discover_sections('custom_purchases',rows)
        self.assertEqual(generic.assignments,result.assignments)

    def test_schema_rejects_fake_ids_duplicate_groups_and_singletons(self):
        summary={'messages':[dict(message_id='a'),dict(message_id='b')]}
        good={'sections':[dict(name='Research notes',message_ids=['a','b'])]}
        self.assertTrue(parse_section_advice(json.dumps(good),summary)['requires_review'])
        for groups in [[dict(name='x',message_ids=['a'])],[dict(name='x',message_ids=['a','invented'])],[dict(name='x',message_ids=['a','b']),dict(name='y',message_ids=['a','b'])]]:
            with self.assertRaises(ValueError):parse_section_advice(json.dumps({'sections':groups}),summary)


class FocusedStoreTests(unittest.TestCase):
    setUp=product.ProductStoreTests.setUp
    add=product.ProductStoreTests.add

    def test_manual_category_wins_and_evidenced_jobs_leave_pending(self):
        self.add('1','Thank you for applying - application received')
        self.add('2','Thank you for applying - application received')
        self.assertEqual(self.organization.overview('personal',state='pending')['total'],0)
        group=self.organization.overview('personal',state='organized')['groups'][0]
        self.assertIn('Applications',group['name'])
        self.classifier.manual('personal','1','other','User says unrelated')
        self.classifier.run('personal')
        self.assertEqual(self.classifier.assignments.message_details('personal','1')['category_id'],'other')
        data=self.management.mailbox('personal','job_applications')
        self.assertEqual(data['items'][0]['job_status']['label'],'Applied / Received')

    def test_thread_inheritance_requires_actual_confirmation_and_does_not_inherit_ads(self):
        headers=(('thread-id','real-submitted-thread'),)
        self.add('ack','Thank you for applying - application received',headers=headers)
        self.add('reply','Interview invitation',headers=headers)
        self.add('ad','Apply now: job alerts',headers=headers)
        self.assertEqual(self.classifier.assignments.message_details('personal','reply')['category_id'],'job_applications')
        self.assertEqual(self.classifier.assignments.message_details('personal','ad')['category_id'],'promotions')
        self.add('other-account','Interview invitation',account='university',headers=headers)
        self.assertNotEqual(self.classifier.assignments.message_details('university','other-account')['category_id'],'job_applications')
        data=self.management.mailbox('personal','job_applications')
        self.assertEqual(next(i for i in data['items'] if i['message_id']=='reply')['job_status']['state'],'interview')

    def test_custom_category_full_pagination_and_manual_membership(self):
        self.classifier.registry.propose('custom_science','Science','User-created research category')
        proposal=next(p for p in self.classifier.registry.proposals() if p['category_id']=='custom_science')
        self.classifier.registry.approve(proposal['id'])
        for i in range(53):
            self.add(str(i),'River ecology notes '+str(i))
            self.classifier.manual('personal',str(i),'custom_science','User selected Science')
        self.add('isolated','An unrelated unique note')
        data=self.management.mailbox('personal','custom_science')
        self.assertEqual(len(data['sections']),1)
        section=data['sections'][0]
        self.assertEqual(section['count'],53)
        self.assertEqual(len(data['items']),50)
        page=self.management.mailbox('personal','custom_science',section=section['id'],offset=50)
        self.assertEqual(len(page['items']),3)
        self.assertEqual(page['total'],53)
        self.assertEqual(sum(s['count'] for s in page['sections']),53)
        self.assertEqual(self.management.mailbox('university','custom_science')['total'],0)


class SectionAITests(unittest.TestCase):
    setUp=organization.SemanticTests.setUp

    def seed(self):
        from email_agent.domain.message_category import MessageCategoryAssignment
        from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository
        assignments=SQLiteMessageCategoryRepository(self.path)
        for id in ('1','2'):assignments.put(MessageCategoryAssignment('personal',id,'other','manual','classified','User category choice'))

    def test_one_request_preview_consent_cache_and_no_application(self):
        self.seed()
        plan=self.service.preview_sections('personal','other',['1','2'],1)
        self.model.generate_semantic.assert_not_called()
        self.assertEqual(plan['request_cap'],1)
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],False)
        self.model.generate_semantic.return_value=json.dumps({'sections':[dict(name='Local research',message_ids=['1','2'])]})
        result=self.service.run('personal',plan['id'],True)
        self.assertEqual(result['requests'],1)
        self.assertEqual(result['local_changes'],0)
        self.assertEqual(result['gmail_changes'],0)
        self.assertTrue(result['items'][0]['requires_review'])
        again=self.service.preview_sections('personal','other',['1','2'],1)
        self.assertEqual(self.service.run('personal',again['id'],True)['requests'],0)

    def test_membership_change_and_cross_account_fail_closed(self):
        self.seed()
        with self.assertRaises(ValueError):self.service.preview_sections('university','other',['1','2'],1)
        with self.assertRaises(ValueError):self.service.preview_sections('personal','other',['1','2'],2)
        plan=self.service.preview_sections('personal','other',['1','2'],1)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE message_category_assignments SET category_id='orders' WHERE message_id='2'")
        with self.assertRaises(ValueError):self.service.run('personal',plan['id'],True)
        self.model.generate_semantic.assert_not_called()
