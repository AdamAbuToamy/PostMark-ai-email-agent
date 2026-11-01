"""Actual SQLite tool execution, publication, learning, consent and reversibility."""
import json
import unittest
from unittest.mock import patch, Mock
from email_agent.application.local_organization_agent import LocalOrganizationAgent, OrganizationCommand
from email_agent.application.semantic_mail import SEMANTIC_VERSION
import test_ux_product as fixture
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
from email_agent.application.semantic_organizer import SemanticOrganizer


class LocalAgentTests(unittest.TestCase):
    def setUp(self):
        fixture.ProductStoreTests.setUp(self)
        self.store=SQLiteSemanticOrganizerStore(self.path,self.root/'data/categories.db');self.store.initialize()
        self.model=Mock();self.model.last_usage={}
        self.settings=dict(configured=True,enabled=True,processing_consent=True,model='configured-model',connection={'blocking_reasons':[]},usage={'provider_policy':dict(free_tier_attested=True,paid_approved=False,model='configured-model')})
        self.engine=SemanticOrganizer(self.store,self.messages,self.classifier,lambda _:self.settings,Mock(return_value=True),lambda:self.model,Mock())
    add=fixture.ProductStoreTests.add
    def seed(self):
        for i in range(4):self.add(f'personal-{i}',f'River research observation {i}',sender=f'researcher{i}@example.org')

    def prepare(self, with_plan=False):
        self.seed()
        for i in range(4):self.messages.record_search_content('personal',f'personal-{i}',f'Independent river field survey {i}. Habitat sampling report from this expedition.',complete=True)
        def response(system,data,schema):
            return json.dumps(dict(frames=[dict(id=m['id'],category_id=None,primary_intent='Field research',message_type='Field report',status='Sampling',entity=None,sensitivities=['personal'],confidence=.93,evidence=[dict(field='excerpt',quote=f'Independent river field survey {i}.')],attributes=[dict(key='habitat',value='River'),dict(key='category_name',value='Field research'),dict(key='category_scope',value='Ongoing field studies and expedition correspondence')],related_ids=[]) for i,m in enumerate(data['messages'])],changes=[]))
        # Summaries are date ordered; quote a common exact passage instead.
        def grounded(system,data,schema):
            value=json.loads(response(system,data,schema))
            for f in value['frames']:f['evidence']=[dict(field='excerpt',quote='Habitat sampling report from this expedition.')]
            if with_plan:value['changes']=[dict(action='create',name='Habitat studies',sources=[],message_ids=[m['id'] for m in data['messages']],dimension=None,reason='Independent field reports share a research purpose')]
            return json.dumps(value)
        self.model.generate_organization.side_effect=grounded
        scope=self.engine.preview(['personal'],2,4,content_mode='full');self.engine.run(scope['id'],True)
        self.engine.organize_local('personal')
        self.agent=LocalOrganizationAgent(self.engine,self.store)
        return next(p for p in self.store.proposals('personal') if p['status']=='suggested')

    def test_unseen_full_body_type_creates_publishes_assigns_and_reuses(self):
        p=self.prepare()
        with self.assertRaises(ValueError):self.agent.execute('personal',OrganizationCommand('create_local_category',{'proposal_id':p['id']}))
        self.store.local_policy('personal',True,True,'test explicit consent')
        r=self.agent.step('personal');self.assertEqual(r['applied'],1)
        target=r['results'][0]['category_ids'][0]
        self.assertIn(target,{c['id'] for c in self.management.categories('personal')})
        self.assertNotIn(target,{c['id'] for c in self.management.categories('university')})
        self.assertEqual(self.management.mailbox('personal',target,'all')['total'],4)
        self.assertEqual(self.agent.execute('personal',OrganizationCommand('publish_category_to_sidebar',{'proposal_id':p['id']}))['status'],'verified')
        self.add('new','Another field observation',sender='new@another.example')
        self.messages.record_search_content('personal','new','Habitat sampling report from this expedition.',complete=True)
        row=self.store.message_row('personal','new');frame=dict(self.store.message_row('personal','personal-0')['semantic'])
        self.store.put_frame(row,frame,SEMANTIC_VERSION);self.classifier.classify(self.messages.get('personal','new'))
        self.assertEqual(self.store.message_row('personal','new')['decision']['category_id'],target)
        self.assertEqual(self.agent.step('personal')['applied'],0)
        with self.assertRaises(ValueError):self.agent.execute('university',OrganizationCommand('read_message_content',{'message_id':'personal-0'}))

    def test_undo_and_manual_correction_are_durable_and_conflict_checked(self):
        p=self.prepare();self.store.local_policy('personal',True,True)
        self.agent.step('personal')
        self.store.local_policy('personal',False)
        target=next(c['id'] for c in self.store.catalog('personal') if c['name']=='Field research')
        self.assertFalse(self.classifier.assignments.automatic_category_allowed('personal',target))
        self.assertTrue(self.classifier.assignments.automatic_category_allowed('personal','bills'))
        r=self.agent.execute('personal',OrganizationCommand('undo_organization_change',{'proposal_id':p['id']}))
        self.assertEqual(r['affected'],4)
        self.assertFalse(any(c['name']=='Field research' for c in self.store.catalog('personal')))
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['category_id'],'other')
        self.assertTrue(self.store.organization_history('personal'))

    def test_manual_change_blocks_reversal_without_partial_writes(self):
        p=self.prepare();self.store.local_policy('personal',True,True);self.agent.step('personal')
        self.management.change_category('personal','personal-0','bills','')
        before=self.store.message_row('personal','personal-1')['decision']
        with self.assertRaises(ValueError):self.agent.execute('personal',OrganizationCommand('undo_organization_change',{'proposal_id':p['id']}))
        self.assertEqual(self.store.message_row('personal','personal-1')['decision'],before)
        self.assertEqual(self.store.message_row('personal','personal-0')['decision']['source'],'manual')

    def test_failure_not_success_and_no_unsupported_or_external_tools(self):
        p=self.prepare();self.store.local_policy('personal',True,True)
        with patch.object(self.store,'apply',side_effect=ValueError('write failed')):
            with self.assertRaises(ValueError):self.agent.execute('personal',OrganizationCommand('create_local_category',{'proposal_id':p['id']}))
        self.assertEqual(self.store.organization_history('personal')[0]['result']['status'],'failed')
        self.assertFalse(any(c['name']=='Field research' for c in self.store.catalog('personal')))
        for tool,args in [('trash',{}),('shell',{}),('create_local_category',{'sql':'DROP TABLE messages'})]:
            with self.assertRaises(ValueError):self.agent.execute('personal',OrganizationCommand(tool,args))
        self.assertEqual(self.model.generate_organization.call_count,1)

    def test_revoked_policy_cannot_apply_even_after_plan_was_approved(self):
        p=self.prepare();self.store.local_policy('personal',True,True)
        policy=self.store.local_policy('personal');self.store.decide('personal',p['id'],p['fingerprint'],True)
        self.store.local_policy('personal',False)
        with self.assertRaises(ValueError):self.store.apply('personal',p['id'],policy_revision=policy['revision'])
        self.assertFalse(any(c['name']=='Field research' for c in self.store.catalog('personal')))

    def test_incomplete_content_and_broad_conflicts_keep_grouped_approval(self):
        p=self.prepare();self.store.local_policy('personal',True,True)
        with self.store.connect() as db,db:db.execute("UPDATE message_content_state SET status='unverified' WHERE message_id='personal-0'")
        result=self.agent.execute('personal',OrganizationCommand('create_local_category',{'proposal_id':p['id']}))
        self.assertEqual(result['status'],'needs_approval')
        self.assertFalse(any(c['name']=='Field research' for c in self.store.catalog('personal')))

    def test_restart_and_manual_feedback_stop_wrong_pattern_and_learn_consensus(self):
        p=self.prepare();self.store.local_policy('personal',True,True);self.agent.step('personal')
        self.management.change_category('personal','personal-0','university','')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT enabled FROM semantic_policies WHERE proposal_id=?',(p['id'],)).fetchone()[0],0)
        self.management.change_category('personal','personal-1','university','')
        self.store.local_policy('personal',False);self.store.local_policy('personal',True,True)
        restarted=SQLiteSemanticOrganizerStore(self.path,self.root/'data/categories.db');restarted.initialize()
        self.assertTrue(restarted.local_policy('personal')['enabled'])
        self.add('later','Later field study',sender='later@example.org')
        self.messages.record_search_content('personal','later','Habitat sampling report from this expedition.',complete=True)
        row=restarted.message_row('personal','later');frame=dict(restarted.message_row('personal','personal-2')['semantic'])
        restarted.put_frame(row,frame,SEMANTIC_VERSION);self.classifier.classify(self.messages.get('personal','later'))
        self.assertEqual(restarted.message_row('personal','later')['decision']['category_id'],'university')
        self.assertEqual(restarted.message_row('personal','personal-0')['decision']['source'],'manual')

    def test_generic_multilingual_facet_equivalence_preserves_status_and_conflicts(self):
        from email_agent.application.semantic_mail import canonical_frames
        def frame(name,canonical):return dict(source='gemini',understood=True,confidence=.92,input_coverage={'body_complete':True},message_type=name,status='Payment due',attributes={'canonical_message_type':canonical,'canonical_status':'Paid'})
        frames=[frame('invoice','Invoice'),frame('فاتورة','Invoice'),frame('חשבונית','Invoice'),frame('קבלה','Receipt')]
        merged=canonical_frames(frames)
        self.assertEqual([f['message_type'] for f in merged],['Invoice','Invoice','Invoice','Receipt'])
        self.assertEqual({f['status'] for f in merged},{'Payment due'})
        unrelated=[frame('تقرير ميداني','Field report'),frame('דוח שדה','Field report')]
        self.assertEqual({f['message_type'] for f in canonical_frames(unrelated)},{'Field report'})
        self.assertEqual(canonical_frames([frame('Record','Invoice'),frame('Record','Receipt')])[0]['message_type'],'Record')

    def test_model_structured_plan_executes_real_tools_and_failed_retries_are_bounded(self):
        p=self.prepare(with_plan=True)
        self.assertEqual(p['payload']['source'],'gemini')
        self.store.local_policy('personal',True,True)
        result=self.agent.execute('personal',OrganizationCommand('create_local_category',{'proposal_id':p['id']}))
        self.assertEqual(result['status'],'verified')
        self.assertTrue(any(c['name']=='Habitat studies' for c in self.management.categories('personal')))
        self.assertEqual(self.management.mailbox('personal',result['category_ids'][0],'all')['total'],4)
        for _ in range(3):self.store.reserve_local_action('personal','failed-plan','unchanged')
        with self.assertRaises(ValueError):self.store.reserve_local_action('personal','failed-plan','unchanged')

    def test_unknown_promotions_are_review_not_strong_protection_and_sensitive_stays_safe(self):
        from email_agent.application.mail_management import assess
        message=self.add('promotion','Special discount sale',labels=('INBOX','CATEGORY_PROMOTIONS'))
        decision=self.classifier.assignments.message_details('personal','promotion')
        result=assess(message,decision,[],body='')
        self.assertEqual((result['verdict'],result['protection_kind']),('needs_review','missing_content'))
        sensitive=assess(message,decision,[],body='Your password has been changed')
        self.assertEqual((sensitive['verdict'],sensitive['protection_kind']),('protected','important'))
        ad=assess(message,decision,[],body='Special discount sale on clothing. Shop now.')
        self.assertEqual(ad['verdict'],'eligible')
        policy=assess(message,decision,[],protection='Historical discovery; no historical cleanup approval',body='Special discount sale on clothing. Shop now.')
        self.assertEqual((policy['verdict'],policy['protection_kind']),('eligible','policy'))

    def test_background_reassessment_tracks_body_changes_without_gmail_or_reclassification(self):
        from email_agent.application.mail_management import MailManagement
        from email_agent.application.semantic_mail import local_frame
        cleanup=Mock();cleanup.store=self.cleanup
        service=MailManagement(self.messages,self.classifier,cleanup,Mock(),Mock())
        self.add('changed','Account update')
        self.messages.record_search_content('personal','changed','Receipt. Total paid: $12.00',complete=True)
        self.assertEqual(service.refresh_changed('personal')['processed'],1)
        with self.store.connect() as db:first=db.execute("SELECT reason FROM cleanup_assessments WHERE message_id='changed'").fetchone()[0]
        self.messages.record_search_content('personal','changed','Your password has been changed.',complete=True)
        row=self.store.message_row('personal','changed');self.store.put_frame(row,local_frame(row),SEMANTIC_VERSION)
        self.assertEqual(service.refresh_changed('personal')['processed'],1)
        with self.store.connect() as db:after=db.execute("SELECT reason,verdict FROM cleanup_assessments WHERE message_id='changed'").fetchone()
        self.assertNotEqual(after['reason'],first);self.assertEqual(after['verdict'],'protected')
        self.assertEqual(service.refresh_changed('personal')['processed'],0)
        service.discovery.assert_not_called()

    def test_approved_plan_survives_benign_automatic_status_refresh_without_losing_undo_state(self):
        p=self.prepare()
        self.store.decide('personal',p['id'],p['fingerprint'],True)
        with self.store.connect() as db,db:db.execute("UPDATE message_category_assignments SET status='classified',reason='semantic_full_message_evidence' WHERE account_id='personal'")
        result=self.store.apply('personal',p['id'])
        self.assertEqual(result['affected'],4)
        self.store.undo_local_change('personal',p['id'])
        row=self.store.message_row('personal','personal-0')
        self.assertEqual(row['decision']['category_id'],'other')
        self.assertEqual(row['decision']['reason'],'semantic_full_message_evidence')

    def test_worker_resumes_explicit_approval_without_demanding_new_approval(self):
        p=self.prepare();self.store.decide('personal',p['id'],p['fingerprint'],True)
        self.store.local_policy('personal',True,True)
        self.assertTrue(self.store.user_approved('personal',p['id']))
        with patch.object(self.agent,'_supported',return_value=False):result=self.agent.step('personal')
        self.assertEqual(result['applied'],1)
        self.assertEqual(self.store.proposals('personal')[0]['status'],'applied')

    def test_human_approval_rolls_back_failure_then_publishes_once_with_ids(self):
        p=self.prepare();targets=[op['id'] for op in p['payload']['operations'] if op['kind']=='create']
        with patch.object(self.store,'_learn_content_patterns',side_effect=RuntimeError('fixture transaction failure')):
            with self.assertRaises(RuntimeError):self.engine.decide('personal',p['id'],p['fingerprint'],True)
        self.assertFalse(set(targets)&{c['id'] for c in self.store.catalog('personal')})
        self.assertEqual(next(x for x in self.store.proposals('personal') if x['id']==p['id'])['status'],'approved')
        first=self.engine.decide('personal',p['id'],p['fingerprint'],True)
        second=self.engine.decide('personal',p['id'],p['fingerprint'],True)
        self.assertTrue(first['publication_verified']);self.assertEqual(first['category_ids'],targets)
        self.assertEqual(second['affected'],0);self.assertEqual(second['category_ids'],targets)
        for assignment in p['payload']['assignments']:
            self.assertEqual(self.store.message_row('personal',assignment['message_id'])['decision']['category_id'],assignment['target'])
        self.assertEqual(next(x for x in self.engine.overview('personal')['proposals'] if x['id']==p['id'])['category_ids'],targets)
