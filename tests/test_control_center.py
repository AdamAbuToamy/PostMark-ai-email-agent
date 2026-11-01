"""Durable sync controls and routing choices without any external requests."""
import time
import json
from dataclasses import replace
from email_agent.application.hybrid_ai import ModelCapability,ModelRouter
from email_agent.infrastructure.database.control_center_store import SQLiteControlCenterStore
from email_agent.infrastructure.database.continuous_store import ReadBudgetExceeded
import unittest
import test_hybrid_api as fixture


class ControlCenterTests(unittest.TestCase):
    setUp=fixture.HybridAPITests.setUp
    api=fixture.HybridAPITests.api
    add=fixture.HybridAPITests.add
    def test_latest_classification_includes_new_providers_and_preserves_account_scope(self):
        api=self.api();store=self.app.state.hybrid_store
        for provider in ('mistral','openai'):
            with store.connect() as db:
                db.execute('DELETE FROM semantic_frames')
                db.execute('INSERT INTO semantic_frames(account_id,message_id,input_fingerprint,payload,source,version) VALUES(?,?,?,?,?,?)',('personal','fixture','fixture',json.dumps({'model':'snapshot','understood':True}),provider,'fixture'))
            self.assertEqual(api.get('/api/control/personal').json()['latest_classification']['source'],provider)
            self.assertIsNone(api.get('/api/control/university').json()['latest_classification'])

    def test_sync_controls_are_idempotent_and_do_not_reset_checkpoints_or_ai(self):
        api=self.api();work=self.app.state.hybrid_organizer.organizer
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        store=ContinuousStore(self.messages.database_path,self.messages);store.configure('personal',True)
        with store.connect() as db:
            initial=[tuple(r) for r in db.execute("SELECT * FROM history_progress WHERE account_id='personal'")]
        self.assertEqual(api.post('/api/control/personal/sync',json={'action':'pause'}).status_code,403)
        for n in range(2):
            r=api.post('/api/control/personal/sync',headers=self.headers,json={'action':'pause'});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['changed'],1 if n==0 else 0)
        self.assertTrue(store.enabled('personal'));self.assertFalse(store.sync_enabled('personal'))
        self.assertEqual(api.get('/api/control/personal').json()['sync']['state'],'Paused')
        run=store.begin_run(4)
        with self.assertRaises(ReadBudgetExceeded):store.reserve_read(run,'personal','gmail')
        r=api.post('/api/control/personal/sync',headers=self.headers,json={'action':'resume'});self.assertTrue(r.json()['sync']['enabled'])
        with store.connect() as db:self.assertEqual(initial,[tuple(r) for r in db.execute("SELECT * FROM history_progress WHERE account_id='personal'")])
        self.assertFalse(store.sync_enabled('university'))
        self.assertEqual(api.get('/api/control/unknown').status_code,400)
        self.assertFalse(work.store.local_policy('university')['enabled'])

    def test_retry_is_bounded_and_preserves_attempts_and_completed_content(self):
        api=self.api()
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        work=ContinuousStore(self.messages.database_path,self.messages);service=SQLiteControlCenterStore(work)
        with work.connect() as db:
            for id,attempts,wire in [('eligible',1,2),('exhausted',3,6),('done',1,1)]:
                db.execute('INSERT INTO mail_work(account_id,message_id,stage,fingerprint,status,attempts,wire_attempts) VALUES(?,?,?,?,?,?,?)',('personal',id,'body','fixture','done' if id=='done' else 'failed',attempts,wire))
        self.assertEqual(api.post('/api/control/personal/sync',headers=self.headers,json={'action':'retry'}).json()['changed'],1)
        self.assertEqual(api.post('/api/control/personal/sync',headers=self.headers,json={'action':'retry'}).json()['changed'],0)
        with work.connect() as db:
            rows={r['message_id']:dict(r) for r in db.execute('SELECT * FROM mail_work')}
        self.assertEqual((rows['eligible']['attempts'],rows['eligible']['wire_attempts']),(1,2))
        self.assertEqual(rows['done']['status'],'done');self.assertEqual(rows['exhausted']['status'],'failed')

    def test_live_lease_is_truthful_and_expired_lease_is_not_syncing(self):
        api=self.api()
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        work=ContinuousStore(self.messages.database_path,self.messages);work.configure('personal',True);service=SQLiteControlCenterStore(work)
        work.sync_activity('personal','Reading content');self.assertEqual(service.sync_snapshot('personal')['state'],'Syncing')
        with work.connect() as db:db.execute("UPDATE automation_accounts SET wait_reason='Daily local Gmail read ceiling reached' WHERE account_id='personal'")
        self.assertEqual(service.sync_snapshot('personal')['state'],'Waiting')
        self.assertFalse(service.sync_snapshot('personal')['running'])
        with work.connect() as db:db.execute("UPDATE automation_accounts SET wait_reason=NULL WHERE account_id='personal'")
        self.assertEqual(service.control_sync('personal','pause')['sync']['state'],'Pausing')
        work.sync_activity('personal');self.assertEqual(service.sync_snapshot('personal')['state'],'Paused')
        service.control_sync('personal','resume')
        with work.connect() as db:db.execute("UPDATE sync_runtime SET lease=? WHERE account_id='personal'",(time.time()-1,))
        self.assertNotEqual(service.sync_snapshot('personal')['state'],'Syncing')
        work.sync_activity('personal','Reading content')
        with work.connect() as db:db.executemany("INSERT INTO automation_requests(run_id,account_id,kind) VALUES('fixture','personal','gmail')",[()]*work.DAILY_READ_LIMIT)
        self.assertEqual(service.sync_snapshot('personal')['state'],'Waiting')
        self.assertFalse(service.sync_snapshot('personal')['running'])

    def test_model_selection_persists_and_restricts_real_router_without_consent_expansion(self):
        api=self.api();store=self.app.state.hybrid_store
        cap=ModelCapability('ollama','first',64000,structured=True,free=True,validated=True,quality={'en':.98})
        store.register(cap);store.register(replace(cap,model='second'));store.configure('ollama',True)
        r=api.post('/api/hybrid/personal/providers/ollama/models',headers=self.headers,json={'models':['second']});self.assertEqual(r.status_code,200)
        self.assertEqual([m.model for m in ModelRouter(store).select('personal','en',1000)],['second'])
        self.assertEqual(len(ModelRouter(store).select('university','en',1000)),2)
        self.assertEqual(api.post('/api/hybrid/personal/providers/ollama/models',headers=self.headers,json={'models':['unknown']}).status_code,400)
        self.assertEqual(api.get('/api/hybrid/personal').json()['providers'][0]['provider'],'ollama')
        self.assertEqual(api.get('/api/hybrid/university').json()['grants'],[])

    def test_credential_enabled_connected_and_authorized_are_independent(self):
        api=self.api()
        api.post('/api/hybrid/personal/providers/groq',headers=self.headers,json={'key':'fixture-private-key','enabled':False})
        provider=next(p for p in api.get('/api/hybrid/personal').json()['providers'] if p['provider']=='groq')
        self.assertTrue(provider['configured']);self.assertFalse(provider['enabled']);self.assertFalse(provider['account_authorized']);self.assertFalse(provider['routing_active']);self.assertEqual(provider['connection'],'Not tested')

    def test_autonomous_toggle_controls_organization_without_resuming_paused_sync(self):
        api=self.api()
        r=api.post('/api/organizer/personal/local-policy',headers=self.headers,json={'enabled':True,'approved':True})
        self.assertEqual(r.status_code,200)
        self.assertTrue(api.get('/api/hybrid/personal').json()['organization_enabled'])
        self.assertFalse(api.get('/api/control/personal').json()['sync']['enabled'])
        self.assertEqual(api.get('/api/hybrid/university').json()['grants'],[])
        r=api.post('/api/organizer/personal/local-policy',headers=self.headers,json={'enabled':False,'approved':False})
        self.assertEqual(r.status_code,200)
        self.assertFalse(api.get('/api/hybrid/personal').json()['organization_enabled'])

    def test_changing_key_requires_free_reverification_without_resetting_usage(self):
        api=self.api();store=self.app.state.hybrid_store
        cap=ModelCapability('groq','fixture-model',64000,structured=True,free=True,validated=True,quality={'en':.98})
        store.register(cap,time.time()+86400);store.configure('groq',True)
        grant=store.grant('personal','groq',['fixture-model'],5,10000,10000,5,'Fixture')
        r=api.post('/api/hybrid/personal/providers/groq',headers=self.headers,json={'key':'changed-project-private','enabled':True})
        self.assertEqual(r.status_code,200)
        self.assertFalse(store.provider('groq')['enabled'])
        self.assertEqual(store.candidates('personal'),[])
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT id FROM hybrid_grants').fetchone()[0],grant)
            self.assertEqual(db.execute("SELECT free_until FROM hybrid_models WHERE provider='groq'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hybrid_attempts').fetchone()[0],0)

    def test_remote_progress_and_retry_counts_exclude_local_work_and_capacity_waits(self):
        api=self.api()
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        work=ContinuousStore(self.messages.database_path,self.messages);service=SQLiteControlCenterStore(work)
        with work.connect() as db:
            db.execute("INSERT INTO automation_events(account_id,stage,status,created_at) VALUES('personal','body','done','2026-10-29 10:00:00')")
            db.execute("INSERT INTO automation_events(account_id,stage,status,created_at) VALUES('personal','local','done','2026-10-29 11:00:00')")
            for id,error in [('capacity','read_budget_wait'),('transport','temporary_transport_failure')]:
                db.execute("INSERT INTO mail_work(account_id,message_id,stage,fingerprint,status,error_kind) VALUES('personal',?,'body','fixture','retry',?)",(id,error))
        snapshot=service.sync_snapshot('personal')
        self.assertEqual(snapshot['last_success'],'2026-10-29 10:00:00')
        self.assertEqual(snapshot['retries'],1)
        with work.connect() as db:db.execute("UPDATE automation_accounts SET last_error='delta_ReadBudgetExceeded' WHERE account_id='personal'")
        self.assertEqual(api.get('/api/control/personal').json()['account_connection'],'Connected')

    def test_progress_is_a_disjoint_union_not_a_sum_of_overlapping_queues(self):
        api=self.api();original=api.get('/api/control/personal').json()['progress'];other=api.get('/api/control/university').json()['progress']['total']
        self.add('indexed','Unknown message');self.add('shared','Unknown message')
        self.messages.record_search_content('personal','shared','Complete current body.',complete=True)
        with self.app.state.hybrid_store.connect() as db:
            db.executemany("INSERT INTO history_observations VALUES('personal',?,0)",[('shared',),('discovered-only',)])
        p=api.get('/api/control/personal').json()['progress']
        self.assertEqual(p['total'],original['total']+3);self.assertEqual(p['bodies'],original['bodies']+1)
        self.assertEqual(sum(p[k] for k in ('organized','waiting_gmail','waiting_ai','failed_blocked')),p['total'])
        self.assertLessEqual(p['organized'],p['total'])
        self.assertEqual(api.get('/api/control/university').json()['progress']['total'],other)

    def test_live_activity_is_not_connected_status_and_stale_runs_do_not_run_forever(self):
        from email_agent.application.hybrid_ai import InferenceResult
        api=self.api();store=self.app.state.hybrid_store;cap=ModelCapability('ollama','local-fixture',32000,free=True)
        with store.connect() as db:
            db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,created_at) VALUES('personal','ollama','local-fixture','old',?)",(time.time()-130,))
            key=store._task_key(db,cap,'current')
            id=db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,created_at) VALUES('personal','ollama','local-fixture',?,?)",(key,time.time())).lastrowid
        before=api.get('/api/control/personal').json()['activity']
        self.assertEqual(len(before['active']),1)
        self.assertEqual(before['history'][1]['result'],'interrupted');self.assertIsNone(before['history'][1]['finished_at'])
        store.finish(id,'done',InferenceResult('private response must not enter activity','ollama',cap.model,{'prompt_tokens':42,'output_tokens':7},.1))
        store.record_organization_result('personal','current',cap,True,1)
        after=api.get('/api/control/personal').json()['activity']
        self.assertEqual(after['active'],[]);self.assertEqual(after['history'][0]['category_changes'],1)
        self.assertTrue(after['history'][0]['finished_at']);self.assertEqual(after['last_successful_inference'],after['history'][0]['finished_at'])
        self.assertNotIn('private response',json.dumps(after));self.assertEqual(after['history'][0]['input_tokens'],42)

    def test_cumulative_organization_survives_pending_rechecks_and_manual_without_frame(self):
        api=self.api()
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        work=ContinuousStore(self.messages.database_path,self.messages)
        service=SQLiteControlCenterStore(work);before=service.progress('personal')['organized']
        for id in ('evidenced','manual-no-frame'):
            self.add(id,'Known message');self.messages.record_search_content('personal',id,'Complete message.',complete=True)
        with work.connect() as db:
            db.execute("UPDATE message_category_assignments SET status='classified',category_id='bills',source='automatic' WHERE message_id='evidenced'")
            db.execute("UPDATE message_category_assignments SET status='classified',category_id='bills',source='manual' WHERE message_id='manual-no-frame'")
            db.execute("INSERT OR REPLACE INTO semantic_frames(account_id,message_id,input_fingerprint,payload,source,version) VALUES('personal','evidenced','fixture',?,'local','fixture')",(json.dumps({'understood':True}),))
            db.execute("DELETE FROM semantic_frames WHERE message_id='manual-no-frame'")
            for stage in ('local','semantic'):
                db.execute("INSERT INTO mail_work(account_id,message_id,stage,fingerprint,status) VALUES('personal','evidenced',?,'new-policy','pending')",(stage,))
        self.assertEqual(service.progress('personal')['organized'],before+2)
        self.assertEqual(work.progress('personal')['organized'],before+2)
        self.assertEqual(api.get('/api/control/personal').json()['progress']['organized'],before+2)

    def test_light_activity_reads_shared_worker_and_expires_lease_without_status_scans(self):
        from unittest.mock import patch
        api=self.api();store=self.app.state.hybrid_store
        with store.connect() as db:
            id=db.execute("INSERT INTO hybrid_attempts(account_id,provider,model,task_key,created_at,worker_id,lease_until) VALUES('university','gemini','real-model-fixture','mail-fixture',?,'fixture-worker',?)",(time.time(),time.time()+120)).lastrowid
        with patch.object(self.app.state,'ai_status_snapshot',side_effect=AssertionError('No expensive status query')):
            data=api.get('/api/control/personal?activity_only=true').json()
            self.assertEqual(data['active'][0]['account'],'university');self.assertEqual(data['active'][0]['worker_id'],'fixture-worker');self.assertEqual(data['state'],'Processing')
            with store.connect() as db:db.execute('UPDATE hybrid_attempts SET lease_until=? WHERE id=?',(time.time()-1,id))
            self.assertEqual(api.get('/api/control/personal?activity_only=true').json()['active'],[])
        store.finish(id,'interrupted')
        with store.connect() as db:
            r=db.execute('SELECT status,finished_at,lease_until FROM hybrid_attempts WHERE id=?',(id,)).fetchone()
            self.assertEqual(r['status'],'interrupted');self.assertTrue(r['finished_at']);self.assertEqual(r['lease_until'],0)
