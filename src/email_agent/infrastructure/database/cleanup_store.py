"""Version-one additive cleanup migration and durable future-event queue."""
from contextlib import closing
import json
import sqlite3
import time
import uuid
from pathlib import Path


class SQLiteCleanupStore:
    def __init__(self,path,events_path):
        self.path,self.events_path=Path(path),Path(events_path)
        with closing(self.connect()) as db,db:
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_schema_versions(version INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_policy(account_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,mode TEXT NOT NULL,exclusions TEXT NOT NULL,revision TEXT NOT NULL,watermark INTEGER NOT NULL,enabled_at_ms INTEGER NOT NULL,approved_at REAL,paused_reason TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_items(account_id TEXT NOT NULL,message_id TEXT NOT NULL,revision TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,message_id))")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_audit(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,message_id TEXT,event TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS cleanup_runs(account_id TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS cleanup_pending ON cleanup_items(account_id,status)")
            db.execute("INSERT OR IGNORE INTO cleanup_schema_versions VALUES (1)")

        from email_agent.infrastructure.database.mail_store import SQLiteMailStore
        self.management=SQLiteMailStore(self.path,self.events_path)

    def protection(self,account,message,ignore_action=None,ignore_system=False):return self.management.protection(account,message,ignore_action,ignore_system)

    def reconsider_future(self,account,id,revision,digest):
        from email_agent.application.subscriptions import subscription_identity
        from email.utils import parseaddr
        with closing(self.connect()) as db,db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT m.*,h.headers,c.digest,a.category_id,a.source,a.status,p.enabled_at_ms
                FROM messages m JOIN message_content_state c USING(account_id,message_id) JOIN message_category_assignments a USING(account_id,message_id)
                JOIN cleanup_policy p USING(account_id) LEFT JOIN message_list_headers h USING(account_id,message_id)
                WHERE m.account_id=? AND m.message_id=? AND p.enabled=1 AND p.revision=? AND c.status='complete' AND c.digest=?''',(account,id,revision,digest)).fetchone()
            if not row or row['source']!='automatic' or row['status']!='classified' or row['category_id']!='promotions' or row['internal_date_ms']<=row['enabled_at_ms']:return False
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='automation_accounts'").fetchone():
                state=db.execute('SELECT started_ms FROM automation_accounts WHERE account_id=?',(account,)).fetchone()
                if state and row['internal_date_ms']<=state[0]:return False
            identity,kind=subscription_identity(json.loads(row['headers'] or '{}'),row['sender'])
            scopes=[('message',id),('sender',parseaddr(row['sender'])[1].casefold())]
            if kind!='promotional_sender':scopes.append(('list',identity))
            from email_agent.application.promotional_cleanup import PROTECTED_LABELS
            if not row['labels_known'] or set(json.loads(row['label_ids'] or '[]'))&PROTECTED_LABELS:return False
            obsolete={'Semantic sensitivity or unresolved meaning; retain for organization','Content purpose not verified; retained during content review'}
            existing=db.execute('SELECT status FROM cleanup_items WHERE account_id=? AND message_id=?',(account,id)).fetchone()
            if existing and existing[0]!='skipped':return False
            for scope,value in scopes:
                hold=db.execute('SELECT reason FROM cleanup_protections WHERE account_id=? AND scope=? AND identity=?',(account,scope,value)).fetchone()
                if hold and (scope!='message' or hold[0] not in obsolete):return False
            changed=db.execute("DELETE FROM cleanup_protections WHERE account_id=? AND scope='message' AND identity=? AND reason IN (?,?)",(account,id,*sorted(obsolete))).rowcount
            if changed:db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) VALUES(?,?,'system_hold_released',?)",(account,id,json.dumps({'reason':'Verified complete future advertising; existing policy only'})))
            db.execute("INSERT INTO cleanup_items(account_id,message_id,revision,status) VALUES(?,?,?,'queued') ON CONFLICT(account_id,message_id) DO UPDATE SET revision=excluded.revision,status='queued',updated_at=CURRENT_TIMESTAMP WHERE cleanup_items.status='skipped'",(account,id,revision))
            db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) VALUES(?,?,'evidence_requeued',?)",(account,id,json.dumps({'reason':'New complete advertising evidence','revision':revision})))
            return True
    def keep_message(self,account,id):return self.management.keep_message(account,id)

    def connect(self):return sqlite3.connect(self.path,timeout=30)

    def policy(self,account):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM cleanup_policy WHERE account_id=?",(account,)).fetchone()
        if row is None:return {"enabled":False,"mode":"review","exclusions":[],"revision":"","watermark":0,"enabled_at_ms":0,"approved_at":None,"paused_reason":None}
        value=dict(row);value["enabled"]=bool(value["enabled"]);value["exclusions"]=json.loads(value["exclusions"]);return value

    def events(self,account,after=None):
        if not self.events_path.exists():return 0 if after is None else []
        with closing(sqlite3.connect(self.events_path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_events'").fetchone():return 0 if after is None else []
            if after is None:return db.execute("SELECT COALESCE(MAX(rowid),0) FROM inbox_events WHERE account_id=?",(account,)).fetchone()[0]
            return db.execute("SELECT rowid,message_id FROM inbox_events WHERE account_id=? AND rowid>? ORDER BY rowid LIMIT 100",(account,after)).fetchall()

    def configure(self,account,enabled,mode,exclusions,approved,expected_revision=None):
        watermark=self.events(account)
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT revision FROM cleanup_policy WHERE account_id=?",(account,)).fetchone()
            if expected_revision is not None and expected_revision!=(current[0] if current else ""):raise ValueError("Cleanup settings changed; reload before saving")
            db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) SELECT account_id,message_id,'cancelled',json_object('reason','Policy changed before execution') FROM cleanup_items WHERE account_id=? AND status='queued'",(account,))
            db.execute("UPDATE cleanup_items SET status='cancelled',detail=json_set(detail,'$.reason','Policy changed before execution'),updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND status='queued'",(account,))
            revision=uuid.uuid4().hex
            db.execute("INSERT INTO cleanup_policy VALUES (?,?,?,?,?,?,?,?,NULL) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,mode=excluded.mode,exclusions=excluded.exclusions,revision=excluded.revision,watermark=excluded.watermark,enabled_at_ms=excluded.enabled_at_ms,approved_at=excluded.approved_at,paused_reason=NULL",(account,int(enabled),mode,json.dumps(exclusions),revision,watermark,int(time.time()*1000),time.time() if enabled and mode=="automatic" and approved else None))
            db.execute("INSERT INTO cleanup_audit(account_id,event,detail) VALUES (?,'policy_changed',?)",(account,json.dumps({"enabled":enabled,"mode":mode,"revision":revision,"exclusions":exclusions,"approved":approved})))

    def add_exclusion(self,account,address):
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT exclusions,revision FROM cleanup_policy WHERE account_id=?",(account,)).fetchone()
            if row is None:raise ValueError("Save a cleanup policy first")
            values=sorted(set(json.loads(row[0])+[address]))
            if len(values)>50:raise ValueError("Exclusion limit reached")
            revision=uuid.uuid4().hex
            db.execute("UPDATE cleanup_policy SET exclusions=?,revision=? WHERE account_id=?",(json.dumps(values),revision,account))
            db.execute("UPDATE cleanup_items SET revision=? WHERE account_id=? AND revision=? AND status='queued'",(revision,account,row[1]))
            db.execute("INSERT INTO cleanup_audit(account_id,event,detail) VALUES (?,'sender_protected',?)",(account,json.dumps({"sender":address})))

    def ingest(self,account):
        policy=self.policy(account)
        if not policy["enabled"]:return
        events=self.events(account,policy["watermark"])
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM cleanup_policy WHERE account_id=? AND enabled=1 AND revision=? AND watermark=?",(account,policy["revision"],policy["watermark"])).fetchone():return
            for _,id in events:
                db.execute("INSERT OR IGNORE INTO cleanup_items(account_id,message_id,revision,status) VALUES (?,?,?,'queued')",(account,id,policy["revision"]))
            if events:db.execute("UPDATE cleanup_policy SET watermark=? WHERE account_id=?",(events[-1][0],account))

    def pending(self,account,limit):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute("SELECT i.* FROM cleanup_items i JOIN cleanup_policy p ON p.account_id=i.account_id WHERE i.account_id=? AND (i.revision=p.revision OR i.status IN ('running','uncertain')) AND i.status IN ('queued','running','uncertain') ORDER BY CASE WHEN i.status='queued' THEN 1 ELSE 0 END,i.rowid LIMIT ?",(account,limit)).fetchall()
        return [{**dict(r),"detail":json.loads(r["detail"])} for r in rows]

    def claim(self,account,id,revision,detail):
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM cleanup_policy WHERE account_id=? AND revision=? AND enabled=1 AND mode='automatic' AND approved_at IS NOT NULL",(account,revision)).fetchone():return False
            changed=db.execute("UPDATE cleanup_items SET status='running',detail=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=? AND revision=? AND status='queued'",(json.dumps(detail),account,id,revision)).rowcount
            if changed:db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) VALUES (?,?,'started',?)",(account,id,json.dumps({**detail,"revision":revision})))
            return bool(changed)

    def finish(self,account,id,status,detail):
        with closing(self.connect()) as db,db:
            if status=='succeeded':db.execute("UPDATE cleanup_assessments SET verdict='protected',reason='Already in Gmail Trash',confidence='Protected',updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?",(account,id))
            db.execute("UPDATE cleanup_items SET status=?,detail=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?",(status,json.dumps(detail),account,id))
            db.execute("INSERT INTO cleanup_audit(account_id,message_id,event,detail) VALUES (?,?,?,?)",(account,id,status,json.dumps(detail)))

    def pause(self,account,reason):
        with closing(self.connect()) as db,db:
            db.execute("UPDATE cleanup_policy SET enabled=0,paused_reason=? WHERE account_id=?",(reason,account))
            db.execute("INSERT INTO cleanup_audit(account_id,event,detail) VALUES (?,'paused',?)",(account,json.dumps({"reason":reason})))

    def acquire(self,account):
        owner=uuid.uuid4().hex
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT expires FROM cleanup_runs WHERE account_id=?",(account,)).fetchone()
            if row and row[0]>time.time():return None
            db.execute("INSERT INTO cleanup_runs VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET owner=excluded.owner,expires=excluded.expires",(account,owner,time.time()+900))
        return owner

    def release(self,account,owner):
        with closing(self.connect()) as db,db:db.execute("DELETE FROM cleanup_runs WHERE account_id=? AND owner=?",(account,owner))

    def item(self,account,id):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM cleanup_items WHERE account_id=? AND message_id=?",(account,id)).fetchone()
        if row is None:raise LookupError("No cleanup record in this account")
        return {**dict(row),"detail":json.loads(row["detail"])}

    def cancellation_status(self,account,subscription):
        with closing(self.connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='subscription_request_state'").fetchone():return None
            row=db.execute("SELECT status FROM subscription_request_state WHERE account_id=? AND subscription_id=?",(account,subscription)).fetchone()
        return row[0] if row else None

    def history(self,account):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            items=[dict(r) for r in db.execute("SELECT i.*,m.sender,m.subject,m.label_ids FROM cleanup_items i LEFT JOIN messages m ON m.account_id=i.account_id AND m.message_id=i.message_id WHERE i.account_id=? ORDER BY i.updated_at DESC,i.rowid DESC LIMIT 50",(account,))]
            today=db.execute("SELECT COUNT(*) FROM cleanup_items i WHERE i.account_id=? AND i.status='succeeded' AND EXISTS(SELECT 1 FROM cleanup_audit a WHERE a.account_id=i.account_id AND a.message_id=i.message_id AND a.event='started' AND date(a.created_at)=date('now'))",(account,)).fetchone()[0]
            audit=[dict(r) for r in db.execute("SELECT * FROM cleanup_audit WHERE account_id=? ORDER BY id DESC LIMIT 100",(account,))]
        for item in items:
            item["detail"]=json.loads(item["detail"])
            item["label_ids"]=json.loads(item["label_ids"]) if item["label_ids"] else None
        for event in audit:event["detail"]=json.loads(event["detail"])
        return {"items":items,"cleaned_today":today,"timezone":"UTC","audit":audit}
