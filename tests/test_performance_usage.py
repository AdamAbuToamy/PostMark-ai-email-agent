"""Reported usage, stable evidence, compact requests and bounded memory retention."""
import json
import unittest
from unittest.mock import Mock
from email_agent.application.hybrid_organizer import HybridOrganizer
from email_agent.application.hybrid_ai import InferenceResult
from email_agent.application.semantic_mail import SYSTEM,SINGLE_MESSAGE_SYSTEM
from email_agent.infrastructure.database.usage_report import report
import test_hybrid_ai as fixture


class PerformanceTests(unittest.TestCase):
    setUp=fixture.HybridTests.setUp
    add=fixture.HybridTests.add
    seed=fixture.HybridTests.seed
    cap=fixture.HybridTests.cap

    def test_catalog_observations_do_not_reenable_insufficient_model(self):
        self.add('m','An unfamiliar purpose')
        self.messages.record_search_content('personal','m','Complete message evidence.',complete=True)
        row=self.store.message_row('personal','m');model=self.cap('ollama')
        old=[{'id':'other','name':'Other','observed_types':['notice']}]
        new=[{'id':'other','name':'Other','observed_types':['other_notice','notice']}]
        key=HybridOrganizer.evidence_key(row,old)
        self.hybrid.escalate('personal','m',key,model,'Insufficient grounded evidence')
        self.assertEqual(key,HybridOrganizer.evidence_key(row,new))
        self.assertEqual(self.hybrid.excluded('personal','m',HybridOrganizer.evidence_key(row,new)),[('ollama',model.model)])
        self.assertNotEqual(key,HybridOrganizer.evidence_key({**row,'body':'Changed evidence'},new))
        self.assertNotEqual(key,HybridOrganizer.evidence_key(row,[{'id':'other','name':'Renamed purpose'}]))

    def test_compact_prompt_keeps_complete_body_and_historical_cache_identity(self):
        self.add('m','Message purpose')
        body='Current Arabic العربية Hebrew עברית English. '+('meaningful evidence '*3000)
        self.messages.record_search_content('personal','m',body,complete=True)
        h=HybridOrganizer(self.hybrid,self.engine,Mock());row=self.store.message_row('personal','m')
        summary,data,schema,key,bound=h.inputs('personal',row,self.store.model_catalog('personal'))
        self.assertEqual(data['messages'][0]['excerpt'],summary['excerpt'])
        self.assertIn('meaningful evidence '*500,summary['excerpt'])
        self.assertLess(len(SINGLE_MESSAGE_SYSTEM),len(SYSTEM)*.65)
        self.assertEqual(schema['properties']['changes']['maxItems'],0)
        self.assertEqual(key,h.inputs('personal',row,self.store.model_catalog('personal'))[3])

    def test_usage_unknowns_account_filters_ids_and_duplicate_effects(self):
        cap=self.cap('ollama');id=self.hybrid.reserve('personal',cap,'fixture',['m1','m2'],100,100)
        self.hybrid.finish(id,'done',InferenceResult('{}','ollama',cap.model,{'prompt_tokens':100,'output_tokens':20,'total_tokens':120,'cached_tokens':30,'reasoning_tokens':5,'purpose':'production'},.2))
        self.hybrid.record_organization_result('personal','fixture',cap,True,1,message_id='m1',newly_organized=True,issues=0)
        self.hybrid.record_organization_result('personal','fixture',cap,True,1,message_id='m1',newly_organized=True,issues=0)
        with self.hybrid.connect() as db:
            db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,status,created_at) VALUES('personal','ollama','fixture','unknown','failed',strftime('%s','now'))")
        r=report(self.hybrid)
        self.assertEqual(r['summary']['total_tokens'],120)
        self.assertEqual(r['summary']['verified'],1)
        self.assertEqual(r['summary']['saved_changes'],1)
        self.assertEqual(r['summary']['unknown_usage'],1)
        self.assertEqual(r['summary']['cached_tokens'],30)
        self.assertEqual(r['summary']['reasoning_tokens'],5)
        self.assertEqual(r['summary']['newly_organized'],1)
        self.assertIsNone(r['cost']['usd'])
        self.assertEqual(next(x for x in r['records'] if x['id']==id)['message_ids'],['m1','m2'])
        self.assertEqual(report(self.hybrid,'university')['summary']['requests'],0)
        self.assertEqual(report(self.hybrid,task='production')['summary']['requests'],1)
        with self.assertRaises(ValueError):report(self.hybrid,start=0)

    def test_group_preview_does_not_retain_body_or_semantic_payloads(self):
        from email_agent.application.organization import Organization
        from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
        for id in ('m1','m2'):
            self.add(id,'Same complete message')
            self.messages.record_search_content('personal',id,'Private long content '*500,complete=True)
        groups=Organization(SQLiteOrganizationStore(self.path,self.cleanup.management),self.classifier.registry,lambda _:[])
        groups.overview('personal')
        for group in groups._group_cache['personal'][1][0]:
            for item in group['_items']:
                self.assertIsNone(item.body);self.assertIsNone(item.semantic)

    def test_one_local_planning_step_reuses_its_evidence_snapshot(self):
        from unittest.mock import patch
        for id in ('m1','m2'):
            self.add(id,'A message with complete evidence')
            self.messages.record_search_content('personal',id,'Complete known evidence.',complete=True)
        rows=[self.store.message_row('personal',id) for id in ('m1','m2')]
        from email_agent.application.semantic_organizer import row_fingerprint
        for row in rows:
            row['input_fingerprint']=row_fingerprint(row)
            row['semantic']=dict(attributes={'category_name':'Field studies','category_scope':'Ongoing scientific correspondence'},source='gemini',understood=True,analysis_reason=None,evidence=[{'field':'excerpt','quote':'Complete known evidence.'}],input_coverage={'body_complete':True})
        with patch.object(self.store,'walk',return_value=[]),patch.object(self.store,'grouping_checkpoint',return_value=None),patch.object(self.store,'grouping_snapshot',return_value=rows) as snapshot,patch.object(self.store,'planning_groups',return_value=[('A',rows),('B',rows)]),patch.object(self.engine,'propose_change') as propose:
            self.engine.organize_local('personal',1)
        self.assertEqual(snapshot.call_count,1)
        self.assertEqual(propose.call_count,2)
        self.assertTrue(all(call.kwargs['rows'] is rows for call in propose.call_args_list))

    def test_profile_stream_matches_full_snapshot_without_mail_bodies(self):
        for id in ('m1','m2'):
            self.add(id,'Receipt')
            self.messages.record_search_content('personal',id,'Full body evidence '*1000,complete=True)
        expected=[((r.get('decision') or {}).get('category_id','other'),r['semantic']) for r in self.store.grouping_snapshot('personal') if r.get('semantic')]
        self.assertEqual(list(self.store.profile_frames('personal')),expected)
