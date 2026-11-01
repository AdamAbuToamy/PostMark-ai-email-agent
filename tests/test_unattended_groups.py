"""Restart-safe Groups memoization; no model calls and no stale safety decisions."""
import sqlite3
from unittest.mock import patch
from email_agent.application.organization import Organization
from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
from test_intelligent_organization import GroupTests
import unittest


class PersistentGroupsTests(unittest.TestCase):
    setUp=GroupTests.setUp
    add=GroupTests.add

    def restart(self, exclusions=lambda _:[]):
        return Organization(SQLiteOrganizationStore(self.path,self.store.management),self.classifier.registry,exclusions)

    def test_restart_reuses_exact_assessments_and_counts(self):
        expected=self.service.overview('personal')
        with patch('email_agent.application.organization.assess',side_effect=AssertionError('unchanged evidence was reassessed')):
            self.assertEqual(self.restart().overview('personal'),expected)
        self.assertEqual(self.restart().overview('university')['summary']['indexed'],0)

    def test_body_manual_protection_policy_and_semantics_invalidate(self):
        self.service.overview('personal')
        from email_agent.application.organization import assess
        self.messages.record_search_content('personal','0','New content evidence',complete=True)
        with patch('email_agent.application.organization.assess',wraps=assess) as run:
            self.restart().overview('personal');self.assertGreaterEqual(run.call_count,1)
        self.store.management.protect('personal','message','1','Explicit user protection')
        with patch('email_agent.application.organization.assess',wraps=assess) as run:
            self.restart().overview('personal');self.assertGreaterEqual(run.call_count,1)
        with patch('email_agent.application.organization.assess',wraps=assess) as run:
            self.restart(lambda _:['example.org']).overview('personal');self.assertEqual(run.call_count,8)

    def test_version_invalidates_and_cache_failure_never_hides_groups(self):
        expected=self.service.overview('personal')
        service=self.restart();service.CACHE_VERSION+=':new-rules'
        from email_agent.application.organization import assess
        with patch('email_agent.application.organization.assess',wraps=assess) as run:
            self.assertEqual(service.overview('personal'),expected);self.assertEqual(run.call_count,8)
        with patch('email_agent.infrastructure.database.organization_store.sqlite3.connect',side_effect=sqlite3.OperationalError('database is locked')):
            self.assertEqual(self.store.item_cache('personal',service.CACHE_VERSION),{})
            self.store.save_item_cache('personal',service.CACHE_VERSION,{'x':[]})
