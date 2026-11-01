"""Regression tests driven by the stored semantic review. No external clients."""
import json
import unittest
from email.message import EmailMessage as MIMEMessage
from email_agent.application.message_text import evidence_excerpt,current_text
from email_agent.application.job_evidence import employment_evidence
from email_agent.application.classify_messages import decide
from email_agent.application.semantic_mail import local_frame,public_summary,validate_model,validate_batch,canonical_frames,status_key
from email_agent.application.dynamic_sections import discover_sections
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.gmail.content_reader import parse_evidence_message
import test_semantic_engine as engine

class TextRegressionTests(unittest.TestCase):
    def test_multilingual_outcomes_are_current_context_not_company(self):
        cases=[('Your application for the Engineer position','We are not able to offer you the position','rejected'),('Your application for the Engineer position','We have decided to move forward with other candidates','rejected'),('Your application for the Engineer position','We are not moving forward with your application','rejected'),('עדכון למועמדותך למשרה','לא נוכל להתקדם עם מועמדותך','rejected'),('تحديث طلبك للوظيفة','نأسف لعدم متابعة طلب توظيفك','rejected'),('עדכון למועמדותך למשרה','התקבלת לתפקיד המפתח','offer'),('تحديث طلبك للوظيفة','تم قبولك في الوظيفة','offer'),('Your application for the Engineer role','We are pleased to offer you the position','offer'),('תודה על מועמדותך למשרה','קיבלנו את מועמדותך למשרה','applied'),('تحديث طلبك للوظيفة','تم استلام طلبك للوظيفة','applied'),('עדכון למועמדותך למשרה','נרצה להזמין אותך לראיון','interview'),('تحديث طلبك للوظيفة','يسرنا دعوتك للمقابلة','interview')]
        for subject,body,state in cases:
            for sender in ['someone@random.example','hr@another.example']:
                with self.subTest(state=state,sender=sender):self.assertEqual(employment_evidence(subject,sender,body).state,state)

    def test_negation_future_and_other_candidates_do_not_invent_outcome(self):
        for body in ['Your application has not been rejected','No decision has been made','Your application may be rejected later','Other candidates will be interviewed too','טרם התקבלה החלטה','لم يتم رفض طلبك للوظيفة']:
            evidence=employment_evidence('Your application for the Engineer position','x@example.net',body)
            self.assertEqual(evidence.state,'unknown',body)

    def test_long_body_relevant_window_retains_outcome_and_excludes_history(self):
        body=('General processing information. '*1800)+'\nWe are not moving forward with your application for the position.\nOn Monday someone wrote:\nYour employment offer letter'
        excerpt=evidence_excerpt(body,600)
        self.assertLessEqual(len(excerpt),600);self.assertIn('not moving forward',excerpt);self.assertNotIn('offer letter',excerpt)
        self.assertEqual(employment_evidence('Your application for the Developer role','x@example.net',body).state,'rejected')

    def test_old_quoted_rejection_and_signature_are_not_current_outcome(self):
        for boundary in ['On Monday someone wrote:','בתאריך יום שני כתב:','في يوم الاثنين كتب:','-----Original Message-----','-- ']:
            body='We will send an update soon.\n'+boundary+'\nWe are not moving forward with your application.'
            self.assertEqual(employment_evidence('Your application for the Engineer role','x@example.net',body).state,'unknown',boundary)

    def test_mime_unicode_plain_fallback_html_and_quoted_class(self):
        mail=MIMEMessage();mail['Subject']='עדכון';mail.set_content('OK')
        mail.add_alternative('<p>تم استلام طلبك للوظيفة</p><p>سنراجع طلبك للوظيفة قريبًا.</p><div class="gmail_quote">تم رفض طلبك للوظيفة</div>',subtype='html',cte='base64')
        result=parse_evidence_message(mail.as_bytes());self.assertIn('استلام',result['body']);self.assertNotIn('رفض',current_text(result['body']))
        self.assertEqual(employment_evidence('تحديث طلبك للوظيفة','x@example.net',result['body']).state,'applied')

    def test_json_mime_reader_ignores_attached_thread_and_uses_rich_alternative(self):
        import base64
        from unittest.mock import Mock
        from email_agent.infrastructure.gmail.content_reader import GmailContentReader
        encode=lambda text:base64.urlsafe_b64encode(text.encode()).decode()
        payload={'mimeType':'multipart/mixed','parts':[
            {'mimeType':'text/plain','body':{'data':encode('OK')}},
            {'mimeType':'text/html','body':{'data':encode('<p>Receipt for your completed payment</p><p>Total paid: 20 USD</p>')}},
            {'mimeType':'message/rfc822','filename':'old.eml','parts':[{'mimeType':'text/plain','body':{'data':encode('Your password has been changed')}}]}]}
        client=Mock();client.get_full_message.return_value={'id':'x','payload':payload}
        result=GmailContentReader(client).get_content('x')
        self.assertIn('Total paid',result['_classification_body']);self.assertNotIn('password',result['_classification_body'])
        client.get_attachment.assert_not_called()

    def test_receipts_security_and_marketing_multilingual_no_sender_exception(self):
        cases=[('Update','Receipt\nTotal paid: $25.00','bills'),('עדכון','חשבונית\nסכום ששולם: 25 ILS','bills'),('تحديث','رقم الفاتورة 12\nالمبلغ المدفوع: 25 USD','bills'),('Update','Your password has been changed','security'),('עדכון','איפוס הסיסמה בוצע','security'),('تحديث','تم تغيير كلمة المرور','security'),('Sale','Special discount sale, shop now','promotions')]
        for subject,body,category in cases:
            for sender in ['office@college.ac.il','store@shop.example','x@random.example']:
                self.assertEqual(decide(EmailMessage('x','personal',sender,subject),body)[0],category,(subject,body,sender))

    def test_financial_product_advertisement_is_not_a_completed_payment(self):
        for sender in ['x@shop.example','office@college.ac.il','billing@example.net']:
            message=EmailMessage('x','personal',sender,'Save40% on invoice software')
            self.assertEqual(decide(message,'Invoice automation software. Start a free trial for $20 per month.')[0],'promotions')
            receipt=EmailMessage('r','personal',sender,'Your receipt for invoice software')
            self.assertEqual(decide(receipt,'Total paid: $20')[0],'bills')

    def test_formatting_and_observed_receipt_subtype_coalesce(self):
        frames=[dict(message_type='Receipt',status=None,primary_intent='Billing',attributes={}) for _ in range(3)]+[dict(message_type='order_receipt',status=None,primary_intent='receipt',attributes={})]
        result=canonical_frames(frames)
        self.assertTrue(all(f['message_type']=='Receipt' for f in result))
        self.assertEqual(status_key('received'),status_key('Applied / Received'))
        self.assertNotEqual(status_key('accepted_for_review'),status_key('offer'))

    def test_sparse_semantic_sample_cannot_hide_known_receipt_sections(self):
        rows=[dict(message_id=str(i),subject='Your receipt',sender='x@example.net',body='') for i in range(12)]
        for row in rows[:2]:row['semantic']=dict(message_type='Odd document',status='completed',primary_intent='alert',attributes={})
        sections=discover_sections('bills',rows)
        self.assertEqual(sections.assignments['5'],'receipts')

