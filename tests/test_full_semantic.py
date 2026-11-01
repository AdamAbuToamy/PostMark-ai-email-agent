"""Full text is consent-scoped and verified, never an excerpt labeled complete."""
import unittest,json
from email_agent.application.semantic_mail import public_summary,local_frame,validate_model
from email_agent.application.message_text import full_sections,current_text
import test_semantic_engine as engine

class FullSemanticTests(engine.SemanticEngineTests):
    def complete(self,id,body):self.messages.record_search_content('personal',id,body,complete=True)

    def test_full_input_beyond600_and_history_are_all_present_but_separate(self):
        self.seed(1);body='Current context. '*200+'\nWe are not moving forward with your application.\nOn Monday HR wrote:\nYour employment offer letter'
        self.complete('personal-0',body)
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full')
        self.assertGreater(len(summary['excerpt']),600);self.assertIn('not moving forward',summary['excerpt']);self.assertNotIn('offer letter',summary['excerpt']);self.assertIn('offer letter',summary['history_context'])
        self.assertTrue(summary['body_complete'])

    def test_receipt_title_is_not_changed_to_invoice_by_footer_word(self):
        self.add('receipt','Your store order receipt')
        self.complete('receipt','Receipt. Total paid: $20. For more information about tax invoices see your account.')
        row=self.store.message_row('personal','receipt')
        self.assertEqual(local_frame(row)['message_type'],'Receipt')

    def test_recruitment_account_passcode_is_security_not_application_outcome(self):
        from email_agent.application.classify_messages import decide
        from email_agent.domain.message import EmailMessage
        message=EmailMessage('code','personal','Talent <talent@example.test>','Your application')
        body='To create your Career Site account, use the passcode below to verify your identity. Passcode: 123456'
        self.assertEqual(decide(message,body)[0],'security')
        self.assertNotEqual(decide(message,'Our hiring newsletter describes passcode technology.')[0],'security')

    def test_conditional_submission_does_not_establish_applied_status(self):
        from email_agent.application.job_evidence import employment_evidence
        body='If you have completed the application, check its status. If you are still working on the application, complete it when ready.'
        self.assertIsNone(employment_evidence('Your application for the Engineer role','Recruiting',body))
        self.add('conditional','Your application for the Engineer role');self.complete('conditional',body)
        row=self.store.message_row('personal','conditional');summary=public_summary(row,'m0','full')
        raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0]['status']='submitted'
        frame=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertIsNone(frame['status'])

    def test_reviewing_received_resume_is_acknowledgment_not_offer(self):
        from email_agent.application.job_evidence import employment_evidence
        body="Thank you for your interest in our Developer position. We're currently reviewing resumes with our hiring manager and will notify you once we have an update."
        evidence=employment_evidence('Your application','Talent <talent@example.test>',body)
        self.assertEqual(evidence.state,'applied')

    def test_missing_or_oversize_body_is_deferred_never_clipped_or_sent(self):
        self.seed(2);self.complete('personal-0','Long text '*20000)
        scope=self.engine.preview(['personal'],2,2,0,'full')
        self.assertEqual(scope['message_count'],0);self.assertEqual(len(scope['deferred']),2)
        self.model.generate_organization.assert_not_called()

    def test_full_body_coverage_is_persisted_only_for_full_mode(self):
        self.seed(1);self.complete('personal-0','Complete message text with full useful context.')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full')
        raw=self.model.generate_organization('',dict(messages=[summary]),{})
        frame=validate_model(raw,[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertTrue(frame['input_coverage']['body_complete'])
        legacy=public_summary(row,'m0')
        self.assertNotIn('body_complete',legacy)

    def test_full_historical_body_is_reported_as_supplied_without_current_outcome(self):
        self.seed(1);self.complete('personal-0','> Your application was rejected')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full')
        raw=self.model.generate_organization('',dict(messages=[summary]),{})
        frame=validate_model(raw,[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertTrue(frame['input_coverage']['body_supplied'])
        self.assertEqual(frame['input_coverage']['excerpt_characters'],0)
        self.assertGreater(frame['input_coverage']['history_characters'],0)

    def test_grounded_code_with_wrong_model_category_is_flagged_for_review(self):
        self.seed(1);self.complete('personal-0','Use this passcode to verify your identity for account creation.')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full')
        raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0]['category_id']='other'
        frame=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertEqual(frame['analysis_reason'],'conflicting_category_evidence')

    def test_historical_rejection_cannot_be_grounded_as_current(self):
        self.seed(1);self.complete('personal-0','We will update you soon.\nOn Monday HR wrote:\nYour application was rejected')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full')
        raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0]['evidence']=[dict(field='excerpt',quote='Your application was rejected')]
        with self.assertRaises(ValueError):validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other'])

    def test_full_ai_can_assign_existing_category_but_manual_and_local_evidence_win(self):
        self.classifier.registry.propose('security','Security','Account security')
        self.classifier.registry.approve(next(p['id'] for p in self.classifier.registry.proposals() if p['category_id']=='security'))
        self.seed(2)
        for id in ['personal-0','personal-1']:self.complete(id,'Access permissions for your organization were updated.')
        self.management.change_category('personal','personal-1','other')
        generate=self.model.generate_organization.side_effect
        def interpret(system,data,schema):
            value=json.loads(generate(system,data,schema))
            for frame in value['frames']:frame['category_id']='security'
            return json.dumps(value)
        self.model.generate_organization.side_effect=interpret
        scope=self.engine.preview(['personal'],2,2,0,'full');self.engine.run(scope['id'],True)
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['category_id'],'security')
        self.classifier.classify(self.messages.get('personal','personal-0'))
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['category_id'],'security')
        self.complete('personal-0','Different message evidence without a security event.')
        self.classifier.classify(self.messages.get('personal','personal-0'))
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['category_id'],'other')
        self.assertEqual(self.store.message_row('personal','personal-1')['decision']['source'],'manual')
        self.assertFalse(any(c['name']=='Ecological research' for c in self.store.catalog()))

    def test_conversations_approval_is_local_audited_and_publishes_only_after_user_decision(self):
        self.framed();proposal=self.propose(name='Conversations')
        self.assertFalse(any(c['name']=='Conversations' for c in self.store.catalog()))
        result=self.engine.decide('personal',proposal['id'],proposal['fingerprint'],True)
        self.assertEqual(result['status'],'applied');self.assertEqual(result['affected'],4)
        category=next(c for c in self.store.catalog() if c['name']=='Conversations')
        self.assertEqual(category['source'],'approved')
        self.assertEqual(self.management.mailbox('personal',category['id'],state='all')['total'],4)

    def test_multilingual_ai_application_evidence_survives_sync_and_reaches_job_facets(self):
        self.add('french','Votre candidature');self.complete('french','Nous avons reçu votre candidature pour ce poste. Nous avons retenu un autre candidat.')
        row=self.store.message_row('personal','french');summary=public_summary(row,'m0','full')
        raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));f=raw['frames'][0]
        f.update(category_id='job_applications',confidence=.98,status='Rejected',attributes=[dict(key='application_context',value='submitted')],evidence=[dict(field='excerpt',quote='Nous avons reçu votre candidature pour ce poste.'),dict(field='excerpt',quote='Nous avons retenu un autre candidat.')])
        frame=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other','job_applications'])[0]['m0']
        self.store.put_frame(row,frame,'test');self.assertTrue(self.store.assign_semantic(row,frame))
        self.classifier.classify(self.messages.get('personal','french'))
        self.assertEqual(self.store.message_row('personal','french')['decision']['category_id'],'job_applications')
        mailbox=self.management.mailbox('personal','job_applications',state='all');item=next(m for m in mailbox['items'] if m['message_id']=='french')
        self.assertEqual(item['job_status']['state'],'rejected');self.assertEqual(item['job_status']['source'],'gemini')

    def test_exact_id_queue_can_analyze_old_mail_outside_the_recent_preview_window(self):
        from unittest.mock import patch
        self.seed(1);self.complete('personal-0','Complete older message text.')
        with patch.object(self.store,'snapshot',return_value=[]):
            scope=self.engine.preview(['personal'],1,1,0,'full',['personal-0'])
            self.assertEqual(scope['message_count'],1)
            result=self.engine.run(scope['id'],True)
        self.assertEqual(result['status'],'completed')

    def test_model_can_resolve_a_protected_local_language_abstention(self):
        self.add('jp','Special offer');self.complete('jp','ご購入ありがとうございます。領収書です。合計 ¥1,000。')
        self.classifier.classify(self.messages.get('personal','jp'));row=self.store.message_row('personal','jp');self.assertEqual(row['decision']['category_id'],'other')
        summary=public_summary(row,'m0','full');raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));frame=raw['frames'][0]
        frame.update(category_id='bills',message_type='Receipt',status=None,confidence=.98,evidence=[dict(field='excerpt',quote='領収書です。')])
        accepted=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other','bills'])[0]['m0'];self.store.put_frame(row,accepted,'test');self.assertTrue(self.store.assign_semantic(row,accepted))

    def test_understood_other_is_classified_but_generic_unknown_is_not_hidden(self):
        self.seed(1);self.complete('personal-0','You have unread messages from sellers in your account.')
        row=self.store.message_row('personal','personal-0');summary=public_summary(row,'m0','full');raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));frame=raw['frames'][0]
        frame.update(category_id='other',primary_intent='Seller correspondence',status=None,evidence=[dict(field='excerpt',quote='You have unread messages from sellers')])
        accepted=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other'])[0]['m0'];self.store.put_frame(row,accepted,'test');self.assertTrue(self.store.assign_semantic(row,accepted))
        self.classifier.classify(self.messages.get('personal','personal-0'));self.assertEqual(self.store.message_row('personal','personal-0')['decision']['status'],'classified')
        accepted['primary_intent']='Unknown';from email_agent.application.semantic_mail import supported_semantic_purpose
        self.assertFalse(supported_semantic_purpose(accepted))
        frame.update(primary_intent='Unknown',message_type='Message')
        unknown=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other'])[0]['m0']
        self.assertFalse(unknown['understood']);self.assertEqual(unknown['analysis_reason'],'insufficient_semantic_evidence')

    def test_actual_body_refines_delivery_subject_and_financial_status_stays_independent(self):
        from email_agent.application.smart_sections import section_for
        self.assertEqual(section_for('orders','Your package was delivered','', 'Your package was delivered to the collection point and is ready for pickup.'),'pickup')
        self.add('due','Your invoice');self.complete('due','Invoice total: $100. Payment due on Friday. It is not paid.')
        frame=local_frame(self.store.message_row('personal','due'));self.assertEqual(frame['message_type'],'Invoice');self.assertEqual(frame['status'],'Payment due')

    def test_literal_quote_does_not_allow_model_to_invert_an_explicit_no_offer(self):
        self.add('no-offer','Your application for the Engineer position');self.complete('no-offer','This is not an employment offer. No hiring decision has been made.')
        row=self.store.message_row('personal','no-offer');summary=public_summary(row,'m0','full');raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0].update(category_id='job_applications',status='Offer',evidence=[dict(field='excerpt',quote='employment offer')])
        with self.assertRaises(ValueError):validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other','job_applications'])

    def test_forwarded_document_can_ground_purpose_without_borrowing_its_outcome(self):
        self.add('forward','FYI');self.complete('forward','FYI\nFrom: archive@example.test\nYour receipt. Total paid: $20.00.')
        row=self.store.message_row('personal','forward');summary=public_summary(row,'m0','full');raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0].update(category_id='bills',message_type='Receipt',status='Paid',evidence=[dict(field='history_context',quote='Your receipt. Total paid: $20.00.')])
        result=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other','bills'])[0]['m0']
        self.assertTrue(result['understood']);self.assertIsNone(result['status']);self.assertTrue(self.store.assign_semantic(row,result))

    def test_neutral_subject_plus_quoted_rejection_is_not_a_new_hiring_outcome(self):
        self.add('quoted-job','FYI');self.classifier.manual('personal','quoted-job','job_applications','Submitted previously','Actual application123');self.complete('quoted-job','FYI\nFrom: archive@example.test\nYour application was rejected.')
        row=self.store.message_row('personal','quoted-job');summary=public_summary(row,'m0','full');raw=json.loads(self.model.generate_organization('',dict(messages=[summary]),{}));raw['frames'][0].update(category_id='job_applications',status='Rejected',evidence=[dict(field='subject',quote='FYI'),dict(field='history_context',quote='Your application was rejected.')])
        frame=validate_model(json.dumps(raw),[summary],{'m0':local_frame(row)},['other','job_applications'])[0]['m0'];self.assertIsNone(frame['status']);self.assertEqual(frame['analysis_reason'],'current_outcome_grounding_missing')

for name in engine.SemanticEngineTests.__dict__:
    if name.startswith('test_') and name not in FullSemanticTests.__dict__:setattr(FullSemanticTests,name,None)
