"""Shared UTC-day reservations; failures consume requests, never a money guarantee."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta

DAILY_CALL_LIMIT = 3
FREE_AUTOMATION_PROVIDERS = frozenset({'gemini','groq','mistral','zai'})


class SQLiteAIQuota:
    def __init__(self, path, *, provider_managed_chat=False):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS ai_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, day_utc TEXT NOT NULL,
                account_id TEXT NOT NULL, message_id TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("CREATE TABLE IF NOT EXISTS ai_budget_settings(id INTEGER PRIMARY KEY CHECK(id=1),daily_limit INTEGER NOT NULL CHECK(daily_limit BETWEEN 0 AND 100))")
            db.execute("CREATE TABLE IF NOT EXISTS ai_schema_versions(version INTEGER PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO ai_schema_versions VALUES (1)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_provider_policy(id INTEGER PRIMARY KEY CHECK(id=1),free_tier_attested INTEGER NOT NULL DEFAULT 0,verified_rpd INTEGER,paid_approved INTEGER NOT NULL DEFAULT 0)")
            if "model" not in {r[1] for r in db.execute("PRAGMA table_info(ai_provider_policy)")}:
                db.execute("ALTER TABLE ai_provider_policy ADD COLUMN model TEXT")
            db.execute("INSERT OR IGNORE INTO ai_schema_versions VALUES (2)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_runtime_settings(id INTEGER PRIMARY KEY CHECK(id=1),model TEXT,connection TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_configuration_audit(id INTEGER PRIMARY KEY,event TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_connection_tests(account_id TEXT NOT NULL,request_id TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,result TEXT,PRIMARY KEY(account_id,request_id))")
            if "policy_recheck" not in {r[1] for r in db.execute("PRAGMA table_info(ai_runtime_settings)")}:
                db.execute("ALTER TABLE ai_runtime_settings ADD COLUMN policy_recheck INTEGER NOT NULL DEFAULT 0")
            db.execute("INSERT OR IGNORE INTO ai_schema_versions VALUES (3)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_chat_policy(id INTEGER PRIMARY KEY CHECK(id=1),provider_managed INTEGER NOT NULL,safety_ceiling INTEGER)")
            if provider_managed_chat:db.execute("INSERT OR IGNORE INTO ai_chat_policy VALUES (1,1,NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_provider_usage(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,model TEXT NOT NULL,metrics TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("INSERT OR IGNORE INTO ai_schema_versions VALUES (4)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_verified_models(model TEXT PRIMARY KEY,source TEXT NOT NULL,verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS ai_model_checks(account_id TEXT NOT NULL,request_id TEXT NOT NULL,result TEXT,PRIMARY KEY(account_id,request_id))")
            db.execute("INSERT OR IGNORE INTO ai_schema_versions VALUES (5)")
            db.execute('CREATE TABLE IF NOT EXISTS ai_free_automation(provider TEXT PRIMARY KEY,enabled INTEGER NOT NULL,reference TEXT NOT NULL)')
            if 'guard_exempt' not in {r[1] for r in db.execute('PRAGMA table_info(ai_attempts)')}:
                db.execute('ALTER TABLE ai_attempts ADD COLUMN guard_exempt INTEGER NOT NULL DEFAULT 0')
            if not db.execute('SELECT 1 FROM ai_schema_versions WHERE version=6').fetchone():
                # Backfill only explicit standing exemptions already in force at
                # reservation time; preserve every request and its original time.
                db.execute("""UPDATE ai_attempts SET guard_exempt=1 WHERE message_id LIKE 'hybrid:%' AND EXISTS(
                    SELECT 1 FROM ai_configuration_audit e,json_each(e.detail,'$.providers') p
                    WHERE e.event='free_automation_authorized' AND p.value=substr(ai_attempts.message_id,8)
                    AND julianday(ai_attempts.created_at)>=julianday(e.created_at))""")
                db.execute('INSERT INTO ai_schema_versions VALUES(6)')
            import json
            row=db.execute("SELECT connection FROM ai_runtime_settings WHERE id=1").fetchone()
            if row and row[0]:
                saved=json.loads(row[0])
                if saved.get('state')=='connected' and saved.get('model'):db.execute("INSERT OR IGNORE INTO ai_verified_models(model,source,verified_at) VALUES (?,?,?)",(saved['model'],saved.get('source','saved_connection'),saved.get('tested_at') or '0001-01-01T00:00:00+00:00'))


            for (raw,) in db.execute("SELECT result FROM ai_connection_tests WHERE status='finished' AND result IS NOT NULL ORDER BY rowid DESC LIMIT 50").fetchall():
                saved=json.loads(raw)
                if saved.get('state')=='connected' and saved.get('request_count') and saved.get('model'):
                    db.execute("INSERT OR IGNORE INTO ai_verified_models(model,source,verified_at) VALUES (?,'connection_test',?)",(saved['model'],saved.get('tested_at') or '0001-01-01T00:00:00+00:00'))

    def status(self):
        now=datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute("SELECT daily_limit FROM ai_budget_settings WHERE id=1").fetchone()
            limit=row[0] if row else DAILY_CALL_LIMIT
            used,total=db.execute("SELECT COALESCE(SUM(guard_exempt=0),0),COUNT(*) FROM ai_attempts WHERE day_utc=?",(now.date().isoformat(),)).fetchone()
            chat=db.execute("SELECT provider_managed,safety_ceiling FROM ai_chat_policy WHERE id=1").fetchone()
            recent=db.execute("SELECT COUNT(*) FROM ai_attempts WHERE created_at>=?",((now-timedelta(seconds=60)).isoformat(),)).fetchone()[0]
            chat_used=db.execute("SELECT COUNT(*) FROM ai_attempts WHERE day_utc=? AND (message_id LIKE 'chat-%' OR message_id='connection-test')",(now.date().isoformat(),)).fetchone()[0]
            policy=db.execute("SELECT free_tier_attested,verified_rpd,paid_approved,model FROM ai_provider_policy WHERE id=1").fetchone()
        reset=datetime.combine(now.date()+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
        return {"chat_safety_ceiling_reached":bool(chat and chat[1] is not None and chat_used>=chat[1]),"provider_managed_chat":bool(chat and chat[0]),"chat_safety_ceiling":chat[1] if chat else None,"short_term_throttled":bool(chat and chat[0] and recent>=10),"limit_source":"postmark_application","provider_policy":{"free_tier_attested":bool(policy[0]) if policy else False,"verified_rpd":policy[1] if policy else None,"paid_approved":bool(policy[2]) if policy else False,"model":policy[3] if policy else None,"verification":"User attestation, not independently queried from Google"},"used":used,"total_requests":total,"exempt_requests":total-used,"limit":limit,"remaining":max(0,limit-used),"resets_at":reset.isoformat(),"scope":"Shared finite-request guard across accounts/providers; previously authorized standing Free-only automation is counted separately","cost_note":"Request ceiling, not a monetary budget. Higher limits can increase charges."}

    def attest_provider(self, free, rpd, paid=False, model="gemini-3.5-flash-lite"):
        if type(free) is not bool or type(paid) is not bool or (free and (type(rpd) is not int or not 1<=rpd<=100000)):
            raise ValueError("Confirm a free-tier project and its verified model RPD, or separately approve paid usage")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO ai_provider_policy(id,free_tier_attested,verified_rpd,paid_approved,model) VALUES (1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET free_tier_attested=excluded.free_tier_attested,verified_rpd=excluded.verified_rpd,paid_approved=excluded.paid_approved,model=excluded.model",(int(free),rpd if free else None,int(paid),model))
            db.execute("UPDATE ai_runtime_settings SET policy_recheck=0 WHERE id=1")
        self.configuration_event("provider_policy_saved",{"model":model,"free_tier_attested":free,"verified_rpd":rpd if free else None,"paid_approved":paid,"source":"explicit_configuration"})

    def configure(self, limit):
        if type(limit) is not int or not 0<=limit<=100:
            raise ValueError("Daily request limit must be 0–100")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO ai_budget_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET daily_limit=excluded.daily_limit",(limit,))

    def reserve(self, account, purpose):
        now=datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT daily_limit FROM ai_budget_settings WHERE id=1").fetchone()
            limit=row[0] if row else DAILY_CALL_LIMIT
            used,total=db.execute("SELECT COALESCE(SUM(guard_exempt=0),0),COUNT(*) FROM ai_attempts WHERE day_utc=?",(now.date().isoformat(),)).fetchone()
            provider=purpose.split(':',1)[1] if purpose.startswith('hybrid:') else 'gemini'
            # Explicit Free-only hybrid automation has no arbitrary cumulative
            # app request ceiling. The caller still checks verified zero cost,
            # per-account access, finite synthetic scope, and provider circuits.
            free_automation=purpose.startswith('hybrid:') and bool(db.execute('SELECT 1 FROM ai_free_automation WHERE provider=? AND enabled=1',(provider,)).fetchone())
            policy=db.execute("SELECT provider_managed,safety_ceiling FROM ai_chat_policy WHERE id=1").fetchone()
            chat=purpose.startswith("chat-") or purpose=="connection-test"
            if chat and policy and policy[0]:
                recent=db.execute("SELECT COUNT(*) FROM ai_attempts WHERE created_at>=?",((now-timedelta(seconds=60)).isoformat(),)).fetchone()[0]
                if recent>=10:return False
                if policy[1] is not None:
                    chat_used=db.execute("SELECT COUNT(*) FROM ai_attempts WHERE day_utc=? AND (message_id LIKE 'chat-%' OR message_id='connection-test')",(now.date().isoformat(),)).fetchone()[0]
                    if chat_used>=policy[1]:return False
            elif used>=limit and not free_automation:return False
            db.execute("INSERT INTO ai_attempts(day_utc,account_id,message_id,created_at,guard_exempt) VALUES (?,?,?,?,?)",(now.date().isoformat(),account,purpose,now.isoformat(),int(free_automation)))
        return True

    def authorize_free_automation(self,providers,reference):
        if not reference or not set(providers)<=FREE_AUTOMATION_PROVIDERS:raise ValueError('Explicit verified Free-only provider policy required')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executemany('INSERT OR REPLACE INTO ai_free_automation VALUES(?,1,?)',[(p,reference) for p in providers])
        self.configuration_event('free_automation_authorized',dict(providers=providers,reference=reference,paid=False,provider_quotas_retained=True))

    def free_automation(self,provider):
        with closing(sqlite3.connect(self.path)) as db:return bool(db.execute('SELECT 1 FROM ai_free_automation WHERE provider=? AND enabled=1',(provider,)).fetchone())

    def runtime(self):
        import json
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute("SELECT model,connection,policy_recheck FROM ai_runtime_settings WHERE id=1").fetchone()
        return {"model":row[0],"connection":json.loads(row[1]) if row[1] else None,"policy_recheck":bool(row[2])} if row else {"model":None,"connection":None,"policy_recheck":False}

    def select_model(self,model):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_runtime_settings(id,model,policy_recheck) VALUES (1,?,1) ON CONFLICT(id) DO UPDATE SET model=excluded.model,connection=NULL,policy_recheck=1",(model,))
        self.configuration_event("model_selected",{"model":model,"policy_recheck_required":True})

    def configuration_event(self,event,detail):
        import json
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_configuration_audit(event,detail) VALUES (?,?)",(event,json.dumps(detail)))

    def begin_test(self,account,request_id,model):
        import json
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT model,status,result FROM ai_connection_tests WHERE account_id=? AND request_id=?",(account,request_id)).fetchone()
            if row:
                if row[0]!=model:raise ValueError("Test identifier belongs to another model")
                if row[1]=='running':raise ValueError("This test is already running; no extra request was sent")
                return json.loads(row[2])
            db.execute("INSERT INTO ai_connection_tests(account_id,request_id,model,status) VALUES (?,?,?,'running')",(account,request_id,model))
        return None

    def finish_test(self,account,request_id,result):
        import json
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE ai_connection_tests SET status='finished',result=? WHERE account_id=? AND request_id=?",(json.dumps(result),account,request_id))
            if result.get("request_count"):
                db.execute("INSERT INTO ai_runtime_settings(id,connection) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET connection=excluded.connection",(json.dumps(result),))
        if result.get("state")=="connected":self.mark_verified(result["model"],"connection_test")
        self.configuration_event("connection_test",{k:result[k] for k in ("state","model","request_count")})

    def record_connection(self,result):
        import json
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_runtime_settings(id,connection) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET connection=excluded.connection",(json.dumps(result),))

    def mark_verified(self,model,source):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_verified_models(model,source,verified_at) VALUES (?,?,?) ON CONFLICT(model) DO UPDATE SET source=CASE WHEN ai_verified_models.source='chat_request' THEN ai_verified_models.source ELSE excluded.source END,verified_at=CASE WHEN ai_verified_models.source='chat_request' AND excluded.source<>'chat_request' THEN ai_verified_models.verified_at ELSE excluded.verified_at END",(model,source,datetime.now(timezone.utc).isoformat()))

    def last_verified(self):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute("SELECT model,source,verified_at FROM ai_verified_models ORDER BY (source='chat_request') DESC,verified_at DESC,rowid DESC LIMIT 1").fetchone()
        return {'model':row[0],'source':row[1],'verified_at':row[2]} if row else None

    def model_check(self,account,request_id,result=None):
        import json
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("BEGIN IMMEDIATE")
            if result is not None:db.execute("UPDATE ai_model_checks SET result=? WHERE account_id=? AND request_id=?",(json.dumps(result),account,request_id));return
            row=db.execute("SELECT result FROM ai_model_checks WHERE account_id=? AND request_id=?",(account,request_id)).fetchone()
            if row:
                if row[0] is None:raise ValueError("Model check already in progress; no duplicate request")
                return json.loads(row[0])
            db.execute("INSERT INTO ai_model_checks VALUES (?,?,NULL)",(account,request_id))
        return None

    def set_chat_ceiling(self,limit,approved):
        if approved is not True or limit is not None and (type(limit) is not int or not 1<=limit<=10000):raise ValueError("Explicitly choose an optional internal ceiling")
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_chat_policy VALUES (1,1,?) ON CONFLICT(id) DO UPDATE SET provider_managed=1,safety_ceiling=excluded.safety_ceiling",(limit,))
        self.configuration_event("chat_guard_changed",{"safety_ceiling":limit,"explicitly_approved":True})

    def set_simple_policy(self,free,paid,model):
        if type(free) is not bool or type(paid) is not bool or free==paid:raise ValueError("Choose exactly one billing policy")
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_provider_policy(id,free_tier_attested,verified_rpd,paid_approved,model) VALUES (1,?,NULL,?,?) ON CONFLICT(id) DO UPDATE SET free_tier_attested=excluded.free_tier_attested,paid_approved=excluded.paid_approved,model=excluded.model",(int(free),int(paid),model))
            db.execute("INSERT OR IGNORE INTO ai_chat_policy VALUES (1,1,NULL)")
            db.execute("UPDATE ai_runtime_settings SET policy_recheck=0 WHERE id=1")
        self.configuration_event("simple_provider_policy",{"plan":"free_only" if free else "paid","model":model,"paid_approval_revoked":free})

    def retarget_policy(self,model):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE ai_provider_policy SET model=? WHERE id=1",(model,))
            db.execute("UPDATE ai_runtime_settings SET policy_recheck=0 WHERE id=1")

    def record_provider_usage(self,account,model,metrics):
        import json
        safe={k:v for k,v in metrics.items() if k in ('prompt_tokens','output_tokens','thinking_tokens','total_tokens','cached_tokens') and type(v) is int and v>=0}
        if not safe:return
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO ai_provider_usage(account_id,model,metrics) VALUES (?,?,?)",(account,model,json.dumps(safe)))

    def provider_usage(self,account):
        import json
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute("SELECT model,metrics,created_at FROM ai_provider_usage WHERE account_id=? ORDER BY id DESC LIMIT 10",(account,)).fetchall()
        return [{"model":r[0],"metrics":json.loads(r[1]),"created_at":r[2]} for r in rows]


def reserve_call(path, account_id, message_id):
    return SQLiteAIQuota(path).reserve(account_id,message_id)
