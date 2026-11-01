"""Durable per-account queues, leases, safe errors and read attempt accounting."""
from contextlib import closing,contextmanager
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import uuid
from email_agent.application.classify_messages import VERSION
from email_agent.application.semantic_mail import SEMANTIC_VERSION
from email_agent.infrastructure.database.content_fill_store import SQLiteContentFillStore

# A queued recheck does not erase an existing evidenced classification.
ORGANIZED_SQL="a.status='classified' AND (a.source='manual' OR (c.status='complete' AND json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL))"

LOCAL_VERSION=VERSION+':'+SEMANTIC_VERSION+':continuous-2'


class ReadBudgetExceeded(RuntimeError):
    pause_work=True

class MessageReadLimit(ReadBudgetExceeded):
    pause_work=False


class ContinuousStore:
    DAILY_READ_LIMIT=10000
    def __init__(self,path,messages):self.path,self.messages=Path(path),messages

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            db.row_factory=sqlite3.Row
            with db:yield db

    def initialize(self):
        SQLiteContentFillStore(self.path,self.messages).initialize()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_frames'").fetchone():db.execute('CREATE INDEX IF NOT EXISTS semantic_frame_latest ON semantic_frames(account_id,updated_at DESC)')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS automation_versions(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO automation_versions VALUES(1);
            CREATE TABLE IF NOT EXISTS automation_accounts(account_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,last_run TEXT,last_error TEXT);
            CREATE TABLE IF NOT EXISTS mail_work(account_id TEXT,message_id TEXT,stage TEXT,fingerprint TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,wire_attempts INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,lease REAL NOT NULL DEFAULT 0,error_kind TEXT,PRIMARY KEY(account_id,message_id,stage));
            CREATE INDEX IF NOT EXISTS mail_work_due ON mail_work(account_id,stage,status,due);
            CREATE TABLE IF NOT EXISTS automation_events(id INTEGER PRIMARY KEY,account_id TEXT,message_id TEXT,stage TEXT,status TEXT,reason TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS automation_runs(id TEXT PRIMARY KEY,attempt_cap INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS automation_requests(id INTEGER PRIMARY KEY,run_id TEXT,account_id TEXT,kind TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS automation_request_day ON automation_requests(created_at);
            CREATE TABLE IF NOT EXISTS automation_history_archive(id INTEGER PRIMARY KEY,account_id TEXT,state TEXT NOT NULL,observations TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS automation_inventory_retry(account_id TEXT PRIMARY KEY,attempts INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,error_kind TEXT);
            CREATE TABLE IF NOT EXISTS automation_delta(account_id TEXT PRIMARY KEY,start_history_id TEXT NOT NULL,next_token TEXT,last_success TEXT);
            CREATE TABLE IF NOT EXISTS automation_remote_absence(account_id TEXT,message_id TEXT,history_id TEXT,PRIMARY KEY(account_id,message_id));
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(automation_accounts)')}
            if 'started_ms' not in columns:
                db.execute('ALTER TABLE automation_accounts ADD COLUMN started_ms INTEGER NOT NULL DEFAULT 0')
                db.execute('UPDATE automation_accounts SET started_ms=? WHERE started_ms=0',(int(time.time()*1000),))
            if 'wait_reason' not in columns:db.execute('ALTER TABLE automation_accounts ADD COLUMN wait_reason TEXT')
            db.execute('INSERT OR IGNORE INTO automation_versions VALUES(2)')
            if 'sync_enabled' not in columns:
                db.execute('ALTER TABLE automation_accounts ADD COLUMN sync_enabled INTEGER NOT NULL DEFAULT 0')
                db.execute('UPDATE automation_accounts SET sync_enabled=enabled')
            db.execute('CREATE TABLE IF NOT EXISTS sync_runtime(account_id TEXT PRIMARY KEY,lease REAL NOT NULL DEFAULT 0,stage TEXT)')
            db.execute('INSERT OR IGNORE INTO automation_versions VALUES(5)')
            run_columns={r[1] for r in db.execute('PRAGMA table_info(automation_runs)')}
            if 'deadline' not in run_columns:db.execute('ALTER TABLE automation_runs ADD COLUMN deadline REAL NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO automation_versions VALUES(3)')
            if not db.execute('SELECT 1 FROM automation_versions WHERE version=4').fetchone():
                db.execute("INSERT INTO automation_events(account_id,message_id,stage,status,reason) SELECT account_id,message_id,'body','parser_upgrade',error_kind FROM mail_work WHERE stage='body' AND status='failed' AND error_kind='parsing_failure' AND attempts<3 AND wire_attempts<6")
                db.execute("UPDATE mail_work SET fingerprint='mime-full-2',status='retry',due=0,lease=0 WHERE stage='body' AND status='failed' AND error_kind='parsing_failure' AND attempts<3 AND wire_attempts<6")
                db.execute('INSERT INTO automation_versions VALUES(4)')
            db.execute("UPDATE mail_work SET status='retry',due=?,error_kind='temporary_transport_failure' WHERE status='failed' AND error_kind IN ('ServerNotFoundError','TransportError') AND attempts<3",(time.time()+60,))

    def configure(self,account,enabled):
        if account not in ('personal','university') or type(enabled)is not bool:raise ValueError('Invalid account control')
        with self.connect() as db:
            db.execute('INSERT INTO automation_accounts(account_id,enabled,sync_enabled) VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,sync_enabled=excluded.sync_enabled',(account,int(enabled),int(enabled)))
            db.execute('UPDATE automation_accounts SET started_ms=? WHERE account_id=? AND started_ms=0',(int(time.time()*1000),account))
            db.execute("INSERT INTO automation_events(account_id,stage,status,reason) VALUES(?,'control',?,'Explicit local organizer setup')",(account,'enabled' if enabled else 'paused'))

    def enabled(self,account):
        with self.connect() as db:
            row=db.execute('SELECT enabled FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            return bool(row and row[0])

    def configure_organization(self,account,enabled):
        if account not in ('personal','university') or type(enabled)is not bool:raise ValueError('Invalid account control')
        with self.connect() as db:
            db.execute('INSERT INTO automation_accounts(account_id,enabled,sync_enabled) VALUES(?,?,0) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled',(account,int(enabled)))
            db.execute("INSERT INTO automation_events(account_id,stage,status,reason) VALUES(?,'organization_control',?,'User control; historical sync unchanged')",(account,'enabled' if enabled else 'paused'))

    def sync_enabled(self,account):
        with self.connect() as db:
            row=db.execute('SELECT sync_enabled FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            return bool(row and row[0])

    def sync_activity(self,account,stage=None):
        with self.connect() as db:
            db.execute('INSERT INTO sync_runtime VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET lease=excluded.lease,stage=excluded.stage',(account,time.time()+420 if stage else 0,stage))

    def seed(self,account):
        with self.connect() as db:
            policy_revision=str(db.execute("SELECT COALESCE(MAX(id),0) FROM semantic_taxonomy_audit WHERE action='applied'").fetchone()[0])+':'+str(db.execute('SELECT COALESCE(MAX(id),0) FROM organization_audit WHERE account_id=?',(account,)).fetchone()[0])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_organization_audit'").fetchone():policy_revision+=':'+str(db.execute("SELECT COALESCE(MAX(id),0) FROM local_organization_audit WHERE account_id=? AND kind='manual_feedback'",(account,)).fetchone()[0])
            db.execute("INSERT OR IGNORE INTO mail_work(account_id,message_id,stage,fingerprint) SELECT account_id,message_id,'metadata','metadata-1' FROM history_observations o WHERE account_id=? AND fetched=0 AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.account_id=o.account_id AND m.message_id=o.message_id AND m.labels_known=1)",(account,))
            db.execute("INSERT OR IGNORE INTO mail_work(account_id,message_id,stage,fingerprint) SELECT account_id,message_id,'metadata','metadata-1' FROM messages WHERE account_id=? AND labels_known=0",(account,))
            db.execute("INSERT OR IGNORE INTO mail_work(account_id,message_id,stage,fingerprint) SELECT m.account_id,m.message_id,'body','mime-full-2' FROM messages m WHERE m.account_id=? AND NOT EXISTS(SELECT 1 FROM message_content_state c WHERE c.account_id=m.account_id AND c.message_id=m.message_id AND c.status='complete')",(account,))
            db.execute("UPDATE mail_work SET status='done',lease=0,error_kind=NULL WHERE account_id=? AND stage='body' AND status!='done' AND EXISTS(SELECT 1 FROM message_content_state c WHERE c.account_id=mail_work.account_id AND c.message_id=mail_work.message_id AND c.status='complete')",(account,))
            db.execute("UPDATE mail_work SET due=-COALESCE((SELECT internal_date_ms/1000.0 FROM messages m WHERE m.account_id=mail_work.account_id AND m.message_id=mail_work.message_id),0) WHERE account_id=? AND stage IN ('body','local') AND status='pending' AND due=0 AND message_id IN (SELECT m.message_id FROM messages m JOIN automation_accounts a USING(account_id) WHERE m.account_id=? AND m.internal_date_ms>=a.started_ms)",(account,account))
            # Preserve terminal failures from the previous explicitly scoped fill.
            for row in db.execute("SELECT i.message_id,i.result FROM content_fill_items i JOIN mail_work w USING(account_id,message_id) WHERE i.account_id=? AND i.status='failed' AND w.stage='body' AND w.status='pending'",(account,)).fetchall():
                result=json.loads(row['result'] or '{}')
                if result.get('error_kind')=='EmptyBody':
                    types=result.get('mime_types',{});kind='attachment_only' if any(k.startswith(('image/','application/')) for k in types) else 'empty'
                    db.execute("UPDATE mail_work SET status=?,error_kind='previous_empty_mime' WHERE account_id=? AND message_id=? AND stage='body'",(kind,account,row['message_id']))
            db.execute('''INSERT INTO mail_work(account_id,message_id,stage,fingerprint)
                SELECT m.account_id,m.message_id,'local',json_array(m.subject,m.sender,m.label_ids,h.headers,COALESCE(c.digest,''),?) FROM messages m
                LEFT JOIN message_list_headers h USING(account_id,message_id) LEFT JOIN message_content_state c USING(account_id,message_id)
                WHERE m.account_id=? ON CONFLICT(account_id,message_id,stage) DO UPDATE SET fingerprint=excluded.fingerprint,status='pending',attempts=0,wire_attempts=0,due=0,lease=0,error_kind=NULL WHERE mail_work.fingerprint!=excluded.fingerprint''',(LOCAL_VERSION+':policy-'+policy_revision,account))
            # Classification updates of old mail must never authorize historical
            # Trash through the independent future-mail cleanup policy.
            db.execute("INSERT OR IGNORE INTO cleanup_protections(account_id,scope,identity,reason) SELECT m.account_id,'message',m.message_id,'Historical discovery; no historical cleanup approval' FROM messages m JOIN cleanup_policy p USING(account_id) JOIN automation_accounts a USING(account_id) WHERE m.account_id=? AND m.internal_date_ms<=MAX(p.enabled_at_ms,a.started_ms)",(account,))

    def claim(self,account,stage):
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mail_work SET status='failed',error_kind='interrupted_retry_limit' WHERE account_id=? AND stage=? AND status='running' AND lease<? AND attempts>=3",(account,stage,now))
            row=db.execute("SELECT * FROM mail_work WHERE account_id=? AND stage=? AND ((status IN ('pending','retry') AND due<=?) OR (status='running' AND lease<?)) AND attempts<3 ORDER BY due,message_id LIMIT 1",(account,stage,now,now)).fetchone()
            if not row:return None
            db.execute("UPDATE mail_work SET status='running',attempts=attempts+1,lease=? WHERE account_id=? AND message_id=? AND stage=?",(now+600,account,row['message_id'],stage))
            return dict(row)

    def finish(self,item,status,reason=None):
        with self.connect() as db:
            db.execute('UPDATE mail_work SET status=?,error_kind=?,lease=0 WHERE account_id=? AND message_id=? AND stage=?',(status,reason,item['account_id'],item['message_id'],item['stage']))
            db.execute('INSERT INTO automation_events(account_id,message_id,stage,status,reason) VALUES(?,?,?,?,?)',(item['account_id'],item['message_id'],item['stage'],status,reason))

    def failed(self,item,error):
        if getattr(error,'pause_work',False):
            with self.connect() as db:
                db.execute("UPDATE mail_work SET status='retry',attempts=MAX(0,attempts-1),due=?,lease=0,error_kind='read_budget_wait' WHERE account_id=? AND message_id=? AND stage=?",(time.time()+120,item['account_id'],item['message_id'],item['stage']))
                db.execute('UPDATE automation_accounts SET wait_reason=? WHERE account_id=?',(str(error),item['account_id']))
            return
        status=getattr(getattr(error,'resp',None),'status',None)
        transient=isinstance(error,(TimeoutError,ConnectionError,OSError)) or type(error).__name__ in {'ServerNotFoundError','TransportError'} or status in (429,500,502,503,504)
        kind='inaccessible' if status in (401,403,404) or isinstance(error,LookupError) else 'parsing_failure' if isinstance(error,(ValueError,UnicodeError)) else type(error).__name__
        kind={'MIMECharsetError':'charset_unresolved','MIMETextTooLarge':'body_too_large','MessageReadLimit':'transport_retry_limit'}.get(type(error).__name__,kind)
        retry=transient and item['attempts']<2
        self.finish(item,'retry' if retry else 'failed',kind)
        if retry:
            with self.connect() as db:db.execute('UPDATE mail_work SET due=? WHERE account_id=? AND message_id=? AND stage=?',(time.time()+min(900,60*4**item['attempts']),item['account_id'],item['message_id'],item['stage']))

    def has_body(self,account,id):
        with self.connect() as db:return bool(db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,id)).fetchone())

    def save_body(self,account,id,evidence):SQLiteContentFillStore(self.path,self.messages).save(account,id,evidence)

    def empty_body(self,item,evidence):
        types=evidence.get('mime_types',{});kind='attachment_only' if any(t.startswith(('image/','application/')) for t in types) else 'empty'
        self.finish(item,kind,'No usable visible MIME text')

    def metadata_saved(self,account,id):
        with self.connect() as db:db.execute('UPDATE history_observations SET fetched=1 WHERE account_id=? AND message_id=?',(account,id))

    def queue_semantic(self,account,id):
        from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore,input_fingerprint
        store=SQLiteSemanticOrganizerStore(self.path,self.path.parent/'categories.db');row=store.message_row(account,id)
        frame=(row or {}).get('semantic') or {}
        if not row or not row['content_complete']:return
        if row['decision']['source']=='manual' and (row['decision']['category_id'] not in {'job_applications','orders','bills'} or frame.get('status')):return
        fingerprint=input_fingerprint(row)
        from email_agent.application.semantic_mail import validated_model_frame
        if validated_model_frame(frame) and frame.get('input_coverage',{}).get('body_complete') and row['input_fingerprint']==fingerprint:
            status='pending' if frame.get('workflow_needs_escalation') else 'done' if frame.get('understood') and not frame.get('analysis_reason') else 'uncertain'
            with self.connect() as db:db.execute("UPDATE mail_work SET status=? WHERE account_id=? AND message_id=? AND stage='semantic' AND status IN ('pending','retry')",(status,account,id))
            return
        missing_workflow=(row['decision']['category_id'] in {'job_applications','orders'} or (row['decision']['category_id']=='bills' and frame.get('message_type')=='Invoice')) and not frame.get('status')
        if frame.get('source')=='local' and frame.get('understood') and not frame.get('analysis_reason') and not missing_workflow:
            if frame.get('source')!='gemini':
                with self.connect() as db:db.execute("UPDATE mail_work SET status='local_resolved' WHERE account_id=? AND message_id=? AND stage='semantic' AND status IN ('pending','retry')",(account,id))
            return
        with self.connect() as db:
            db.execute("INSERT INTO mail_work(account_id,message_id,stage,fingerprint) VALUES(?,?,'semantic',?) ON CONFLICT(account_id,message_id,stage) DO UPDATE SET fingerprint=excluded.fingerprint,status='pending',attempts=0,due=0,lease=0 WHERE mail_work.fingerprint!=excluded.fingerprint",(account,id,fingerprint+':semantic-policy-2'))

    def begin_run(self,cap=96,duration=70):
        id=uuid.uuid4().hex
        with self.connect() as db:db.execute('INSERT INTO automation_runs(id,attempt_cap,deadline) VALUES(?,?,?)',(id,cap,time.time()+duration))
        return id

    def read_ready(self,account):
        """Do not claim/retry remote items when the shared local wire budget is closed."""
        with self.connect() as db:
            state=db.execute('SELECT sync_enabled FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            used=db.execute("SELECT COUNT(*) FROM automation_requests WHERE created_at>=date('now')").fetchone()[0]
            return bool(state and state[0] and used<self.DAILY_READ_LIMIT)

    def reserve_read(self,run,account,kind,item=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sync_state=db.execute('SELECT sync_enabled FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            if sync_state and not sync_state[0]:raise ReadBudgetExceeded('Historical sync paused')
            db.execute('UPDATE sync_runtime SET stage=?,lease=? WHERE account_id=?',(kind,time.time()+420,account))
            state=db.execute('SELECT deadline FROM automation_runs WHERE id=?',(run,)).fetchone()
            if not state or state[0] and state[0]<time.time():raise ReadBudgetExceeded('Worker wall-clock budget reached')
            today=db.execute("SELECT COUNT(*) FROM automation_requests WHERE created_at>=date('now')").fetchone()[0]
            if today>=self.DAILY_READ_LIMIT:raise ReadBudgetExceeded('Daily local Gmail read ceiling reached')
            if item and db.execute('SELECT wire_attempts FROM mail_work WHERE account_id=? AND message_id=? AND stage=?',(account,item['message_id'],item['stage'])).fetchone()[0]>=6:raise MessageReadLimit('Message transport retry cap reached')
            if not db.execute('UPDATE automation_runs SET attempts=attempts+1 WHERE id=? AND attempts<attempt_cap',(run,)).rowcount:raise ReadBudgetExceeded('Worker network attempt cap reached')
            if item:db.execute('UPDATE mail_work SET wire_attempts=wire_attempts+1 WHERE account_id=? AND message_id=? AND stage=?',(account,item['message_id'],item['stage']))
            db.execute('INSERT INTO automation_requests(run_id,account_id,kind) VALUES(?,?,?)',(run,account,kind))

    def progress(self,account):
        with self.connect() as db:
            state=db.execute('SELECT * FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
            counts=[dict(r) for r in db.execute('SELECT stage,status,COUNT(*) count FROM mail_work WHERE account_id=? GROUP BY stage,status',(account,))]
            total=db.execute('SELECT COUNT(*) FROM messages WHERE account_id=?',(account,)).fetchone()[0]
            bodies=db.execute("SELECT COUNT(*) FROM message_content_state WHERE account_id=? AND status='complete'",(account,)).fetchone()[0]
            organized=db.execute("SELECT COUNT(*) FROM message_category_assignments a LEFT JOIN message_content_state c USING(account_id,message_id) LEFT JOIN semantic_frames f USING(account_id,message_id) WHERE a.account_id=? AND "+ORGANIZED_SQL,(account,)).fetchone()[0]
            revision=str(db.execute('SELECT COALESCE(MAX(id),0) FROM classification_audit WHERE account_id=?',(account,)).fetchone()[0])+':'+str(db.execute('SELECT COALESCE(MAX(id),0) FROM semantic_frame_history WHERE account_id=?',(account,)).fetchone()[0])
            revision+=':'+str(db.execute('SELECT COALESCE(MAX(a.id),0) FROM semantic_taxonomy_audit a JOIN semantic_taxonomy_proposals p ON p.id=a.proposal_id WHERE p.account_id=?',(account,)).fetchone()[0])
            history=db.execute('SELECT completed,pages_completed FROM history_progress WHERE account_id=?',(account,)).fetchone()
            delta=db.execute('SELECT next_token,last_success FROM automation_delta WHERE account_id=?',(account,)).fetchone()
            delta_pending=not delta or bool(delta['next_token']) or not delta['last_success']
            legacy=db.execute('SELECT legacy_pages FROM history_observation_runs WHERE account_id=?',(account,)).fetchone()
            inventory_complete=bool(history and history['completed'] and not (legacy and legacy[0]))
            discovered=db.execute('SELECT COUNT(*) FROM history_observations WHERE account_id=?',(account,)).fetchone()[0]
            remaining_bodies=db.execute("SELECT COUNT(*) FROM (SELECT message_id FROM history_observations WHERE account_id=? UNION SELECT message_id FROM messages WHERE account_id=?) ids WHERE NOT EXISTS(SELECT 1 FROM message_content_state c WHERE c.account_id=? AND c.message_id=ids.message_id AND c.status='complete')",(account,account,account)).fetchone()[0]
            metadata_remaining=db.execute('SELECT COUNT(*) FROM history_observations o WHERE account_id=? AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.account_id=o.account_id AND m.message_id=o.message_id AND m.labels_known=1)',(account,)).fetchone()[0]
            waiting_gmail=db.execute("SELECT COUNT(DISTINCT message_id) FROM mail_work WHERE account_id=? AND stage IN ('body','metadata') AND status IN ('pending','retry','running')",(account,)).fetchone()[0]
            waiting_ai=db.execute("SELECT COUNT(*) FROM mail_work WHERE account_id=? AND stage='semantic' AND status IN ('pending','retry','running','uncertain')",(account,)).fetchone()[0]
            failures=db.execute("SELECT COUNT(DISTINCT message_id) FROM mail_work WHERE account_id=? AND status='failed'",(account,)).fetchone()[0]
            last_success=db.execute("SELECT MAX(t) FROM (SELECT MAX(created_at) t FROM automation_events WHERE account_id=? AND status='done' UNION ALL SELECT last_success FROM automation_delta WHERE account_id=?)",(account,account)).fetchone()[0]
            waiting=sum(r['count'] for r in counts if r['stage']!='semantic' and r['status'] in ('pending','retry','running'))
            attention=sum(r['count'] for r in counts if r['status'] in ('failed','attachment_only','empty'))
            attention+=db.execute("SELECT COUNT(*) FROM message_category_assignments a JOIN message_content_state c USING(account_id,message_id) WHERE a.account_id=? AND c.status='complete' AND a.status!='classified'",(account,)).fetchone()[0]
            attention+=db.execute("SELECT COUNT(*) FROM semantic_taxonomy_proposals WHERE account_id=? AND status IN ('suggested','approved')",(account,)).fetchone()[0]
        return dict(enabled=bool(state and state['enabled']),status='Organizing' if waiting or not inventory_complete or delta_pending else 'Needs attention' if attention or (state and state['last_error']) or any(r['stage']=='semantic' and r['status'] not in ('done','local_resolved') for r in counts) else 'Up to date',indexed=total,bodies_complete=bodies,organized=organized,revision=revision,queues=counts,inventory_complete=inventory_complete,incremental_pending=delta_pending,last_run=state['last_run'] if state else None,last_error=state['last_error'] if state else None,
            coverage=dict(discovered=discovered,bodies_available=bodies,bodies_remaining=remaining_bodies,metadata_remaining=metadata_remaining,organized=organized,waiting_gemini=waiting_ai,waiting_gmail=waiting_gmail,failed=failures,last_success=last_success,wait_reason=state['wait_reason'] if state else None))

    def commit_delta(self,account,start,token,response):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT start_history_id,next_token FROM automation_delta WHERE account_id=?',(account,)).fetchone()
            if not state or tuple(state)!=(start,token):raise ValueError('Gmail delta checkpoint changed')
            for event in response.get('history',[]):
                history_id=str(event['id']);changed=set()
                for kind in ('messagesAdded','labelsAdded','labelsRemoved'):
                    changed.update(entry['message']['id'] for entry in event.get(kind,[]))
                for id in changed:
                    db.execute('INSERT INTO history_observations VALUES(?,?,0) ON CONFLICT(account_id,message_id) DO NOTHING',(account,id))
                    db.execute("INSERT INTO mail_work(account_id,message_id,stage,fingerprint,due) VALUES(?,?,'metadata',?,?) ON CONFLICT(account_id,message_id,stage) DO UPDATE SET fingerprint=excluded.fingerprint,status='pending',attempts=0,wire_attempts=0,due=excluded.due,lease=0 WHERE mail_work.fingerprint!=excluded.fingerprint",(account,id,'delta-'+history_id,-time.time()))
                    db.execute('DELETE FROM automation_remote_absence WHERE account_id=? AND message_id=?',(account,id))
                for entry in event.get('messagesDeleted',[]):
                    db.execute('INSERT OR REPLACE INTO automation_remote_absence VALUES(?,?,?)',(account,entry['message']['id'],history_id))
            next_token=response.get('nextPageToken')
            if next_token and next_token==token:raise ValueError('Non-advancing Gmail delta')
            db.execute('UPDATE automation_delta SET start_history_id=?,next_token=?,last_success=CURRENT_TIMESTAMP WHERE account_id=?',(start if next_token else str(response['historyId']),next_token,account))
