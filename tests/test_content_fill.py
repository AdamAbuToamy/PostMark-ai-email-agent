"""Durable budgets, full-body persistence and readonly fill regressions."""
import unittest,json
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage as MIME
from email_agent.application.fill_content import FillContent
from email_agent.infrastructure.database.content_fill_store import SQLiteContentFillStore
from email_agent.infrastructure.gmail.content_reader import parse_evidence_message
from email_agent.infrastructure.gmail.bounded_http import BudgetConnection
import test_ux_product as product

class FillTests(product.ProductStoreTests):
    def setUp(self):
        super().setUp();self.store=SQLiteContentFillStore(self.path,self.messages);self.store.initialize()
        self.add('x','Your application for the Engineer position');self.add('y','Your receipt')
        self.targets=[('personal','x'),('personal','y')];self.store.create_fill('scope',self.targets,4)
        def factory(account,reserve):
            def read(id):
                reserve('gmail_raw')
                return dict(body='Your application for the Engineer position. We are not moving forward with your application.' if id=='x' else 'Receipt. Total paid: $20',complete=True,headers={})
            return Mock(read=read)
        self.reader=factory;self.service=FillContent(self.store,self.messages,self.classifier,factory)

    def test_decisive_acknowledgment_still_gets_full_body_then_rejection(self):
        first=self.service.run('scope',1)
        self.assertEqual(first['states']['done'],1);self.assertEqual(first['attempts'],1)
        self.assertTrue(self.store.cached_complete('personal','x'))
        page=self.management.mailbox('personal','job_applications',state='all')
        self.assertEqual(page['items'][0]['job_status']['state'],'rejected')
        self.service.run('scope',1);self.assertEqual(self.service.run('scope',1)['attempts'],2)

    def test_campaign_cannot_expand_and_reservations_are_atomic(self):
        with self.assertRaises(ValueError):self.store.create_fill('scope',self.targets,8)
        item=self.store.claim('scope')
        def reserve(_):
            try:self.store.reserve_attempt('scope',item,'gmail_raw');return True
            except ValueError:return False
        with ThreadPoolExecutor(4) as pool:result=list(pool.map(reserve,range(8)))
        self.assertEqual(sum(result),2)
        self.assertEqual(self.store.progress_fill('scope')['attempts'],2)

    def test_manual_category_is_preserved_and_body_is_filled(self):
        self.management.change_category('personal','x','other')
        self.service.run('scope',1)
        self.assertEqual(self.classifier.assignments.message_details('personal','x')['source'],'manual')
        self.assertEqual(self.classifier.assignments.message_details('personal','x')['category_id'],'other')
        self.assertTrue(self.store.cached_complete('personal','x'))

    def test_full_body_survives_later_ui_excerpt_and_restart(self):
        body='Context. '*16000+' Decisive final line'
        self.store.save('personal','x',dict(body=body,complete=True,headers={}))
        self.messages.record_search_content('personal','x',body[:100000])
        self.assertEqual(self.store.cached('personal','x'),body)
        restarted=SQLiteContentFillStore(self.path,self.messages)
        self.assertTrue(restarted.cached_complete('personal','x'))

    def test_failure_or_interrupted_read_is_not_replayed(self):
        def failed(account,reserve):
            def read(id):reserve('gmail_raw');raise TimeoutError()
            return Mock(read=read)
        self.service.readers=failed
        result=self.service.run('scope',1);self.assertEqual(result['states']['failed'],1)
        self.service.readers=self.reader;self.service.run('scope',1)
        self.assertEqual(self.store.progress_fill('scope')['attempts'],2)
        self.assertEqual(self.store.progress_fill('scope')['states']['failed'],1)

    def test_auth_and_raw_share_global_budget(self):
        item=self.store.claim('scope')
        for _ in range(4):self.store.reserve_attempt('scope',item,'oauth')
        with self.assertRaises(ValueError):self.store.reserve_attempt('scope',item,'gmail_raw')
        self.assertEqual(self.store.progress_fill('scope')['attempts'],4)

    def test_large_mime_is_never_silently_cut_at_100k(self):
        mail=MIME();body='Context '*16000+'\nYour application for the Engineer position has been rejected.'
        mail.set_content(body)
        parsed=parse_evidence_message(mail.as_bytes())
        self.assertTrue(parsed['complete']);self.assertGreater(len(parsed['body']),100000)
        self.assertIn('rejected',parsed['body'][-100:])

    def test_boolean_html_attributes_do_not_abort_full_body_read(self):
        mail=MIME();mail.set_content('<div style class>Receipt</div><p>Total paid: 25 USD</p><blockquote>Old quoted rejection</blockquote>',subtype='html')
        result=parse_evidence_message(mail.as_bytes())
        self.assertTrue(result['complete']);self.assertIn('Receipt',result['body'])
        from email_agent.application.message_text import current_text
        self.assertNotIn('rejection',current_text(result['body']))
        self.assertIn('rejection',result['body'])

    def test_wire_retry_reuses_no_reservation_and_stops_before_extra_send(self):
        from email_agent.infrastructure.gmail.bounded_http import BudgetHTTP
        self.assertFalse(BudgetHTTP(Mock()).follow_redirects)
        connection=Mock();budget=Mock();proxy=BudgetConnection(connection,budget)
        proxy.connect();proxy.request('GET','/');self.assertEqual(budget.call_count,1)
        proxy.request('GET','/');self.assertEqual(budget.call_count,2)
        budget.side_effect=ValueError('stop')
        with self.assertRaises(ValueError):proxy.request('GET','/')
        self.assertEqual(connection.request.call_count,2)

for name in product.ProductStoreTests.__dict__:
    if name.startswith('test_') and name not in FillTests.__dict__:setattr(FillTests,name,None)
