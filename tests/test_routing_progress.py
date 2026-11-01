"""Routing fairness, batching protections and persisted no-change checkpoints."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import test_hybrid_ai as hybrid_fixture
import test_hybrid_api as api_fixture
import test_local_organization_agent as local_fixture
from email_agent.application.hybrid_ai import ModelRouter
from email_agent.infrastructure.database.routing_diagnostics import routing_diagnostics
from email_agent.application.hybrid_organizer import HybridOrganizer

class RoutingTests(unittest.TestCase):
    setUp=hybrid_fixture.HybridTests.setUp
    cap=hybrid_fixture.HybridTests.cap
    add=hybrid_fixture.HybridTests.add
    def test_auto_uses_measured_quality_and_latency_not_local_precedence(self):
        local=self.cap('ollama',.95);cloud=self.cap('groq',.98)
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',1000)[0].provider,'groq')
        self.hybrid.register(replace(local,quality={'en':.98},latency=4))
        # Established production policy prefers a qualified authorized cloud route.
        self.assertEqual(ModelRouter(self.hybrid).select('personal','en',1000)[0].provider,'groq')
        with patch.object(self.hybrid,'healthy',side_effect=lambda provider,model:provider!='groq'):
            self.assertEqual(ModelRouter(self.hybrid).select('personal','en',1000)[0].provider,'ollama')
        self.assertEqual(ModelRouter(self.hybrid).select('university','en',1000)[0].provider,'ollama')
    def test_diagnostics_distinguish_missing_credentials_consent_and_quality(self):
        d=routing_diagnostics(self.hybrid,'personal',lambda _:False)
        groq=next(p for p in d['providers'] if p['provider']=='groq')
        self.assertIn('missing_key',groq['reasons']);self.assertIn('account_consent_or_budget_missing',groq['reasons']);self.assertIn('model_quality_not_evaluated',groq['reasons'])
        self.assertEqual(d['completed_1h'],0)

    def test_unroutable_first_message_does_not_block_available_message(self):
        for id in ('blocked','available'):
            self.add(id,'Uncertain purpose')
            self.messages.record_search_content('personal',id,'A complete body for this message.',complete=True)
        self.store.local_policy('personal',True,True,'Fixture consent')
        model=self.cap('ollama')
        engine=HybridOrganizer(self.hybrid,self.engine,lambda _:None)
        self.hybrid.reconcile_other('personal')
        with patch.object(self.hybrid,'select',return_value=['blocked','available']),patch.object(engine,'refresh_eligibility'),patch.object(engine.inference,'options',side_effect=[[],[model]]),patch.object(engine.inference,'run',side_effect=RuntimeError('Fixture stops before inference')) as run:
            engine.step('personal')
            self.assertEqual(run.call_args.args[2],['available'])

class ProgressAPITests(unittest.TestCase):
    setUp=api_fixture.HybridAPITests.setUp
    api=api_fixture.HybridAPITests.api
    add=api_fixture.HybridAPITests.add
    def test_progress_is_account_isolated_and_read_only(self):
        api=self.api();store=self.app.state.hybrid_store
        before=len(store.overview('personal')['grants'])
        response=api.get('/api/hybrid/personal/routing');self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['completed_1h'],0)
        self.assertEqual(len(store.overview('personal')['grants']),before)
        self.assertEqual(api.get('/api/hybrid/unknown/routing').status_code,400)

class GroupCheckpointTests(unittest.TestCase):
    setUp=local_fixture.LocalAgentTests.setUp
    add=local_fixture.LocalAgentTests.add
    def test_unchanged_groups_skip_facet_rebuild_but_continue_purpose_rotation(self):
        self.add('a','Research notes')
        self.engine.organize_local('personal')
        with patch.object(self.store,'grouping_snapshot',side_effect=AssertionError('Unchanged input rebuilt')),patch.object(self.store,'planning_groups',wraps=self.store.planning_groups) as planning:
            self.engine.organize_local('personal');planning.assert_called_once()
        self.store.local_policy('personal',True,True,'Explicit fixture')
        with patch.object(self.store,'grouping_snapshot',wraps=self.store.grouping_snapshot) as snapshot:
            self.engine.organize_local('personal');snapshot.assert_called_once()
    def test_batched_protection_matches_individual_precedence_and_account(self):
        self.add('a','Message',sender='team@example.org')
        message=self.messages.get('personal','a');management=self.cleanup.management
        management.protect('personal','sender','team@example.org','Sender protection')
        management.protect('personal','message','a','Message protection')
        self.assertEqual(management.protection_snapshot('personal',[message])['a'],management.protection('personal',message))
        self.assertIsNone(management.protection_snapshot('university',[message])['a'])

    def test_preview_reuses_identical_groups_but_manual_change_invalidates(self):
        self.add('a','Uncertain purpose')
        self.organization.overview('personal')
        with patch('email_agent.application.organization.decide',side_effect=AssertionError('Repeated grouping')):
            self.organization.overview('personal')
        self.classifier.manual('personal','a','orders','User correction')
        data=self.organization.overview('personal')
        self.assertEqual(data['summary']['organized'],1)
