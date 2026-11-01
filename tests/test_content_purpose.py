import json
import sqlite3
import unittest
from email.message import EmailMessage as MIMEMessage
from unittest.mock import Mock
from email_agent.application.classify_messages import decide
from email_agent.application.content_review import ContentReview
from email_agent.application.mail_management import assess
from email_agent.application.promotional_cleanup import eligible_ad
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.gmail.content_reader import parse_evidence_message, GmailEvidenceReader
from email_agent.infrastructure.database.content_review_store import SQLiteContentReviewStore
import test_ux_product as product
import test_intelligent_organization as organization


class PurposeTests(unittest.TestCase):
    def test_body_purpose_is_independent_of_sender_and_generic_subject(self):
        cases=[
            ('Your receipt\nTotal paid: 59.90 USD\nTransaction ID: A123','bills'),
            ('Invoice number 42\nTotal: ₪100.00','bills'),
            ('Payment received\nAmount paid: 12.50 EUR','bills'),
            ('Refund processed. Your refund has been issued.','orders'),
            ('Your password has been changed successfully.','security'),
            ('קיבלנו בקשה לשינוי סיסמה. הסיסמה שונתה בהצלחה','security'),
            ('تم تغيير كلمة المرور بنجاح','security'),
            ('Your verification code is 123456','security'),
            ('A new login was detected on your account.','security'),
            ('Your lecture schedule has changed.','university'),
            ('Thank you for applying for the Developer position. Application received.','job_applications'),
        ]
        for sender in ['shop@store.example','office@college.ac.il','postmark-synthetic-2@github.com','person@example.net']:
            for body,expected in cases:
                with self.subTest(sender=sender,body=body):
                    message=EmailMessage('id','personal',sender,'Message for you')
                    self.assertEqual(decide(message,body)[:2],(expected,'classified'))

    def test_metadata_receipts_security_and_negative_marketing(self):
        for subject,expected in [('Your receipt from Store','bills'),('Payment confirmation','bills'),('אישור שינוי סיסמה','security'),('GitHub security alert','security'),('Lecture schedule update','university')]:
            message=EmailMessage('id','personal','office@college.ac.il',subject)
            self.assertEqual(decide(message)[0],expected)
        message=EmailMessage('id','personal','sales@store.example','Special discount sale')
        self.assertEqual(decide(message,'Get a discount on your next purchase. Save $20 today.')[0],'promotions')
        self.assertEqual(decide(message,'Get a receipt with your next purchase. Save $20 today.')[0],'promotions')
        self.assertEqual(decide(message,'Offers today.\n> Your password has been changed')[0],'promotions')


class MIMEPurposeTests(unittest.TestCase):
    def test_plain_quoted_printable_charset_and_encoded_headers(self):
        message=MIMEMessage();message['Subject']='אישור שינוי סיסמה';message['From']='University <office@college.ac.il>'
        message.set_content('הסיסמה שונתה בהצלחה',charset='windows-1255',cte='quoted-printable')
        content=parse_evidence_message(message.as_bytes())
        self.assertEqual(content['subject'],'אישור שינוי סיסמה')
        self.assertIn('שונתה',content['body'])
        self.assertEqual(decide(EmailMessage('id','personal',content['sender'],content['subject']),content['body'])[0],'security')

    def test_html_cleaning_alternatives_entities_and_attachment_exclusion(self):
        message=MIMEMessage();message['Subject']='Your account update';message.set_content('')
        message.add_alternative('<head><title>Password reset</title></head><div hidden>Your password has been changed</div><script>Your password has been changed</script><p>Invoice &amp; receipt</p><p>Total paid: 12.50 USD</p><blockquote>Your password has been changed</blockquote><img src="https://tracker.invalid/pixel">',subtype='html',cte='base64')
        message.add_attachment(b'Your password has been changed',maintype='application',subtype='pdf',filename='invoice.pdf')
        result=parse_evidence_message(message.as_bytes())
        self.assertNotIn('password',__import__('email_agent.application.message_text',fromlist=['current_text']).current_text(result['body']));self.assertIn('Invoice & receipt',result['body'])
        self.assertEqual(decide(EmailMessage('id','personal','x@example.com','Update'),result['body'])[0],'bills')
        client=Mock();client.get_raw_message.return_value=message.as_bytes()
        self.assertEqual(GmailEvidenceReader(client).read('id')['body'],result['body'])
        client.get_raw_message.assert_called_once_with('id');client.get_attachment.assert_not_called()

    def test_plain_preferred_and_malformed_oversized_fail_closed(self):
        message=MIMEMessage();message.set_content('Lecture schedule update for your course')
        message.add_alternative('<p>Your password has been changed</p>',subtype='html')
        self.assertNotIn('password',parse_evidence_message(message.as_bytes())['body'])
        for raw in [b'',b'x'*10_000_001]:
            with self.assertRaises(ValueError):parse_evidence_message(raw)