class PersistenceRegressionTests(engine.SemanticEngineTests):
    def test_one_bad_quote_does_not_discard_three_valid_frames(self):
        self.seed();generate=self.model.generate_organization.side_effect
        def partial(system,data,schema):
            result=json.loads(generate(system,data,schema));result['frames'][0]['evidence'][0]['quote']='Not in the supplied input';return json.dumps(result)
        self.model.generate_organization.side_effect=partial
        scope=self.engine.preview(['personal'],1,4);result=self.engine.run(scope['id'],True)
        self.assertEqual(result['requests_used'],1);self.assertEqual(result['batches'][0]['status'],'partial')
        self.assertEqual(sum(r['semantic']['source']=='gemini' for r in self.store.snapshot('personal')),3)
        record=json.loads(self.store.batches(scope['id'])[0]['result'])
        self.assertEqual(record['validation_issues'][0]['stage'],'grounding');self.assertTrue(record['rejected_output'])
        self.engine.run(scope['id'],True);self.assertEqual(self.model.generate_organization.call_count,1)

    def test_invalid_proposal_does_not_discard_grounded_frames(self):
        self.seed();generate=self.model.generate_organization.side_effect
        def proposal(system,data,schema):
            result=json.loads(generate(system,data,schema));result['changes']=[dict(action='create',name='Research',sources=['invented_category'],message_ids=['m0','m1'],dimension=None,reason='Example')];return json.dumps(result)
        self.model.generate_organization.side_effect=proposal
        scope=self.engine.preview(['personal'],1,4);self.engine.run(scope['id'],True)
        self.assertEqual(sum(r['semantic']['source']=='gemini' for r in self.store.snapshot('personal')),4)
        self.assertEqual(self.store.proposals('personal'),[])

    def test_cached_body_reaches_preview_as_relevant_excerpt_not_claimed_full(self):
        self.seed();self.messages.record_search_content('personal','personal-0','General context. '*900+'\nWe are not moving forward with your application for the position.')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0')
        self.assertIn('not moving forward',summary['excerpt']);self.assertLessEqual(len(summary['excerpt']),600)
        raw=self.model.generate_organization('',dict(messages=[summary]),{});frame=validate_model(raw,[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertTrue(frame['input_coverage']['body_supplied']);self.assertFalse(frame['input_coverage']['body_complete'])

    def test_accepted_receipt_and_shipped_status_appear_in_existing_semantic_facet(self):
        for i in range(4):self.add(str(i),'Your receipt')
        rows=self.store.snapshot('personal')
        for i,row in enumerate(rows):
            frame=local_frame(row)
            if i==0:frame.update(message_type='order_receipt',source='gemini',confidence=.99)
            self.store.put_frame(row,frame,'review-test')
        page=self.management.mailbox('personal','bills',state='all')
        self.assertEqual(len({m['section_id'] for m in page['items']}),1)
        self.assertNotEqual(page['items'][0]['section_id'],'other')
        self.assertEqual(page['sections'][0]['name'],'Receipt')

    def test_inferred_pending_does_not_erase_confirmed_application_receipt(self):
        message=self.add('ack','Thank you for applying for the Developer position')
        row=self.store.message_row('personal',message.id);summary=public_summary(row,'m0')
        raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}))
        raw['frames'][0].update(primary_intent='application',message_type='application_acknowledgment',status='pending')
        frame=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['job_applications'])[0]['m0']
        self.assertEqual(frame['status'],'Applied / Received');self.assertEqual(frame['status_source'],'local')
        self.assertIsNone(frame['analysis_reason']);self.assertIn('unverified',frame['status_note'])

    def test_relationship_interpretation_is_disclosed_not_observed_fact(self):
        self.framed();row=self.store.snapshot('personal')[0];frame=dict(row['semantic'],related_ids=['personal-0'])
        self.store.put_frame(row,frame,'test')
        entries=self.store.timeline('personal',row['message_id'])
        linked=[e for e in entries if e['message_id']!=row['message_id']]
        self.assertTrue(linked);self.assertEqual(linked[0]['relationship_basis'],'AI interpretation')

for name in engine.SemanticEngineTests.__dict__:
    if name.startswith('test_') and name not in PersistenceRegressionTests.__dict__:setattr(PersistenceRegressionTests,name,None)
