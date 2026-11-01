"""Read/write concurrency and independent last-verified status, no external calls."""
import copy
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from fastapi import FastAPI
from email_agent.api.control_center import register_control_center
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository

class LiveProgressRepairTests(unittest.TestCase):
 def test_wal_reader_sees_committed_state_while_writer_remains_active(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'messages.db';SQLiteMessageRepository(path).initialize()
   with sqlite3.connect(path) as db:db.execute("insert into messages(account_id,message_id,sender,subject) values('personal','one','fixture','before')")
   writer=sqlite3.connect(path);self.addCleanup(writer.close);writer.execute('begin immediate');writer.execute("update messages set subject='uncommitted' where message_id='one'")
   start=time.monotonic()
   with sqlite3.connect(path,timeout=.1) as reader:
    self.assertEqual(reader.execute('pragma journal_mode').fetchone()[0],'wal');self.assertEqual(reader.execute('select subject from messages').fetchone()[0],'before')
   self.assertLess(time.monotonic()-start,.1);writer.rollback()
 def route(self):
  app=FastAPI();app.state.ai_status_snapshot=Mock(return_value=dict(local='Connected',cloud='Available',preference='auto',free_only=True,organization_enabled=True,local_policy={'enabled':True},work={},providers=[]))
  store=Mock();store.sync_snapshot.return_value={'last_error':None,'last_success':'2026-10-31 00:00:00'}
  data=dict(new_mail=0,awaiting_analysis=3,cleanup_candidates=0,latest_classification=None,progress={'total':10,'organized':7},activity={'active':[]})
  store.counters.side_effect=lambda _:copy.deepcopy(data);store.activity.return_value={'active':[],'state':'Queued','refreshed_at':123}
  with patch('email_agent.api.control_center.ContinuousStore'),patch('email_agent.api.control_center.SQLiteControlCenterStore',return_value=store):register_control_center(app,Mock())
  return app,store,next(r.endpoint for r in app.routes if r.path=='/api/control/{account}')
 def test_provider_read_failure_preserves_independent_progress_and_live_activity(self):
  app,store,route=self.route();app.state.ai_status_snapshot.side_effect=sqlite3.OperationalError('database is locked')
  with patch('email_agent.infrastructure.ai.resource_monitor.resources.snapshot',side_effect=AssertionError('Status must not trigger live hardware probing')):r=route('personal')
  self.assertEqual(r['progress']['organized'],7);self.assertIn('engine',r['stale_sections']);self.assertEqual(r['engine']['local'],'Unknown');self.assertEqual(route('personal',True)['state'],'Queued')
 def test_cached_progress_retains_measurement_time_and_recovers_after_transient_lock(self):
  app,store,route=self.route()
  with patch('email_agent.api.control_center.time.time',return_value=100),patch('email_agent.infrastructure.ai.resource_monitor.resources.snapshot',side_effect=AssertionError('Status must not trigger live hardware probing')):first=route('personal')
  store.counters.side_effect=sqlite3.OperationalError('database is locked')
  with patch('email_agent.api.control_center.time.time',return_value=110):stale=route('personal')
  self.assertTrue(stale['stale']);self.assertEqual(stale['observed_at'],first['observed_at']);self.assertEqual(stale['progress'],first['progress'])
  store.counters.side_effect=lambda _:dict(new_mail=0,awaiting_analysis=2,cleanup_candidates=0,latest_classification=None,progress={'total':10,'organized':8},activity={'active':[]})
  with patch('email_agent.api.control_center.time.time',return_value=120),patch('email_agent.infrastructure.ai.resource_monitor.resources.snapshot',side_effect=AssertionError('Status must not trigger live hardware probing')):fresh=route('personal')
  self.assertFalse(fresh['stale']);self.assertEqual(fresh['progress']['organized'],8);self.assertEqual(fresh['observed_at'],120)
