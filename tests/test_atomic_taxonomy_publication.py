"""Real on-disk WAL/rollback journals, abrupt process exit and idempotent retry."""
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_local_organization_agent as fixture
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
from email_agent.infrastructure.database.taxonomy_transaction import taxonomy_transaction


class AtomicPublicationTests(unittest.TestCase):
    setUp = fixture.LocalAgentTests.setUp
    seed = fixture.LocalAgentTests.seed
    prepare = fixture.LocalAgentTests.prepare
    add = fixture.LocalAgentTests.add

    def ready(self):
        p = self.prepare()
        for path in (self.path, self.store.categories):
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA journal_mode=WAL')
        self.store.decide('personal', p['id'], p['fingerprint'], True)
        return p

    def assert_original(self, p):
        self.assertNotIn(p['payload']['operations'][0]['id'], {c['id'] for c in self.store.catalog('personal')})
        for a in p['payload']['assignments']:
            self.assertEqual(self.store.message_row('personal', a['message_id'])['decision'], {k:v for k,v in a['before'].items() if k not in ('account_id','message_id')})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_taxonomy_audit WHERE proposal_id=? AND action='applied'", (p['id'],)).fetchone()[0], 0)

    def test_wal_commit_audit_sidebar_and_retry_once(self):
        p = self.ready()
        r = self.store.apply('personal', p['id'])
        self.assertEqual(r['affected'], 4)
        self.assertEqual(self.store.apply('personal', p['id'])['affected'], 0)
        target = r['category_ids'][0]
        self.assertIn(target, {c['id'] for c in self.management.categories('personal')})
        self.assertNotIn(target, {c['id'] for c in self.management.categories('university')})
        self.assertEqual(self.management.mailbox('personal', target, 'all')['total'], 4)
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA main.journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA taxonomy.journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_taxonomy_audit WHERE proposal_id=? AND action='applied'", (p['id'],)).fetchone()[0], 1)
        self.store.undo_local_change('personal', p['id'])
        self.assertNotIn(target, {c['id'] for c in self.management.categories('personal')})

    def test_failure_rolls_back_both_files_and_restores_wal(self):
        p = self.ready()
        with patch.object(self.store, '_learn_content_patterns', side_effect=RuntimeError('forced local failure')):
            with self.assertRaises(RuntimeError):
                self.store.apply('personal', p['id'])
        self.assert_original(p)
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA main.journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.store.apply('personal', p['id'])['affected'], 4)

    def test_process_exit_recovers_hot_journals_and_retry(self):
        p = self.ready()
        code = '''
import os,sys
from pathlib import Path
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
store=SQLiteSemanticOrganizerStore(Path(sys.argv[1]),Path(sys.argv[2]))
def stop(db,*args):
    assert db.execute('PRAGMA main.journal_mode').fetchone()[0]=='delete'
    assert db.execute('PRAGMA taxonomy.journal_mode').fetchone()[0]=='delete'
    assert db.execute('PRAGMA main.synchronous').fetchone()[0]==2
    assert db.execute('PRAGMA taxonomy.synchronous').fetchone()[0]==2
    assert Path(sys.argv[1]+'-journal').is_file()
    assert Path(sys.argv[2]+'-journal').is_file()
    os._exit(73)
store._learn_content_patterns=stop
store.apply('personal',sys.argv[3])
'''
        r = subprocess.run([sys.executable, '-c', code, str(self.path), str(self.store.categories), p['id']], env={**os.environ, 'PYTHONPATH':'src:tests'}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 73, r.stderr)
        self.store = SQLiteSemanticOrganizerStore(self.path, self.store.categories)
        self.store.initialize()
        self.assert_original(p)
        self.assertEqual(self.store.apply('personal', p['id'])['affected'], 4)

    def test_interruption_after_commit_before_wal_restore_keeps_complete_publication(self):
        p = self.ready()
        code = '''
import os,sys
from pathlib import Path
from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
from email_agent.infrastructure.database.taxonomy_transaction import taxonomy_transaction
store=SQLiteSemanticOrganizerStore(Path(sys.argv[1]),Path(sys.argv[2]))
import sqlite3
connect=sqlite3.connect
class ExitAfterCommit(sqlite3.Connection):
    def execute(self, sql, *args):
        if sql=='PRAGMA main.journal_mode=wal':os._exit(74)
        return super().execute(sql,*args)
sqlite3.connect=lambda *args,**kw:connect(*args,**kw,factory=ExitAfterCommit)
store.apply('personal',sys.argv[3])
'''
        r = subprocess.run([sys.executable, '-c', code, str(self.path), str(self.store.categories), p['id']], env={**os.environ,'PYTHONPATH':'src:tests'},capture_output=True,text=True)
        self.assertEqual(r.returncode, 74, r.stderr)
        self.store = SQLiteSemanticOrganizerStore(self.path, self.store.categories)
        self.store.initialize()
        self.assertEqual(self.store.apply('personal', p['id'])['affected'], 0)
        self.assertEqual(self.management.mailbox('personal', p['payload']['operations'][0]['id'], 'all')['total'], 4)

    def test_missing_storage_fails_before_approval_and_busy_reader_keeps_original(self):
        p = self.ready()
        original = self.store.categories
        self.store.categories = original.parent/'missing.db'
        with self.assertRaisesRegex(ValueError, 'writable database'):
            self.store.publication_prerequisites()
        self.store.categories = original
        with closing(sqlite3.connect(self.path)) as reader:
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM semantic_taxonomy_proposals').fetchall()
            with self.assertRaisesRegex(ValueError, 'busy'):
                with taxonomy_transaction(self.path, self.store.categories):
                    self.fail('Reader lock must prevent publication')
        self.assert_original(p)


    def test_legacy_wal_failures_retry_once_and_busy_does_not_spend_attempts(self):
        p = self.ready()
        with self.store.connect() as db, db:
            db.execute('DELETE FROM organizer_schema_versions WHERE version=8')
            db.execute('INSERT INTO local_tool_attempts VALUES(?,?,?,3)', ('personal',p['id'],p['fingerprint']))
            db.execute("INSERT INTO local_organization_audit(account_id,kind,payload) VALUES(?,'apply_approved_plan',?)",('personal',__import__('json').dumps({'proposal_id':p['id'],'error':'Atomic taxonomy publication requires rollback journals; no change applied'})))
        self.store.initialize(); self.store.initialize()
        self.store.local_policy('personal', True, True)
        from email_agent.application.organization_contract import PublicationBusy
        with patch.object(self.store,'apply',side_effect=PublicationBusy('busy')):
            self.assertEqual(self.agent.step('personal')['applied'],0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT attempts FROM local_tool_attempts WHERE proposal_id=?',(p['id'],)).fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_taxonomy_audit WHERE proposal_id=? AND action='publication_retry_enabled'",(p['id'],)).fetchone()[0],1)
        self.assertEqual(self.agent.step('personal')['applied'],1)

    def test_one_grounded_useful_discovery_publishes_without_per_action_approval(self):
        self.seed()
        self.messages.record_search_content('personal','personal-0','Habitat sampling report from an independent expedition.',complete=True)
        row=self.store.message_row('personal','personal-0')
        frame=dict(understood=True,analysis_reason=None,primary_intent='Habitat research',message_type='Field report',status=None,entity=None,source='mistral',model='fixture',quality_verified=True,taxonomy_quality_verified=True,confidence=.94,evidence=[{'field':'excerpt','quote':'Habitat sampling report'}],input_coverage={'body_complete':True},attributes={},sensitivities=['personal'])
        self.store.put_frame(row,frame,'fixture')
        self.engine.propose_change('personal',dict(action='create',name='Habitat studies',sources=[],message_ids=['personal-0'],dimension=None,reason='Grounded research document purpose',icon_key='file-text'),source='mistral')
        self.store.local_policy('personal',True,True)
        from email_agent.application.local_organization_agent import LocalOrganizationAgent
        result=LocalOrganizationAgent(self.engine,self.store).step('personal')
        self.assertEqual(result['applied'],1)
        target=result['results'][0]['category_ids'][0]
        visible=next(c for c in self.management.categories('personal') if c['id']==target)
        self.assertEqual(visible['icon_key'],'file-text')
        self.assertEqual(self.management.mailbox('personal',target,'all')['total'],1)
        self.assertNotIn(target,{c['id'] for c in self.management.categories('university')})

    def test_expired_inference_is_recovered_once_without_replaying_success(self):
        import time
        from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore
        from email_agent.application.hybrid_ai import ModelCapability
        hybrid=SQLiteHybridStore(self.path);hybrid.initialize()
        cap=ModelCapability('ollama','fixture',64000,structured=True,free=True,validated=True,quality={'en':.95},fingerprint='fixture')
        hybrid.register(cap,time.time()+86400);hybrid.configure('ollama',True)
        attempt=hybrid.reserve('personal',cap,'interrupted-fixture',['m'],100,100)
        with hybrid.connect() as db:
            db.execute('UPDATE hybrid_attempts SET created_at=?,lease_until=? WHERE id=?',(time.time()-600,time.time()-60,attempt))
        hybrid.initialize()
        with hybrid.connect() as db:
            row=db.execute('SELECT * FROM hybrid_attempts WHERE id=?',(attempt,)).fetchone()
            self.assertEqual(row['status'],'interrupted');self.assertIsNone(row['finished_at'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM hybrid_events WHERE event='inference_interrupted'").fetchone()[0],1)
        self.assertEqual(hybrid.recover_expired_attempts(),0)
        retried=hybrid.reserve('personal',cap,'interrupted-fixture',['m'],100,100)
        hybrid.finish(retried,'done')
        with self.assertRaises(ValueError):hybrid.reserve('personal',cap,'interrupted-fixture',['m'],100,100)
