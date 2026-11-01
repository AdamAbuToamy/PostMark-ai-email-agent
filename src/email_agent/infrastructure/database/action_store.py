"""Durable, account-bound previews and single-use approval claims."""
import json
import sqlite3
import time
import uuid
import threading
from contextlib import closing


class _LockedMemoryConnection(sqlite3.Connection):
    def close(self):
        try: super().close()
        finally:
            lock=getattr(self,"_store_lock",None)
            if lock is not None:
                self._store_lock=None
                lock.release()


class SQLiteActionStore:
    def __init__(self, path, *, memory=False):
        self.path = "file:chat-"+uuid.uuid4().hex+"?mode=memory&cache=shared" if memory else path
        self.memory=memory
        self._lock=threading.RLock()
        self._keeper=sqlite3.connect(self.path,uri=True,check_same_thread=False) if memory else None
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS action_previews (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, expires REAL NOT NULL, result TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS action_audit (id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS batch3_schema_versions(version INTEGER PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (1)")
            db.execute("""CREATE TABLE IF NOT EXISTS subscription_request_state (
                account_id TEXT NOT NULL, subscription_id TEXT NOT NULL, action_id TEXT NOT NULL,
                status TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_id,subscription_id))""")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (2)")
            db.execute("BEGIN IMMEDIATE") if not db.in_transaction else None
            for table in ("chat_messages","action_previews"):
                if "conversation_id" not in {r[1] for r in db.execute("PRAGMA table_info("+table+")")}:
                    db.execute("ALTER TABLE "+table+" ADD COLUMN conversation_id TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS chat_conversations(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,active INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS chat_active_account ON chat_conversations(account_id) WHERE active=1")
            for (account,) in db.execute("SELECT DISTINCT account_id FROM chat_messages WHERE conversation_id IS NULL").fetchall():
                legacy="legacy-"+account
                db.execute("INSERT OR IGNORE INTO chat_conversations(id,account_id,active) VALUES (?,?,0)",(legacy,account))
                if not db.execute("SELECT 1 FROM chat_conversations WHERE account_id=? AND active=1",(account,)).fetchone():
                    db.execute("UPDATE chat_conversations SET active=1 WHERE id=?",(legacy,))
                db.execute("UPDATE chat_messages SET conversation_id=? WHERE account_id=? AND conversation_id IS NULL",(legacy,account))
                for (content,) in db.execute("SELECT content FROM chat_messages WHERE account_id=? AND conversation_id=?",(account,legacy)):
                    for result in json.loads(content).get("results",[]):
                        if "preview" in result:
                            db.execute("UPDATE action_previews SET conversation_id=? WHERE account_id=? AND id=?",(legacy,account,result["preview"]["id"]))
            db.execute("CREATE TABLE IF NOT EXISTS chat_preferences(account_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,developer_local INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS chat_requests(account_id TEXT NOT NULL,request_id TEXT NOT NULL,conversation_id TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,result TEXT,PRIMARY KEY(account_id,request_id))")
            db.execute("CREATE TABLE IF NOT EXISTS chat_plan_cache(account_id TEXT NOT NULL,fingerprint TEXT NOT NULL,plan TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(account_id,fingerprint))")
            if "expires" not in {r[1] for r in db.execute("PRAGMA table_info(chat_requests)")}:
                db.execute("ALTER TABLE chat_requests ADD COLUMN expires REAL NOT NULL DEFAULT 0")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (3)")
            if "save_history" not in {r[1] for r in db.execute("PRAGMA table_info(chat_preferences)")}:
                db.execute("ALTER TABLE chat_preferences ADD COLUMN save_history INTEGER NOT NULL DEFAULT 1")
            if "consent_version" not in {r[1] for r in db.execute("PRAGMA table_info(chat_preferences)")}:
                db.execute("ALTER TABLE chat_preferences ADD COLUMN consent_version TEXT")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_manual_state(account_id TEXT NOT NULL,subscription_id TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,subscription_id))")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_manual_audit(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,subscription_id TEXT NOT NULL,event TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (4)")
            if "processing_consent" not in {r[1] for r in db.execute("PRAGMA table_info(chat_preferences)")}:
                db.execute("ALTER TABLE chat_preferences ADD COLUMN processing_consent INTEGER")
                db.execute("UPDATE chat_preferences SET processing_consent=CASE WHEN enabled=1 AND consent_version='3.4-context' THEN 1 ELSE 0 END")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (5)")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_manual_snapshots(account_id TEXT NOT NULL,subscription_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(account_id,subscription_id))")
            db.execute("INSERT OR IGNORE INTO batch3_schema_versions VALUES (6)")

    def preview(self, account, kind, payload, conversation_id=None):
        id = str(uuid.uuid4())
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if conversation_id is not None and not db.execute("SELECT 1 FROM chat_conversations WHERE id=? AND account_id=? AND active=1",(conversation_id,account)).fetchone():
                raise ValueError("Conversation changed; no approval was created")
            db.execute("INSERT INTO action_previews(id,account_id,kind,payload,status,expires,result,conversation_id) VALUES (?,?,?,?,?,?,NULL,?)", (id, account, kind, json.dumps(payload), "pending", time.time() + 900,conversation_id))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,'requested',?)", (id, json.dumps({"kind": kind, "account_id": account})))
        return self.get(account, id)

    def get(self, account, id):
        with closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM action_previews WHERE id=? AND account_id=?", (id, account)).fetchone()
        if row is None:
            raise LookupError("Preview not found in this account")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def claim(self, account, id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE action_previews SET status='running' WHERE id=? AND account_id=? AND status='pending' AND expires>?", (id, account, time.time()))
            if cursor.rowcount:
                db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,'approved','Explicit user confirmation; single-use claim')", (id,))
            return bool(cursor.rowcount)

    def finish(self, account, id, status, result):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE action_previews SET status=?,result=? WHERE id=? AND account_id=?", (status, json.dumps(result), id, account))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,?,?)", (id, status, json.dumps(result)))

    def event(self, id, event, detail):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,?,?)", (id, event, json.dumps(detail)))

    def chat(self, account, role, content):
        conversation=self.current_conversation(account)
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO chat_messages(account_id,role,content,conversation_id) VALUES (?,?,?,?)", (account, role, json.dumps(content),conversation))

    def history(self, account):
        conversation=self.current_conversation(account)
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id,role,content,created_at FROM chat_messages WHERE account_id=? AND conversation_id=? ORDER BY id DESC LIMIT 100", (account,conversation,)).fetchall()
        return [{"id": r[0], "role": r[1], "content": json.loads(r[2]), "created_at":r[3]} for r in reversed(rows)]

    def list(self, account):
        with closing(self.connect()) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM action_previews WHERE account_id=? ORDER BY rowid DESC LIMIT 50", (account,))]
        return [self.get(account, id) for id in ids]

    def audit(self, account, id):
        self.get(account, id)
        with closing(self.connect()) as db:
            rows = db.execute("SELECT event,detail,created_at FROM action_audit WHERE action_id=? ORDER BY id LIMIT 100", (id,)).fetchall()
        return [{"event": row[0], "detail": row[1], "created_at": row[2]} for row in rows]

    def subscription_states(self, account):
        with closing(self.connect()) as db:
            return {row[0]: {"status":row[1],"action_id":row[2],"updated_at":row[3]} for row in db.execute("SELECT subscription_id,status,action_id,updated_at FROM subscription_request_state WHERE account_id=?", (account,))}

    def start_subscription(self, account, action_id, subscription_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute("SELECT 1 FROM action_previews WHERE account_id=? AND id=? AND status='running'", (account,action_id)).fetchone()
            if not owned:
                raise LookupError("Running approval not found in this account")
            if db.execute("SELECT 1 FROM subscription_request_state WHERE account_id=? AND subscription_id=?", (account,subscription_id)).fetchone():
                return False
            db.execute("INSERT INTO subscription_request_state(account_id,subscription_id,action_id,status) VALUES (?,?,?,'running')", (account,subscription_id,action_id))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,'unsubscribe_started',?)", (action_id,json.dumps({"subscription_id":subscription_id})))
            return True

    def finish_subscriptions(self, account, id, status, result):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for item in result["items"]:
                if item["status"] in ("requested","uncertain"):
                    db.execute("UPDATE subscription_request_state SET status=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND action_id=? AND subscription_id=?", (item["status"],account,id,item["subscription_id"]))
            db.execute("UPDATE action_previews SET status=?,result=? WHERE id=? AND account_id=?", (status,json.dumps(result),id,account))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,?,?)", (id,status,json.dumps(result)))

    @staticmethod
    def _conversation(db, account):
        row=db.execute("SELECT id FROM chat_conversations WHERE account_id=? AND active=1",(account,)).fetchone()
        if row: return row[0]
        id=str(uuid.uuid4())
        db.execute("INSERT INTO chat_conversations(id,account_id,active) VALUES (?,?,1)",(id,account))
        return id

    def current_conversation(self, account):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return self._conversation(db,account)

    def preferences(self, account):
        with closing(self.connect()) as db:
            row=db.execute("SELECT enabled,developer_local,save_history,consent_version,processing_consent FROM chat_preferences WHERE account_id=?",(account,)).fetchone()
        return {"enabled":bool(row[0]) and row[3]=="3.4-context" if row else False,"developer_local":bool(row[1]) if row else False,"save_history":bool(row[2]) if row else True,"model_enabled":bool(row[0]) if row else False,"processing_consent":bool(row[4]) and row[3]=="3.4-context" if row else False}

    def save_preferences(self, account, enabled, developer_local, processing_consent=None):
        if processing_consent is None: processing_consent=enabled
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO chat_preferences(account_id,enabled,developer_local,consent_version) VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,developer_local=excluded.developer_local,consent_version=excluded.consent_version",(account,int(enabled),int(developer_local),"3.4-context" if processing_consent else None))
            db.execute("UPDATE chat_preferences SET processing_consent=? WHERE account_id=?",(int(processing_consent),account))

    def sessions(self, account):
        self.current_conversation(account)
        with closing(self.connect()) as db:
            return [{"id":r[0],"active":bool(r[1]),"created_at":r[2],"message_count":r[3]} for r in db.execute("SELECT c.id,c.active,c.created_at,COUNT(m.id) FROM chat_conversations c LEFT JOIN chat_messages m ON m.conversation_id=c.id AND m.account_id=c.account_id WHERE c.account_id=? GROUP BY c.id ORDER BY c.rowid DESC LIMIT 100",(account,))]

    def change_conversation(self, account, operation, target=None):
        if operation not in ("new","clear","clear_all","select"): raise ValueError("Invalid conversation action")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current=self._conversation(db,account)
            db.execute("UPDATE chat_requests SET status='interrupted',result=? WHERE account_id=? AND status='running' AND expires<=?",(json.dumps({"text":"Interrupted request; no automatic retry. Inspect history and action results.","results":[],"processor":"local"}),account,time.time()))
            if db.execute("SELECT 1 FROM chat_requests WHERE account_id=? AND status='running'",(account,)).fetchone() or db.execute("SELECT 1 FROM action_previews WHERE account_id=? AND conversation_id IS NOT NULL AND status='running'",(account,)).fetchone():
                raise ValueError("A chat request or approved action is running. Wait or inspect its result before changing conversations.")
            if operation=="select" and not db.execute("SELECT 1 FROM chat_conversations WHERE account_id=? AND id=?",(account,target)).fetchone():
                raise LookupError("Conversation not found in this account")
            where="account_id=?" if operation=="clear_all" else "account_id=? AND conversation_id=?"
            params=(account,) if operation=="clear_all" else (account,current)
            # Cancel before removing history, so saved approval URLs cannot execute.
            db.execute("INSERT INTO action_audit(action_id,event,detail) SELECT id,'cancelled','Conversation changed; pending approval revoked' FROM action_previews WHERE "+where+" AND conversation_id IS NOT NULL AND status='pending'",params)
            db.execute("UPDATE action_previews SET status='cancelled' WHERE "+where+" AND conversation_id IS NOT NULL AND status='pending'",params)
            if operation in ("clear","clear_all"):
                ids=[r[0] for r in db.execute("SELECT id FROM action_previews WHERE "+where+" AND status='cancelled'",params)]
                for id in ids:
                    db.execute("DELETE FROM action_audit WHERE action_id=?",(id,))
                    db.execute("DELETE FROM action_previews WHERE id=?",(id,))
                db.execute("UPDATE action_previews SET conversation_id=NULL WHERE "+where,params)
                db.execute("DELETE FROM chat_messages WHERE "+where,params)
                db.execute("DELETE FROM chat_requests WHERE "+where,params)
                db.execute("DELETE FROM chat_plan_cache WHERE account_id=?",(account,))
                if operation=="clear_all": db.execute("DELETE FROM chat_conversations WHERE account_id=?",(account,))
                else: db.execute("DELETE FROM chat_conversations WHERE account_id=? AND id=?",(account,current))
            db.execute("UPDATE chat_conversations SET active=0 WHERE account_id=?",(account,))
            if operation=="select":
                db.execute("UPDATE chat_conversations SET active=1 WHERE account_id=? AND id=?",(account,target))
            else:
                self._conversation(db,account)
        return self.sessions(account)

    def begin_request(self, account, request_id, fingerprint):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT fingerprint,status,result FROM chat_requests WHERE account_id=? AND request_id=?",(account,request_id)).fetchone()
            if row:
                if row[0]!=fingerprint: raise ValueError("Request ID already belongs to different input")
                if row[1]=='running': raise ValueError("Identical request is still running; do not resubmit")
                return json.loads(row[2])
            if db.execute("SELECT 1 FROM chat_requests WHERE account_id=? AND status='running'",(account,)).fetchone():
                raise ValueError("Another request is running for this account")
            db.execute("INSERT INTO chat_requests(account_id,request_id,conversation_id,fingerprint,status,result,expires) VALUES (?,?,?,?, 'running',NULL,?)",(account,request_id,self._conversation(db,account),fingerprint,time.time()+120))
            return None

    def finish_request(self, account, request_id, result):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE chat_requests SET status='finished',result=? WHERE account_id=? AND request_id=?",(json.dumps(result),account,request_id))

    def cached_plan(self, account, fingerprint):
        with closing(self.connect()) as db:
            row=db.execute("SELECT plan FROM chat_plan_cache WHERE account_id=? AND fingerprint=? AND expires>?",(account,fingerprint,time.time())).fetchone()
            return json.loads(row[0]) if row else None

    def save_plan(self, account, fingerprint, plan):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO chat_plan_cache VALUES (?,?,?,?) ON CONFLICT(account_id,fingerprint) DO UPDATE SET plan=excluded.plan,expires=excluded.expires",(account,fingerprint,json.dumps(plan),time.time()+900))

    def verified_subscriptions(self, account):
        verified={}
        for preview in self.list(account):
            if preview["kind"]!="unsubscribe:one_click" or preview["expires"]<=time.time(): continue
            for item in preview["payload"]["items"]:
                if item.get("eligible") and item.get("proof"):
                    verified.setdefault(item["subscription_id"],item["proof"])
        return verified

    def request_active(self, account, request_id):
        with closing(self.connect()) as db:
            return bool(db.execute("SELECT 1 FROM chat_requests r JOIN chat_conversations c ON c.account_id=r.account_id AND c.id=r.conversation_id WHERE r.account_id=? AND r.request_id=? AND r.status='running' AND r.expires>? AND c.active=1",(account,request_id,time.time())).fetchone())

    def connect(self):
        if not self.memory: return sqlite3.connect(self.path)
        # Shared-cache in-memory SQLite returns SQLITE_LOCKED rather than honoring
        # busy_timeout for concurrent table transactions. Serialize per session.
        self._lock.acquire()
        try:
            connection=sqlite3.connect(self.path,uri=True,check_same_thread=False,factory=_LockedMemoryConnection)
            connection._store_lock=self._lock
            return connection
        except Exception:
            self._lock.release()
            raise

    def close(self):
        with self._lock:
            if self._keeper is not None:
                self._keeper.close()
                self._keeper=None

    def save_history_preference(self, account, enabled):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO chat_preferences(account_id,enabled,developer_local,save_history) VALUES (?,0,0,?) ON CONFLICT(account_id) DO UPDATE SET save_history=excluded.save_history",(account,int(enabled)))

    def manual_states(self, account):
        with closing(self.connect()) as db:
            return {r[0]:{"status":r[1],"note":r[2],"updated_at":r[3]} for r in db.execute("SELECT subscription_id,status,note,updated_at FROM subscription_manual_state WHERE account_id=?",(account,))}

    def mark_manual(self, account, subscription, note):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO subscription_manual_state(account_id,subscription_id,status,note) VALUES (?,?,'handled_locally',?) ON CONFLICT(account_id,subscription_id) DO UPDATE SET status='handled_locally',note=excluded.note,updated_at=CURRENT_TIMESTAMP WHERE subscription_manual_state.status!='manual_completed'",(account,subscription,note))
            db.execute("INSERT INTO subscription_manual_audit(account_id,subscription_id,event,note) VALUES (?,?,'handled_locally',?)",(account,subscription,note))
        return self.manual_states(account)[subscription]

    def complete_manual(self, account, group, note):
        subscription=group['id']
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT status FROM subscription_manual_state WHERE account_id=? AND subscription_id=?',(account,subscription)).fetchone()
            if prior and prior[0]=='manual_completed':return self.manual_states(account)[subscription]
            db.execute("INSERT INTO subscription_manual_state(account_id,subscription_id,status,note) VALUES (?,?,'manual_completed',?) ON CONFLICT(account_id,subscription_id) DO UPDATE SET status='manual_completed',note=excluded.note,updated_at=CURRENT_TIMESTAMP",(account,subscription,note))
            snapshot={k:group.get(k) for k in ('id','identity','display_name','kind','message_ids','latest_message_ms')}
            snapshot.update(candidate_message_ids=[],one_click_candidate=False,methods=[],manual_destinations=[],automatic_available=False,verification='manual_report',state='saved_manual',reason='Manual unsubscribe reported complete by the user',observed_count=group.get('observed_count',0))
            db.execute('INSERT INTO subscription_manual_snapshots VALUES (?,?,?) ON CONFLICT(account_id,subscription_id) DO UPDATE SET payload=excluded.payload',(account,subscription,json.dumps(snapshot)))
            db.execute("INSERT INTO subscription_manual_audit(account_id,subscription_id,event,note) VALUES (?,?,'manual_completed',?)",(account,subscription,note))
        return self.manual_states(account)[subscription]

    def manual_snapshots(self, account):
        with closing(self.connect()) as db:
            return {id:json.loads(raw) for id,raw in db.execute('SELECT subscription_id,payload FROM subscription_manual_snapshots WHERE account_id=?',(account,))}

    def manual_event(self,account,subscription,event,note):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO subscription_manual_audit(account_id,subscription_id,event,note) VALUES (?,?,?,?)",(account,subscription,event,note))

    def manual_audit(self,account,subscription):
        with closing(self.connect()) as db:
            return [{"event":r[0],"note":r[1],"created_at":r[2]} for r in db.execute("SELECT event,note,created_at FROM subscription_manual_audit WHERE account_id=? AND subscription_id=? ORDER BY id DESC LIMIT 50",(account,subscription))]

    def archive_remote(self, value, audit):
        # Temporary chat text and pending previews never enter the durable database.
        if not value["kind"].startswith("gmail:") or value["status"] in ("pending","cancelled"): return
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO action_previews(id,account_id,kind,payload,status,expires,result,conversation_id) VALUES (?,?,?,?,?,?,?,NULL)",(value["id"],value["account_id"],value["kind"],json.dumps(value["payload"]),value["status"],value["expires"],json.dumps(value["result"])))
            db.execute("DELETE FROM action_audit WHERE action_id=?",(value["id"],))
            for event in audit:
                db.execute("INSERT INTO action_audit(action_id,event,detail,created_at) VALUES (?,?,?,?)",(value["id"],event["event"],event["detail"],event["created_at"]))

    def subscription_checks(self,account):
        checks={}
        for preview in self.list(account):
            if preview["kind"]!="unsubscribe:one_click" or preview["expires"]<=time.time(): continue
            for item in preview["payload"]["items"]: checks.setdefault(item["subscription_id"],item)
        return checks

    def reject(self,account,id):
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT status FROM action_previews WHERE account_id=? AND id=?",(account,id)).fetchone()
            if row is None:raise LookupError("Preview not found in this account")
            if row[0]=='running':raise ValueError("Approved action already running; inspect its result")
            if row[0]=='pending':
                db.execute("UPDATE action_previews SET status='rejected' WHERE account_id=? AND id=?",(account,id))
                db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,'rejected','Explicit user rejection; no action executed')",(id,))
        return self.get(account,id)

    def subscription_history(self,account,subscription):
        result=self.manual_audit(account,subscription)
        with closing(self.connect()) as db:
            rows=db.execute("SELECT p.id,p.status,p.payload,p.result,(SELECT MIN(created_at) FROM action_audit WHERE action_id=p.id) FROM action_previews p WHERE p.account_id=? AND p.kind='unsubscribe:one_click' AND EXISTS(SELECT 1 FROM json_each(p.payload,'$.items') i WHERE json_extract(i.value,'$.subscription_id')=?) ORDER BY p.rowid DESC LIMIT 50",(account,subscription)).fetchall()
        for id,status,payload,outcome,created in rows:
            item=next(i for i in json.loads(payload)["items"] if i["subscription_id"]==subscription)
            saved=json.loads(outcome) if outcome else None
            if saved:saved={"items":[row for row in saved.get("items",[]) if row.get("subscription_id")==subscription]}
            result.append({"event":"automatic_"+status,"note":item.get("reason","Eligibility reviewed"),"created_at":created or "","action_id":id,"result":saved})
        with closing(self.connect()) as db:
            rows=db.execute("SELECT id,status,result FROM action_previews WHERE account_id=? AND kind='unsubscribe:browser' AND json_extract(payload,'$.subscription_id')=? ORDER BY rowid DESC LIMIT 50",(account,subscription)).fetchall()
        for id,status,outcome in rows:
            result.append({"action_id":id,"event":"website_"+status,"result":json.loads(outcome) if outcome else None,"audit":self.audit(account,id)})
        return result

    def cancel_chat_approvals(self,account):
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO action_audit(action_id,event,detail) SELECT id,'cancelled','Account changed; pending chat approval expired' FROM action_previews WHERE account_id=? AND conversation_id IS NOT NULL AND status='pending'",(account,))
            count=db.execute("UPDATE action_previews SET status='cancelled' WHERE account_id=? AND conversation_id IS NOT NULL AND status='pending'",(account,)).rowcount
        return {"cancelled":count}

    def resume_browser_subscription(self,account,id,subscription,parent):
        with closing(self.connect()) as db,db:
            cursor=db.execute("UPDATE subscription_request_state SET action_id=?,status='running',updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND subscription_id=? AND action_id=? AND status='needs_action'",(id,account,subscription,parent))
            return bool(cursor.rowcount)

    def finish_browser(self,account,id,subscription,result):
        with closing(self.connect()) as db,db:
            db.execute("UPDATE subscription_request_state SET status=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND subscription_id=? AND action_id=?",(result['state'],account,subscription,id))
            db.execute("UPDATE action_previews SET status=?,result=? WHERE account_id=? AND id=?",('succeeded' if result['state'] in ('cancelled','requested') else result['state'],json.dumps(result),account,id))
            db.execute("INSERT INTO action_audit(action_id,event,detail) VALUES (?,?,?)",(id,'browser_'+result['state'],json.dumps(result)))
