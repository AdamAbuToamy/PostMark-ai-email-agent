"""Structural taxonomy, true singleton workflows, recoverable merges, no model calls."""
import unittest
from unittest.mock import Mock,patch
from email_agent.application.taxonomy_fit import fit
from email_agent.application.semantic_mail import SEMANTIC_VERSION
from email_agent.application.semantic_organizer import SemanticOrganizer
from email_agent.application.local_organization_agent import LocalOrganizationAgent
import test_local_organization_agent as fixture


class TaxonomyTests(unittest.TestCase):
    setUp=fixture.LocalAgentTests.setUp
    add=fixture.LocalAgentTests.add

    def frame(self,id,category='other',name='Habitat studies',type='field_report',intent='field_research',scope=True):
        self.add(id,'Research correspondence',sender='scientist@example.org')
        self.messages.record_search_content('personal',id,'Habitat sampling report from this expedition.',complete=True)
        row=self.store.message_row('personal',id)
        f=dict(primary_intent=intent,message_type=type,status=None,entity=None,category_hint='other',suggested_category_id=category,source='gemini',confidence=.95,understood=True,analysis_reason=None,evidence=[dict(field='excerpt',quote='Habitat sampling report from this expedition.')],attributes=dict(category_name=name,category_scope='Ongoing studies and expedition correspondence') if scope else {},input_coverage=dict(body_complete=True),sensitivities=['personal'])
        self.store.put_frame(row,f,SEMANTIC_VERSION)
        return self.store.message_row('personal',id)

    def test_singleton_scope_creates_after_twelve_categories_and_restart_does_not_repeat(self):
        row=self.frame('one')
        with self.store.connect() as db,db:
            for i in range(12):db.execute("INSERT INTO taxonomy.categories VALUES(?,?,'approved','bell')",(f'sem_older_{i}',f'Older {i}'))
        id=self.engine.propose_change('personal',dict(action='create',name='Habitat studies',sources=[],message_ids=['one'],reason='Distinct ongoing research workflow',dimension=None))
        self.store.local_policy('personal',True,True)
        a=LocalOrganizationAgent(self.engine,self.store);self.assertEqual(a.step('personal')['applied'],1)
        target=self.store.message_row('personal','one')['decision']['category_id']
        self.assertIn(target,{c['id'] for c in self.management.categories('personal')})
        self.assertNotIn(target,{c['id'] for c in self.management.categories('university')})
        restarted=LocalOrganizationAgent(SemanticOrganizer(self.store,self.messages,self.classifier,Mock(),Mock(),Mock(),Mock()),self.store)
        self.assertEqual(restarted.step('personal')['applied'],0)
        self.assertEqual(self.store.undo_local_change('personal',id)['affected'],1)

    def test_intent_alone_is_not_a_navigation_scope_and_draft_is_durable_history(self):
        r=self.frame('one',name='Completion notice',type='completion_notice',intent='completion_notice',scope=False)
        self.assertIsNone(fit([r['semantic']],self.store.catalog('personal')))
        id=self.engine.propose_change('personal',dict(action='create',name='Completion notice',sources=[],message_ids=['one'],reason='Notice of completion',dimension=None))
        self.store.local_policy('personal',True,True)
        before=self.store.message_row('personal','one')['semantic']
        self.engine.maintain_taxonomy('personal');self.engine.maintain_taxonomy('personal')
        p=next(p for p in self.store.proposals('personal') if p['id']==id)
        self.assertEqual(p['status'],'deferred');self.assertEqual(LocalOrganizationAgent(self.engine,self.store).step('personal')['applied'],0)
        self.assertEqual(self.store.message_row('personal','one')['semantic'],before)
        with self.store.connect() as db,db:self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_taxonomy_audit WHERE proposal_id=? AND action='deferred'",(id,)).fetchone()[0],1)

    def test_merge_failure_rolls_back_restart_recovers_and_undo_restores_all(self):
        with self.store.connect() as db,db:
            db.execute("INSERT INTO taxonomy.categories VALUES('sem_source','Report delivery','approved','file-text')")
            db.execute("INSERT INTO taxonomy.categories VALUES('sem_target','Field report','approved','file-text')")
            db.execute("INSERT INTO taxonomy.local_category_accounts VALUES('sem_source','personal')")
            db.execute("INSERT INTO taxonomy.local_category_accounts VALUES('sem_target','personal')")
        self.frame('source',type='field_report',scope=False);self.frame('target',type='field_report',scope=False)
        with self.store.connect() as db,db:
            db.execute("UPDATE message_category_assignments SET category_id='sem_source' WHERE message_id='source'")
            db.execute("UPDATE message_category_assignments SET category_id='sem_target' WHERE message_id='target'")
            db.execute("INSERT INTO semantic_policies VALUES('old','personal','sem_source','{}',1,'prior')")
        self.store.local_policy('personal',True,True);self.engine.maintain_taxonomy('personal')
        p=next(p for p in self.store.proposals('personal') if p['payload']['action']=='merge')
        agent=LocalOrganizationAgent(self.engine,self.store)
        with patch.object(self.store,'_learn_content_patterns',side_effect=RuntimeError('forced failure')):
            with self.assertRaises(RuntimeError):agent.step('personal')
        self.assertIn('sem_source',{c['id'] for c in self.store.catalog('personal')})
        self.assertEqual(self.store.message_row('personal','source')['decision']['category_id'],'sem_source')
        self.assertEqual(LocalOrganizationAgent(self.engine,self.store).step('personal')['applied'],1)
        self.assertNotIn('sem_source',{c['id'] for c in self.store.catalog('personal')})
        with self.store.connect() as db,db:self.assertEqual(db.execute("SELECT enabled FROM semantic_policies WHERE id='old'").fetchone()[0],0)
        self.assertEqual(self.store.undo_local_change('personal',p['id'])['affected'],1)
        with self.store.connect() as db,db:
            self.assertEqual(db.execute("SELECT enabled FROM semantic_policies WHERE id='old'").fetchone()[0],1)
            self.assertIsNone(db.execute("SELECT 1 FROM taxonomy.category_aliases WHERE alias='sem_source'").fetchone())
        self.assertEqual(self.store.message_row('personal','source')['decision']['category_id'],'sem_source')

    def test_equivalent_labels_share_plan_but_different_meanings_do_not(self):
        self.frame('one')
        change=dict(action='create',name='Habitat studies',sources=[],message_ids=['one'],reason='Research purpose',dimension=None)
        first=self.engine.propose_change('personal',change)
        self.assertEqual(self.engine.propose_change('personal',{**change,'name':'Expedition research'}),first)
        self.assertEqual(len(self.store.proposals('personal')),1)

    def test_competing_purposes_share_one_review_record_without_publication(self):
        row=self.frame('one')
        change=dict(action='create',name='Habitat studies',sources=[],message_ids=['one'],reason='Research purpose',dimension=None)
        first=self.engine.propose_change('personal',change)
        frame=dict(row['semantic']);frame['attributes']=dict(category_name='Expedition logistics',category_scope='Ongoing expedition logistics')
        self.store.put_frame(row,frame,SEMANTIC_VERSION)
        second=self.engine.propose_change('personal',{**change,'name':'Expedition logistics'})
        self.assertEqual(first,second)
        self.assertEqual(self.store.proposals('personal')[0]['status'],'needs_review')
        self.store.local_policy('personal',True,True)
        self.assertEqual(LocalOrganizationAgent(self.engine,self.store).step('personal')['applied'],0)
