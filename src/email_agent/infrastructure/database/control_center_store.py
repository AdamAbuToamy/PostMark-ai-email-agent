"""SQLite operational controls; never spawns processes or calls Gmail/AI."""
import time
from email_agent.application.hybrid_ai import PROVIDERS
from email_agent.infrastructure.database.continuous_store import ContinuousStore,ORGANIZED_SQL


class SQLiteControlCenterStore:
    def __init__(self, continuous: ContinuousStore):
        self.work = continuous

    def sync_snapshot(self, account):
        progress = self.work.progress(account)
        with self.work.connect() as db:
            enabled = db.execute('SELECT sync_enabled FROM automation_accounts WHERE account_id=?', (account,)).fetchone()
            runtime = db.execute('SELECT lease,stage FROM sync_runtime WHERE account_id=?', (account,)).fetchone()
            daily_used=db.execute("SELECT COUNT(*) FROM automation_requests WHERE created_at>=date('now')").fetchone()[0]
            retries = db.execute("SELECT COUNT(*) FROM mail_work WHERE account_id=? AND stage IN ('metadata','body') AND status='retry' AND COALESCE(error_kind,'')!='read_budget_wait'", (account,)).fetchone()[0]
            last_read=db.execute("SELECT MAX(t) FROM (SELECT MAX(created_at) t FROM automation_events WHERE account_id=? AND stage IN ('metadata','body') AND status='done' UNION ALL SELECT last_success FROM automation_delta WHERE account_id=?)",(account,account)).fetchone()[0]
            eligible = db.execute("SELECT COUNT(*) FROM mail_work WHERE account_id=? AND stage IN ('metadata','body') AND status='failed' AND attempts<3 AND wire_attempts<6 AND COALESCE(error_kind,'')!='inaccessible'", (account,)).fetchone()[0]
        on = bool(enabled and enabled[0])
        active_lease = bool(runtime and runtime['lease'] > time.time())
        running = active_lease
        stage_names={'body':'Reading message content','metadata':'Reading message details','delta':'Checking new mail','inventory':'Discovering older mail','oauth':'Connecting Gmail'}
        runtime_stage=runtime['stage'] if running else None
        if runtime_stage:runtime_stage=stage_names.get(runtime_stage.split('_')[0],runtime_stage)
        coverage = progress['coverage']
        reason = 'Daily local Gmail read ceiling reached' if daily_used>=self.work.DAILY_READ_LIMIT else coverage['wait_reason']
        next_read=None
        if daily_used>=self.work.DAILY_READ_LIMIT:
            from datetime import datetime,timedelta,timezone
            next_read=(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).timestamp()
        running = bool(active_lease and on and not reason)
        state = 'Pausing' if not on and active_lease else 'Paused' if not on else 'Waiting' if reason else 'Syncing' if running else 'Queued' if coverage['bodies_remaining'] or not progress['inventory_complete'] else 'Up to date'
        stage = runtime_stage if running else 'Discovering mail' if not progress['inventory_complete'] else 'Reading message content' if coverage['bodies_remaining'] else 'Monitoring new mail'
        return dict(enabled=on, state=state, running=running, stage=stage, inventory_complete=progress['inventory_complete'],
            discovered=coverage['discovered'], bodies_available=coverage['bodies_available'], bodies_remaining=coverage['bodies_remaining'],
            last_success=last_read, failures=coverage['failed'], retries=retries, retryable=eligible,
            note='Waiting for reading capacity; resumes automatically.' if on and reason else 'Finishes the current read before pausing.' if state=='Pausing' else 'The existing worker continues automatically.' if on and state=='Queued' else '',
            diagnostic=reason, next_read_at=next_read, last_run=progress['last_run'],last_error=progress['last_error'])

    def control_sync(self, account, action):
        with self.work.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT sync_enabled FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            before=bool(row and row[0]); changed=0
            if action in ('resume','pause'):
                value=action=='resume'
                db.execute('INSERT INTO automation_accounts(account_id,enabled,sync_enabled) VALUES(?,0,?) ON CONFLICT(account_id) DO UPDATE SET sync_enabled=excluded.sync_enabled',(account,int(value)))
                changed=int(before!=value)
            else:
                changed=db.execute("UPDATE mail_work SET status='retry',due=0,lease=0 WHERE account_id=? AND stage IN ('metadata','body') AND status='failed' AND attempts<3 AND wire_attempts<6 AND COALESCE(error_kind,'')!='inaccessible'",(account,)).rowcount
            if changed:db.execute("INSERT INTO automation_events(account_id,stage,status,reason) VALUES(?,'sync_control',?,'User control; checkpoints and attempt limits preserved')",(account,action))
        return dict(changed=changed, sync=self.sync_snapshot(account))

    def counters(self, account):
        with self.work.connect() as db:
            new=db.execute("SELECT COUNT(*) FROM messages m LEFT JOIN local_message_state n USING(account_id,message_id) WHERE m.account_id=? AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value='INBOX') AND COALESCE(n.state,'new')='new'",(account,)).fetchone()[0]
            awaiting=db.execute("SELECT COUNT(DISTINCT message_id) FROM mail_work WHERE account_id=? AND stage='semantic' AND status NOT IN ('done','local_resolved')",(account,)).fetchone()[0]
            cleanup=db.execute("SELECT COUNT(*) FROM cleanup_assessments WHERE account_id=? AND verdict='eligible'",(account,)).fetchone()[0]
            latest=db.execute("SELECT updated_at,source,json_extract(payload,'$.model') model FROM semantic_frames WHERE account_id=? AND source IN ("+','.join('?' for _ in PROVIDERS)+") AND json_extract(payload,'$.understood')=1 AND json_extract(payload,'$.analysis_reason') IS NULL ORDER BY updated_at DESC LIMIT 1",(account,*PROVIDERS)).fetchone()
        return dict(new_mail=new,awaiting_analysis=awaiting,cleanup_candidates=cleanup,latest_classification=dict(latest) if latest else None,progress=self.progress(account),activity=self.activity(account))

    def progress(self,account):
        with self.work.connect() as db:
            # One unique known-message population, including newly indexed delta mail.
            rows=db.execute(f"""WITH ids AS (SELECT message_id FROM history_observations WHERE account_id=? UNION SELECT message_id FROM messages WHERE account_id=?),flags AS (
                SELECT i.message_id,c.status='complete' body,
                {ORGANIZED_SQL} organized,
                t.status='blocked' OR EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=? AND w.message_id=i.message_id AND w.status IN ('failed','empty','attachment_only')) blocked,
                COALESCE(m.labels_known,0)=0 metadata_missing
                FROM ids i LEFT JOIN message_content_state c ON c.account_id=? AND c.message_id=i.message_id
                LEFT JOIN messages m ON m.account_id=? AND m.message_id=i.message_id
                LEFT JOIN message_category_assignments a ON a.account_id=? AND a.message_id=i.message_id
                LEFT JOIN semantic_frames f ON f.account_id=? AND f.message_id=i.message_id
                LEFT JOIN hybrid_tasks t ON t.account_id=? AND t.message_id=i.message_id)
                SELECT COUNT(*) total,COALESCE(SUM(body),0) bodies,
                COALESCE(SUM(organized),0) organized,
                COALESCE(SUM(CASE WHEN COALESCE(organized,0)=0 AND blocked THEN 1 ELSE 0 END),0) failed_blocked,
                COALESCE(SUM(CASE WHEN COALESCE(organized,0)=0 AND COALESCE(blocked,0)=0 AND (COALESCE(body,0)=0 OR metadata_missing) THEN 1 ELSE 0 END),0) waiting_gmail
                FROM flags""",(account,)*8).fetchone()
        p=dict(rows);p['waiting_ai']=p['total']-p['organized']-p['failed_blocked']-p['waiting_gmail'];p['denominator']='Unique discovered or locally indexed messages'
        return p

    def activity(self,account,light=False):
        import json
        now=time.time()
        with self.work.connect() as db:
            active=db.execute("SELECT id,account_id,provider,model,status,created_at,seconds,usage,error,task_key,worker_id,lease_until,(SELECT json_extract(e.detail,'$.reason') FROM hybrid_events e WHERE e.event='routing_selected' AND json_extract(e.detail,'$.attempt_id')=hybrid_attempts.id ORDER BY e.id DESC LIMIT 1) routing_reason FROM hybrid_attempts WHERE status='running' AND COALESCE(NULLIF(lease_until,0),created_at+120)>? AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' ORDER BY id DESC",(now,)).fetchall()
            enabled=db.execute('SELECT a.enabled AND COALESCE(p.enabled,0) FROM automation_accounts a LEFT JOIN local_organization_policy p USING(account_id) WHERE a.account_id=?',(account,)).fetchone()
            queued=db.execute("SELECT COUNT(*) FROM hybrid_tasks WHERE account_id=? AND status NOT IN ('classified','analyzed_other')",(account,)).fetchone()[0]
            if light:
                completed=db.execute("SELECT COUNT(*),MAX(finished_at) FROM hybrid_attempts WHERE account_id=? AND status='done' AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' AND created_at>=?",(account,now-3600)).fetchone()
                return dict(completed_1h=completed[0],last_successful_inference=completed[1],active=[self.active_run(r) for r in active],state='Processing' if active else 'Paused' if not enabled or not enabled[0] else 'Queued' if queued else 'Idle',queued=queued,refreshed_at=time.time())
            attempts=db.execute("SELECT id,provider,model,status,created_at,seconds,usage,error,task_key FROM hybrid_attempts WHERE account_id=? AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' ORDER BY id DESC LIMIT 25",(account,)).fetchall()
            events=db.execute("SELECT event,detail FROM hybrid_events WHERE account_id=? AND event IN ('inference_finished','organization_verified') ORDER BY id DESC LIMIT 200",(account,)).fetchall()
            completed=db.execute("SELECT COUNT(*),MAX(created_at) FROM hybrid_attempts WHERE account_id=? AND status='done' AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' AND created_at>=?",(account,now-3600)).fetchone()
            last=db.execute("SELECT id,created_at FROM hybrid_attempts WHERE account_id=? AND status='done' AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' ORDER BY id DESC LIMIT 1",(account,)).fetchone()
        finish={};verified={}
        for e in events:
            d=json.loads(e['detail']);target=finish if e['event']=='inference_finished' else verified
            target.setdefault(d['attempt_id'],d)
        history=[]
        for r in attempts:
            status='processing' if r['status']=='running' and r['created_at']>now-120 else 'interrupted' if r['status']=='running' else 'succeeded' if r['status']=='done' else 'failed' if r['status']=='failed' else 'interrupted' if r['status']=='interrupted' else 'waiting'
            usage=json.loads(r['usage'] or '{}');end=finish.get(r['id'],{}).get('finished_at');saved=verified.get(r['id'],{})
            history.append(dict(id=r['id'],provider=r['provider'],model=r['model'],account=account,task='Discovering categories' if r['task_key'].startswith('group:') else 'Classifying',started_at=r['created_at'],finished_at=end,duration=r['seconds'],result=status,category_changes=saved.get('category_changes'),organization_verified=saved.get('verified'),input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('output_tokens'),cost_usd=finish.get(r['id'],{}).get('cost_usd',0 if r['provider']=='ollama' else None)))
        return dict(active=[self.active_run(r) for r in active],history=history,completed_1h=completed[0],last_successful_inference=finish.get(last['id'],{}).get('finished_at') if last else None,refreshed_at=now)


    @staticmethod
    def active_run(r):
        return dict(id=r['id'],account=r['account_id'],provider=r['provider'],model=r['model'],task_id=r['id'],worker_id=r['worker_id'],routing_reason=r['routing_reason'],lease_until=r['lease_until'] or r['created_at']+120,started_at=r['created_at'],result='processing',task='Discovering categories' if r['task_key'].startswith('group:') else 'Classifying')
