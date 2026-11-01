"""Production 500 regression, lane pagination and independent last-verified status. No AI calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from fastapi import FastAPI,HTTPException
from email_agent.domain.message import EmailMessage
from email_agent.infrastructure.database.action_store import SQLiteActionStore
from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
from email_agent.infrastructure.database.bulk_mail_store import SQLiteBulkMailStore
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore
from email_agent.application.hybrid_organizer import HybridOrganizer
import test_live_progress_repair as control_fixture

class ProtectionSnapshotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'mail.db';self.actions=SQLiteActionStore(self.path);self.management=SQLiteCleanupStore(self.path,Path(self.tmp.name)/'events.db').management;self.bulk=SQLiteBulkMailStore(self.path,self.actions)
 def test_empty_bulk_table_and_empty_snapshot_never_reference_undefined_message(self):
  message=EmailMessage('a','personal','sender@example.test','Subject')
  self.assertEqual(self.management.protection_snapshot('personal',[]),{})
  self.assertEqual(self.management.protection_snapshot('personal',[message]),{'a':None})
 def test_bulk_protection_is_per_message_per_account_and_user_precedence_stays(self):
  messages=[EmailMessage(id,'personal','sender@example.test','Subject') for id in ('a','b')]
  op=self.actions.preview('personal','gmail:bulk',{'items':[]})
  with self.bulk.connect() as db:db.execute("INSERT INTO bulk_mail_items VALUES(?,?,?,'uncertain',NULL)",(op['id'],'a','{}'))
  result=self.management.protection_snapshot('personal',messages);self.assertIn('Unresolved bulk',result['a']);self.assertIsNone(result['b'])
  self.assertEqual(self.management.protection_snapshot('university',messages),{'a':None,'b':None})
  self.management.protect('personal','message','a','User kept message')
  self.assertEqual(self.management.protection_snapshot('personal',messages)['a'],'User kept message')

class QueuePagingTests(unittest.TestCase):
 def test_lane_reads_next_pages_when_head_is_for_another_language(self):
  store=Mock();store.select.side_effect=[['english'],[]]
  engine=HybridOrganizer(store,Mock(),Mock())
  rows=list(engine._lane_pages('personal',[f'hebrew-{i}' for i in range(100)],'mistral',85))
  self.assertEqual(rows[-1][0],'english');self.assertEqual(len(rows),101);store.select.assert_called_with('personal',100,provider='mistral')
 def test_provider_cursor_is_independent_persistent_and_wraps(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'db';s=SQLiteHybridStore(path);s.initialize()
   with s.connect() as db:
    db.executescript('CREATE TABLE messages(account_id TEXT,message_id TEXT); CREATE TABLE message_category_assignments(account_id TEXT,message_id TEXT,category_id TEXT,source TEXT); CREATE TABLE message_content_state(account_id TEXT,message_id TEXT,status TEXT);')
    for i in range(102):
     id=f'm{i:03}';db.execute('INSERT INTO messages VALUES(?,?)',('personal',id));db.execute("INSERT INTO message_category_assignments VALUES(?,?,'other','automatic')",('personal',id));db.execute("INSERT INTO message_content_state VALUES(?,?,'complete')",('personal',id));db.execute("INSERT INTO hybrid_tasks(account_id,message_id,content_key,status,reason) VALUES(?,?,'key','ready_local','Queued for evaluated local AI')",('personal',id))
   first=s.select('personal',100,provider='mistral');self.assertEqual(len(first),100)
   self.assertEqual(s.select('personal',100,provider='ollama'),first)
   restarted=SQLiteHybridStore(path);self.assertEqual(restarted.select('personal',100,provider='mistral'),['m100','m101'])
   self.assertEqual(restarted.select('personal',100,provider='mistral'),[]);self.assertEqual(restarted.select('personal',100,provider='mistral'),first)

class StatusIsolationTests(unittest.TestCase):
 route=control_fixture.LiveProgressRepairTests.route
 def test_unexpected_provider_exception_does_not_remove_progress_or_activity(self):
  app,store,route=self.route()
  with patch('email_agent.api.control_center.time.time',return_value=100):first=route('personal')
  app.state.ai_status_snapshot.side_effect=RuntimeError('provider status unavailable')
  with patch('email_agent.api.control_center.time.time',return_value=110):second=route('personal')
  self.assertEqual(second['progress'],first['progress']);self.assertIn('engine',second['stale_sections']);self.assertEqual(second['engine']['local'],'Connected');self.assertEqual(second['observed_at'],110)
 def test_group_read_failure_keeps_timestamped_snapshot_and_other_accounts_are_isolated(self):
  from email_agent.api.organization import register_organization_routes
  app=FastAPI();service=Mock();service.overview.return_value={'summary':{'organized':3},'groups':[],'total':0,'next_offset':None}
  with patch('email_agent.api.organization.Organization',return_value=service),patch('email_agent.api.organization.SQLiteOrganizationStore'):
   register_organization_routes(app,Mock(),Mock(),Mock())
  route=next(r.endpoint for r in app.routes if r.path=='/api/organization/{account}')
  with patch('email_agent.infrastructure.database.continuous_store.ContinuousStore') as continuous,patch('email_agent.api.organization.logging.getLogger'),patch('email_agent.api.organization.time.time',return_value=100):
   continuous.return_value.progress.return_value={'organized':7};first=route('personal',0,3,'all')
  service.overview.side_effect=RuntimeError('read failed')
  with patch('email_agent.api.organization.logging.getLogger'),patch('email_agent.api.organization.time.time',return_value=110):
   stale=route('personal',0,3,'all');self.assertTrue(stale['stale']);self.assertEqual(stale['observed_at'],first['observed_at']);self.assertEqual(stale['summary'],first['summary'])
   with self.assertRaises(HTTPException) as err:route('university',0,3,'all')
   self.assertEqual(err.exception.status_code,503)