class ContentStoreTests(unittest.TestCase):
    setUp=product.ProductStoreTests.setUp
    add=product.ProductStoreTests.add

    def review(self,readers):
        store=SQLiteContentReviewStore(self.path,self.messages);store.initialize()
        return ContentReview(self.messages,self.classifier,store,readers)

    def test_cached_body_reclassifies_receipt_and_university_security_without_network(self):
        self.add('receipt','A note from Store')
        self.add('campus','הודעה מהאוניברסיטה',sender='office@college.ac.il')
        self.messages.record_search_content('personal','receipt','Receipt\nTotal paid: $15.00\nTransaction ID: test')
        self.messages.record_search_content('personal','campus','Your password has been changed.')
        readers=Mock(side_effect=AssertionError('No network for cached content'))
        result=self.review(readers).run('personal',200,0)
        self.assertEqual(result['reclassified'],2);self.assertEqual(result['body_reads_this_run'],0)
        self.assertEqual(self.classifier.assignments.message_details('personal','receipt')['category_id'],'bills')
        self.assertEqual(self.classifier.assignments.message_details('personal','campus')['category_id'],'security')
        readers.assert_not_called()

    def test_resume_budget_account_isolation_and_manual_decision_preserved(self):
        for id in ['b','c','manual']:self.add(id,'A generic update')
        self.classifier.manual('personal','manual','other','User deliberately kept this')
        self.add('university-id','A generic update',account='university')
        reader=Mock();reader.read.side_effect=[dict(body='Receipt\nTotal paid: $12.00'),dict(body='Your password has been changed')]
        readers=Mock(return_value=reader)
        service=self.review(readers)
        first=service.run('personal',200,1)
        self.assertEqual(first['body_reads_this_run'],1);self.assertEqual(first['states']['awaiting_body'],1)
        second=self.review(readers).run('personal',200,1)
        self.assertTrue(second['complete']);self.assertEqual(second['reclassified'],2)
        self.assertEqual(reader.read.call_count,2)
        self.assertEqual(self.classifier.assignments.message_details('personal','manual')['source'],'manual')
        self.assertEqual(self.classifier.assignments.message_details('university','university-id')['category_id'],'other')
        self.assertEqual(service.run('personal',200,1)['body_reads_this_run'],0)

    def test_failed_body_remains_protected_and_requires_explicit_retry(self):
        self.add('promo','Special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        reader=Mock();reader.read.side_effect=TimeoutError('Unknown outcome')
        readers=Mock(return_value=reader)
        service=self.review(readers)
        result=service.run('personal',200,1)
        self.assertEqual(result['states']['failed'],1)
        service.run('personal',200,1)
        self.assertEqual(reader.read.call_count,1)
        self.assertEqual(self.management.classifier.assignments.message_details('personal','promo')['category_id'],'promotions')
        self.assertEqual(self.cleanup.protection('personal',self.messages.get('personal','promo')),service.store.HOLD)
        reader.read.side_effect=None;reader.read.return_value=dict(body='Special discount sale on clothing. Shop now.')
        self.assertEqual(service.run('personal',200,1,True)['states']['done'],1)
        self.assertIsNone(self.cleanup.protection('personal',self.messages.get('personal','promo')))

    def test_stale_promotions_cannot_trash_cached_receipts_or_security_or_unread_content(self):
        message=self.add('ad','Special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        decision=self.classifier.assignments.message_details('personal','ad')
        for body in ['', 'Receipt\nTotal paid: $12.00','Your password has been changed','See your receipt in the attached file']:
            self.assertFalse(eligible_ad(message,decision,[],-1,body)[0])
            self.assertEqual(assess(message,decision,[],body=body)['verdict'],'protected' if body else 'needs_review')
            if not body:self.assertEqual(assess(message,decision,[],body=body)['protection_kind'],'missing_content')
        self.assertTrue(eligible_ad(message,decision,[],-1,'Special discount sale on clothing. Shop now.')[0])


class ExcerptConsentTests(unittest.TestCase):
    setUp=organization.SemanticTests.setUp

    def test_excerpt_opt_in_exact_preview_and_cache_change_invalidates_approval(self):
        self.messages.record_search_content('personal','1','Private ambiguous content '*40)
        no_excerpt=self.service.preview('personal',['1'],1)
        self.assertNotIn('excerpt',no_excerpt['items'][0]['summary'])
        preview=self.service.preview('personal',['1'],1,True)
        self.assertEqual(len(preview['items'][0]['summary']['excerpt']),600)
        self.model.generate_semantic.assert_not_called()
        self.messages.record_search_content('personal','1','Changed private content')
        with self.assertRaises(ValueError):self.service.run('personal',preview['id'],True)
        self.model.generate_semantic.assert_not_called()
