"""Hybrid registry, immutable budgets, circuits and recoverable task evidence."""
from contextlib import contextmanager,closing
from dataclasses import asdict,replace
import json
import os
from pathlib import Path
import sqlite3
import time
from email_agent.application.hybrid_ai import ModelCapability,InferenceResult,PROVIDERS,ACTIVE_PROVIDERS
from email_agent.application.semantic_mail import SEMANTIC_VERSION


from email_agent.infrastructure.database.provider_authorization import ProviderAuthorization


class SQLiteHybridStore(ProviderAuthorization):
    def __init__(self,path,shared_quota=None,resource_admission=lambda:True):self.path=Path(path);self.shared_quota=shared_quota;self.resource_admission=resource_admission
    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path,timeout=20)) as db:
            db.row_factory=sqlite3.Row
            with db:yield db
    def initialize(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS hybrid_versions(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO hybrid_versions VALUES(1);
            INSERT OR IGNORE INTO hybrid_versions VALUES(2);
            INSERT OR IGNORE INTO hybrid_versions VALUES(3);
            INSERT OR IGNORE INTO hybrid_versions VALUES(9);
            CREATE TABLE IF NOT EXISTS hybrid_group_runs(account_id TEXT,task_key TEXT,status TEXT,lease REAL NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,result TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,task_key));
            CREATE TABLE IF NOT EXISTS hybrid_scan_cursors(account_id TEXT,provider TEXT,position INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account_id,provider));
            CREATE TABLE IF NOT EXISTS hybrid_preferences(account_id TEXT PRIMARY KEY,preference TEXT NOT NULL DEFAULT 'auto');
            CREATE TABLE IF NOT EXISTS hybrid_providers(provider TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,endpoint TEXT,trust_lan INTEGER NOT NULL DEFAULT 0,last_state TEXT,last_checked REAL);
            CREATE TABLE IF NOT EXISTS hybrid_models(provider TEXT,model TEXT,payload TEXT NOT NULL,free_until REAL NOT NULL DEFAULT 0,validated_at REAL,PRIMARY KEY(provider,model));
            CREATE TABLE IF NOT EXISTS hybrid_health(provider TEXT,model TEXT,failures INTEGER NOT NULL DEFAULT 0,until REAL NOT NULL DEFAULT 0,disabled INTEGER NOT NULL DEFAULT 0,last_error TEXT,PRIMARY KEY(provider,model));
            CREATE TABLE IF NOT EXISTS hybrid_grants(id INTEGER PRIMARY KEY,account_id TEXT,provider TEXT,models TEXT,consent TEXT NOT NULL,request_cap INTEGER NOT NULL,input_cap INTEGER NOT NULL,output_cap INTEGER NOT NULL,message_cap INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS hybrid_grant_scopes(scope_id TEXT PRIMARY KEY,grant_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS hybrid_attempts(id INTEGER PRIMARY KEY,account_id TEXT,provider TEXT,model TEXT,task_key TEXT,grant_id INTEGER,input_bound INTEGER,output_bound INTEGER,status TEXT NOT NULL DEFAULT 'running',usage TEXT,seconds REAL,error TEXT,created_at REAL,UNIQUE(account_id,provider,model,task_key));
            CREATE TABLE IF NOT EXISTS hybrid_attempt_messages(attempt_id INTEGER NOT NULL,account_id TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(attempt_id,message_id));
            CREATE TABLE IF NOT EXISTS hybrid_responses(attempt_id INTEGER PRIMARY KEY,response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hybrid_credential_revisions(provider TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS hybrid_account_providers(account_id TEXT,provider TEXT,enabled INTEGER NOT NULL,legacy_allowed INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(account_id,provider));
            CREATE TABLE IF NOT EXISTS hybrid_grant_messages(grant_id INTEGER,message_id TEXT,PRIMARY KEY(grant_id,message_id));
            CREATE TABLE IF NOT EXISTS hybrid_tasks(account_id TEXT,message_id TEXT,content_key TEXT,status TEXT,reason TEXT,provider TEXT,model TEXT,due REAL NOT NULL DEFAULT 0,lease REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(account_id,message_id));
            CREATE INDEX IF NOT EXISTS hybrid_task_due ON hybrid_tasks(account_id,status,due);
            CREATE TABLE IF NOT EXISTS hybrid_events(id INTEGER PRIMARY KEY,account_id TEXT,message_id TEXT,event TEXT,provider TEXT,model TEXT,detail TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS hybrid_evaluations(id INTEGER PRIMARY KEY,provider TEXT,model TEXT,fingerprint TEXT,result TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT OR IGNORE INTO hybrid_versions VALUES(4);
            CREATE TABLE IF NOT EXISTS hybrid_model_preferences(account_id TEXT,provider TEXT,models TEXT NOT NULL,PRIMARY KEY(account_id,provider));
            CREATE TABLE IF NOT EXISTS hybrid_selection(account_id TEXT PRIMARY KEY,last_category TEXT);
            CREATE TABLE IF NOT EXISTS hybrid_routing(account_id TEXT,message_id TEXT,revision TEXT,PRIMARY KEY(account_id,message_id));
            CREATE TABLE IF NOT EXISTS hybrid_escalations(account_id TEXT,message_id TEXT,task_key TEXT,provider TEXT,model TEXT,fingerprint TEXT,reason TEXT,PRIMARY KEY(account_id,message_id,task_key,provider,model,fingerprint));
            ''')
            self.initialize_provider_authorization(db)
            discovery_columns={r[1] for r in db.execute('PRAGMA table_info(hybrid_discovery)')}
            if discovery_columns and 'credential_revision' not in discovery_columns:db.execute('ALTER TABLE hybrid_discovery ADD COLUMN credential_revision INTEGER NOT NULL DEFAULT 0')
            if discovery_columns and 'account_id' not in discovery_columns:db.execute('ALTER TABLE hybrid_discovery ADD COLUMN account_id TEXT')
            for fresh in ('cloudflare','sambanova','siliconflow'):
                db.executemany('INSERT OR IGNORE INTO hybrid_account_providers VALUES(?,?,0,0)',[('personal',fresh),('university',fresh)])
            db.executemany("INSERT OR IGNORE INTO hybrid_account_providers(account_id,provider,enabled,legacy_allowed) VALUES(?,'zai',0,0)",[('personal',),('university',)])
            for provider in PROVIDERS:
                db.execute('INSERT OR IGNORE INTO hybrid_providers(provider,enabled,endpoint) VALUES(?,?,?)',(provider,int(provider=='ollama'),'http://127.0.0.1:11434' if provider=='ollama' else None))

        with self.connect() as db:
            for provider in set(PROVIDERS)-set(ACTIVE_PROVIDERS):
                if db.execute('UPDATE hybrid_providers SET enabled=0 WHERE provider=? AND enabled=1',(provider,)).rowcount:
                    db.execute("INSERT INTO hybrid_events(event,provider,detail) VALUES('provider_retired',?,'Credentials and history retained; no inference or consent changes')",(provider,))

        with self.connect() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(hybrid_attempts)')}
            for name,kind in [('worker_id','TEXT'),('lease_until','REAL NOT NULL DEFAULT 0'),('finished_at','REAL')]:
                if name not in columns:db.execute(f'ALTER TABLE hybrid_attempts ADD COLUMN {name} {kind}')
            db.execute('CREATE INDEX IF NOT EXISTS hybrid_active_attempt ON hybrid_attempts(status,lease_until)')
            db.execute("CREATE INDEX IF NOT EXISTS hybrid_outcome_attempt ON hybrid_events(json_extract(detail,'$.attempt_id')) WHERE event='organization_verified'")
            db.execute('CREATE INDEX IF NOT EXISTS hybrid_usage_window ON hybrid_attempts(created_at,account_id,provider)')
            db.execute("CREATE INDEX IF NOT EXISTS hybrid_finish_attempt ON hybrid_events(json_extract(detail,'$.attempt_id')) WHERE event='inference_finished'")
            db.execute('INSERT OR IGNORE INTO hybrid_versions VALUES(10)')
            if not db.execute('SELECT 1 FROM hybrid_versions WHERE version=11').fetchone():
                from email_agent.application.model_errors import retry_delay
                rows=db.execute("SELECT a.*,d.payload diagnostic FROM hybrid_attempts a JOIN hybrid_attempt_diagnostics d ON d.attempt_id=a.id WHERE a.error='provider_rate_limited' AND NOT EXISTS(SELECT 1 FROM hybrid_attempts n WHERE n.provider=a.provider AND n.created_at>a.created_at AND n.status='done') ORDER BY a.id DESC").fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_attempt_diagnostics'").fetchone() else []
                for row in rows:
                    wait=retry_delay(json.loads(row['diagnostic']).get('reason',''))
                    if wait and row['created_at']+wait>time.time():
                        db.execute("UPDATE hybrid_health SET until=MAX(until,?) WHERE provider=? AND model IN (?,'*')",(row['created_at']+wait,row['provider'],row['model']))
                db.execute('INSERT INTO hybrid_versions VALUES(11)')

        self.recover_expired_attempts()

    def recover_expired_attempts(self):
        now=time.time()
        with self.connect() as db:
            rows=db.execute("SELECT id,account_id,provider,model FROM hybrid_attempts WHERE status='running' AND COALESCE(NULLIF(lease_until,0),created_at+120)<?",(now-30,)).fetchall()
            for row in rows:
                db.execute("UPDATE hybrid_attempts SET status='interrupted',error='worker_interrupted',lease_until=0 WHERE id=? AND status='running'",(row['id'],))
                db.execute("INSERT INTO hybrid_events(account_id,event,provider,model,detail) VALUES(?,'inference_interrupted',?,?,?)",(row['account_id'],row['provider'],row['model'],json.dumps({'attempt_id':row['id'],'detected_at':now,'reason':'Execution lease expired; provider outcome unknown'})))
        return len(rows)

    def preference(self,account,value=None):
        if account not in ('personal','university'):raise ValueError('Unknown account')
        with self.connect() as db:
            if value is not None:
                if value not in ('auto','local','cloud'):raise ValueError('Unknown preference')
                db.execute('INSERT INTO hybrid_preferences VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET preference=excluded.preference',(account,value))
                db.execute("INSERT INTO hybrid_events(account_id,event,detail) VALUES(?,'preference',?)",(account,value))
            row=db.execute('SELECT preference FROM hybrid_preferences WHERE account_id=?',(account,)).fetchone()
            return row[0] if row else 'auto'

    def provider(self,provider):
        if provider not in PROVIDERS:raise ValueError('Unknown provider')
        with self.connect() as db:return dict(db.execute('SELECT * FROM hybrid_providers WHERE provider=?',(provider,)).fetchone())
    def configure(self,provider,enabled,endpoint=None,trust_lan=False):
        self.provider(provider)
        if enabled and provider not in ACTIVE_PROVIDERS:raise ValueError('Provider retired from active use; credentials and history are retained')
        with self.connect() as db:
            db.execute('UPDATE hybrid_providers SET enabled=?,endpoint=COALESCE(?,endpoint),trust_lan=? WHERE provider=?',(int(enabled),endpoint,int(trust_lan),provider))
            db.execute("INSERT INTO hybrid_events(event,provider,detail) VALUES('provider_configuration',?,?)",(provider,json.dumps(dict(enabled=enabled,trust_lan=trust_lan))))

    def register(self,capability,free_until=0):
        if capability.provider not in PROVIDERS or not capability.model or len(capability.model)>180 or not 2048<=capability.context<=2_000_000:raise ValueError('Invalid discovered capability')
        capability=replace(capability,paid_authorized=False,free=False) if capability.provider=='openai' else replace(capability,paid_authorized=False)
        with self.connect() as db:
            db.execute('INSERT INTO hybrid_models VALUES(?,?,?,?,?) ON CONFLICT(provider,model) DO UPDATE SET payload=excluded.payload,free_until=excluded.free_until,validated_at=excluded.validated_at',(capability.provider,capability.model,json.dumps(asdict(capability)),free_until,time.time() if capability.validated else None))

    def model_allowed(self,account,provider,model):
        with self.connect() as db:
            row=db.execute('SELECT models FROM hybrid_model_preferences WHERE account_id=? AND provider=?',(account,provider)).fetchone()
            return not row or not json.loads(row[0]) or model in json.loads(row[0])

    def select_models(self,account,provider,models):
        self.preference(account);self.provider(provider)
        if provider not in ACTIVE_PROVIDERS:raise ValueError("Provider retired from model selection")
        if len(models)>12 or len(set(models))!=len(models):raise ValueError('Select at most12 distinct models')
        eligible={m['model'] for m in self.models() if m['provider']==provider and m['payload']['validated'] and m['payload']['structured'] and self.payment_allowed(account,ModelCapability(**m['payload'])) and (provider in ('ollama','openai') or m['free_until']>time.time())}
        if not set(models)<=eligible:raise ValueError('Only evaluated models allowed by the account payment policy may be selected')
        with self.connect() as db:
            db.execute('INSERT INTO hybrid_model_preferences VALUES(?,?,?) ON CONFLICT(account_id,provider) DO UPDATE SET models=excluded.models',(account,provider,json.dumps(models)))
            db.execute("INSERT INTO hybrid_events(account_id,event,provider,detail) VALUES(?,'model_selection',?,?)",(account,provider,json.dumps(models)))
        return dict(models=models)

    def models(self):
        with self.connect() as db:return [dict(r,payload=json.loads(r['payload'])) for r in db.execute('SELECT * FROM hybrid_models ORDER BY provider,model')]

    def group_candidates(self,account):
        with self.connect() as db:
            rows=db.execute("""WITH candidates AS (
                SELECT f.message_id,COALESCE(json_extract(f.payload,'$.dimensions.document_type'),lower(json_extract(f.payload,'$.message_type'))) kind
                FROM semantic_frames f JOIN message_category_assignments a USING(account_id,message_id) JOIN message_content_state c USING(account_id,message_id)
                WHERE f.account_id=? AND a.category_id='other' AND a.source!='manual' AND c.status='complete'
                AND json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL
            ),ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY kind ORDER BY message_id) rn,COUNT(*) OVER(PARTITION BY kind) n FROM candidates
                WHERE kind NOT IN ('','unknown','other','message','notification','update','correspondence','conversation'))
                SELECT kind,message_id FROM ranked WHERE n>=3 AND rn<=3 ORDER BY kind,rn LIMIT 24""",(account,)).fetchall()
        groups={}
        for row in rows:groups.setdefault(row['kind'],[]).append(row['message_id'])
        return list(groups.values())
    def claim_group(self,account,key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO hybrid_group_runs(account_id,task_key,status) VALUES(?,?,'pending')",(account,key))
            return db.execute("UPDATE hybrid_group_runs SET status='running',lease=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND task_key=? AND status!='done' AND due<=? AND lease<=?",(time.time()+120,account,key,time.time(),time.time())).rowcount==1
    def finish_group(self,account,key,status,result):
        with self.connect() as db:db.execute('UPDATE hybrid_group_runs SET status=?,lease=0,due=?,result=?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND task_key=?',(status,time.time()+300 if status=='retry' else 0,json.dumps(result),account,key))

    def attempt_version(self,account,provider=None):
        with self.connect() as db:return db.execute('SELECT COALESCE(MAX(id),0) FROM hybrid_attempts WHERE account_id=? AND (? IS NULL OR provider=?)',(account,provider,provider)).fetchone()[0]

    def dispatch_pause(self):
        """Read-only wake deadline; never retries a provider before its own circuit permits."""
        now=time.time();deadlines=[]
        for account in ('personal','university'):
            candidates=self.candidates(account)
            if any(self.healthy(m.provider,m.model) for m in candidates):return 30.0
            with self.connect() as db:
                for model in candidates:
                    row=db.execute("SELECT MAX(until) FROM hybrid_health WHERE provider=? AND model IN (?,'*') AND disabled=0",(model.provider,model.model)).fetchone()
                    if row and row[0] and row[0]>now:deadlines.append(row[0])
        return max(1,min(60,min(deadlines)-now)) if deadlines else 60.0

    def model_load(self,account):
        with self.connect() as db:
            rows=db.execute("SELECT provider,model,SUM(created_at>?) recent,AVG(CASE WHEN status='done' THEN seconds END) seconds FROM hybrid_attempts WHERE account_id=? AND created_at>? AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'eval:%' GROUP BY provider,model",(time.time()-60,account,time.time()-86400)).fetchall()
        return {(r['provider'],r['model']):dict(recent_requests=r['recent'],mean_seconds=r['seconds']) for r in rows}
    def candidates(self,account):
        now=time.time();result=[]
        with self.connect() as db:
            rows=db.execute("SELECT m.* FROM hybrid_models m JOIN hybrid_providers p USING(provider) WHERE p.enabled=1 AND json_extract(m.payload,'$.validated')=1 AND json_extract(m.payload,'$.structured')=1").fetchall()
            for row in rows:
                model=ModelCapability(**json.loads(row['payload']))
                if model.provider not in ACTIVE_PROVIDERS or not self.payment_allowed(account,model):continue
                if model.provider!='ollama' and not self.model_checked(account,model.provider,model.model):continue
                if model.provider=='openai':model=replace(model,paid_authorized=True)
                if not self.account_allows(account,model.provider) or not self.model_allowed(account,model.provider,model.model):continue
                if model.provider!='ollama':
                    standing=self.standing(account,model.provider)
                    grant=db.execute('SELECT * FROM hybrid_grants WHERE account_id=? AND provider=? AND enabled=1 AND EXISTS(SELECT 1 FROM json_each(models) WHERE value=?)',(account,model.provider,model.model)).fetchone()
                    if (model.provider!='openai' and row['free_until']<now and not standing) or not grant and not standing:continue
                    if self.shared_quota and self.shared_quota.status()['remaining']<1 and not self.shared_quota.free_automation(model.provider):continue
                    if standing:
                        result.append(model);continue
                    used,inp,out=db.execute('SELECT COUNT(*),COALESCE(SUM(input_bound),0),COALESCE(SUM(output_bound),0) FROM hybrid_attempts WHERE grant_id=?',(grant['id'],)).fetchone()
                    if used>=grant['request_cap'] or inp+5000>grant['input_cap'] or out+2048>grant['output_cap']:continue
                elif self.provider('ollama')['trust_lan'] and not db.execute("SELECT 1 FROM hybrid_grants WHERE account_id=? AND provider='ollama' AND enabled=1 AND EXISTS(SELECT 1 FROM json_each(models) WHERE value=?)",(account,model.model)).fetchone():continue
                result.append(model)
        return result

    def reuse_discovered_model(self,account,cap,unchanged):
        if not unchanged or not cap.validated or not cap.structured or not cap.free or not self.payment_allowed(account,cap):return False
        with self.connect() as db:
            proof=db.execute("SELECT operation FROM hybrid_model_checks WHERE provider=? AND model=? AND json_extract(result,'$.passed')=1 ORDER BY created_at DESC LIMIT 1",(cap.provider,cap.model)).fetchone()
        if not proof:return False
        self.record_model_check(account,cap,dict(passed=True,source='reused_schema_evidence_after_authenticated_discovery',original_operation=proof[0]),'connect-existing-proof')
        return True

    def healthy(self,provider,model,allow_disabled=False):
        with self.connect() as db:
            availability=db.execute('SELECT enabled,last_state FROM hybrid_providers WHERE provider=?',(provider,)).fetchone()
            if availability and not allow_disabled and (not availability['enabled'] or availability['last_state']=='offline'):return False
            rows=db.execute("SELECT * FROM hybrid_health WHERE provider=? AND model IN (?,'*')",(provider,model)).fetchall()
            # Explicit bounded synthetic tests are the revalidation path for
            # model/configuration failures, never for quota or auth circuits.
            return all(allow_disabled and row['model']!='*' and row['last_error'] in ('provider_request_rejected','model_unavailable','invalid_model','model_access_denied') or not row['disabled'] and row['until']<=time.time() for row in rows)

    def credential_changed(self,provider):
        with self.connect() as db:
            db.execute('INSERT INTO hybrid_credential_revisions VALUES(?,1) ON CONFLICT(provider) DO UPDATE SET revision=revision+1',(provider,))
            # Replacing a broken credential never clears quota/503 circuits.
            db.execute("UPDATE hybrid_health SET disabled=0,until=0 WHERE provider=? AND last_error='authentication_failed'",(provider,))
            if provider!='ollama':
                db.execute('UPDATE hybrid_models SET free_until=0 WHERE provider=?',(provider,))
                db.execute("UPDATE hybrid_providers SET enabled=0,last_state='not_tested',last_checked=NULL WHERE provider=?",(provider,))
            db.execute("INSERT INTO hybrid_events(event,provider,detail) VALUES('credential_replaced',?,'No quota or usage reset')",(provider,))

    def legacy_credentials_unchanged(self,provider):
        with self.connect() as db:
            row=db.execute('SELECT revision FROM hybrid_credential_revisions WHERE provider=?',(provider,)).fetchone()
            return not row or row[0]==0

    def account_allows(self,account,provider,legacy=False):
        with self.connect() as db:
            row=db.execute('SELECT enabled,legacy_allowed FROM hybrid_account_providers WHERE account_id=? AND provider=?',(account,provider)).fetchone()
            return row is None or bool(row['enabled'] and (not legacy or row['legacy_allowed']))

    def adopt_legacy(self):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM hybrid_events WHERE provider='gemini' AND event IN ('legacy_adopted','provider_configuration')").fetchone():return
            db.execute("UPDATE hybrid_providers SET enabled=1,last_state='legacy_authorized' WHERE provider='gemini'")
            db.execute("INSERT INTO hybrid_events(event,provider,detail) VALUES('legacy_adopted','gemini','Existing exact task grant and account consent only; no expansion')")

    @staticmethod
    def _task_key(db,model,key):
        row=db.execute('SELECT revision FROM hybrid_credential_revisions WHERE provider=?',(model.provider,)).fetchone()
        return key+':model:'+model.fingerprint+':credential:'+str(row[0] if row else 0)

    def saved_result(self,account,model,key):
        with self.connect() as db:
            prefix=key+':model:'+model.fingerprint+':credential:'
            row=db.execute("SELECT a.*,r.response FROM hybrid_attempts a JOIN hybrid_responses r ON r.attempt_id=a.id WHERE a.account_id=? AND a.provider=? AND a.model=? AND substr(a.task_key,1,?)=? AND a.status='done' ORDER BY a.id DESC LIMIT 1",(account,model.provider,model.model,len(prefix),prefix)).fetchone()
            return InferenceResult(row['response'],model.provider,model.model,json.loads(row['usage'] or '{}'),row['seconds'] or 0) if row else None

    def replay_candidates(self,account):
        with self.connect() as db:return [ModelCapability(**json.loads(r['payload'])) for r in db.execute("SELECT m.payload FROM hybrid_models m WHERE EXISTS(SELECT 1 FROM hybrid_attempts a JOIN hybrid_responses r ON r.attempt_id=a.id WHERE a.account_id=? AND a.provider=m.provider AND a.model=m.model AND a.status='done')",(account,)) if json.loads(r['payload'])['provider'] in ACTIVE_PROVIDERS]

    def grant(self,account,provider,models,requests,input_tokens,output_tokens,messages,consent,scope_id=None):
        if account not in ('personal','university') or provider not in PROVIDERS or not consent or not 1<=len(models)<=12 or any(not isinstance(m,str) or not m or len(m)>180 for m in models) or any(type(v)is not int or v<1 for v in (requests,input_tokens,output_tokens,messages)):raise ValueError('Account/provider-specific consent and finite budget required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if scope_id:
                existing=db.execute('SELECT g.* FROM hybrid_grant_scopes s JOIN hybrid_grants g ON g.id=s.grant_id WHERE s.scope_id=?',(scope_id,)).fetchone()
                if existing:
                    if (existing['account_id'],existing['provider'],json.loads(existing['models']),existing['request_cap'],existing['input_cap'],existing['output_cap'],existing['message_cap'])!=(account,provider,models,requests,input_tokens,output_tokens,messages):raise ValueError('Immutable scope cannot expand or change account')
                    return existing['id']
            # Replacement cannot reset a prior grant. A separate explicit new
            # scope creates a separately audited grant only through this action.
            db.execute('UPDATE hybrid_grants SET enabled=0 WHERE account_id=? AND provider=?',(account,provider))
            db.execute('INSERT INTO hybrid_account_providers VALUES(?,?,1,0) ON CONFLICT(account_id,provider) DO UPDATE SET enabled=1',(account,provider))
            id=db.execute('INSERT INTO hybrid_grants(account_id,provider,models,consent,request_cap,input_cap,output_cap,message_cap) VALUES(?,?,?,?,?,?,?,?)',(account,provider,json.dumps(models),consent,requests,input_tokens,output_tokens,messages)).lastrowid
            if scope_id:db.execute('INSERT INTO hybrid_grant_scopes VALUES(?,?)',(scope_id,id))
            db.execute("INSERT INTO hybrid_events(account_id,event,provider,detail) VALUES(?,'explicit_grant',?,?)",(account,provider,str(id)))
            return id

    def revoke(self,account,provider):
        with self.connect() as db:
            db.execute('UPDATE hybrid_standing_access SET enabled=0 WHERE account_id=? AND provider=?',(account,provider))
            db.execute('UPDATE hybrid_grants SET enabled=0 WHERE account_id=? AND provider=?',(account,provider))
            db.execute('INSERT INTO hybrid_account_providers VALUES(?,?,0,0) ON CONFLICT(account_id,provider) DO UPDATE SET enabled=0,legacy_allowed=0',(account,provider))
            db.execute("INSERT INTO hybrid_events(account_id,event,provider) VALUES(?,'revoke',?)",(account,provider))

    def reserve(self,account,model,key,ids,input_bound,output_bound,synthetic_scope=None):
        self.recover_expired_attempts()
        if model.provider not in ACTIVE_PROVIDERS:raise ValueError('Provider retired from active use')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            public_probe=bool(synthetic_scope and model.provider!='openai')
            standing=self.standing(account,model.provider) and model.free and not public_probe
            key=self._task_key(db,model,key)
            if not public_probe and not db.execute('SELECT 1 FROM hybrid_providers WHERE provider=? AND enabled=1',(model.provider,)).fetchone():raise ValueError('Provider disabled')
            if not self.healthy(model.provider,model.model,allow_disabled=public_probe):raise ValueError('Provider circuit is unavailable; retry interval is retained')
            if not public_probe and not self.account_allows(account,model.provider):raise ValueError('Account provider consent revoked')
            retry_prefix=key+'#retry'
            prior=db.execute("SELECT * FROM hybrid_attempts WHERE account_id=? AND provider=? AND model=? AND (task_key=? OR substr(task_key,1,?)=?) ORDER BY id",(account,model.provider,model.model,key,len(retry_prefix),retry_prefix)).fetchall()
            if prior:
                recoverable=prior[-1]['status']=='interrupted' and prior[-1]['error']=='worker_interrupted' or prior[-1]['status']=='failed' and prior[-1]['error'] in ('provider_rate_limited','provider_unavailable','network_unavailable')
                recent=sum(r['created_at']>time.time()-600 for r in prior)
                if not recoverable or (recent>=2 if standing else len(prior)>=2):raise ValueError('Fingerprint already attempted; retry cooldown retained' if recoverable else 'Fingerprint already attempted; no hidden replay')
                if not self.healthy(model.provider,model.model):raise ValueError('Circuit is cooling down')
                key+='#retry'+str(len(prior))
            running=db.execute("SELECT 1 FROM hybrid_attempts WHERE provider=? AND status='running' AND COALESCE(NULLIF(lease_until,0),created_at+120)>?",(model.provider,time.time(),)).fetchone()
            if running:raise ValueError('One inference per provider at a time')
            grant=None
            local=model.provider=='ollama' and not self.provider('ollama')['trust_lan']
            if not local:
                if not self.payment_allowed(account,model):raise ValueError('Free eligibility or explicit paid authorization required')
                grant=db.execute('SELECT * FROM hybrid_grants WHERE account_id=? AND provider=? AND enabled=1 AND EXISTS(SELECT 1 FROM json_each(models) WHERE value=?) ORDER BY id DESC LIMIT 1',(account,model.provider,model.model)).fetchone()
                if synthetic_scope and model.provider!='openai':
                    public=db.execute('SELECT * FROM hybrid_synthetic_scopes WHERE id=? AND account_id=? AND provider=? AND model=? AND credential_revision=?',(synthetic_scope,account,model.provider,model.model,self.credential_revision(model.provider,db))).fetchone()
                    if not public or ids or input_bound>public['input_cap'] or output_bound>public['output_cap']:raise ValueError('Synthetic scope cannot submit mailbox contents or exceed its single-request bounds')
                    if db.execute('SELECT 1 FROM hybrid_attempts WHERE account_id=? AND provider=? AND model=? AND task_key=?',(account,model.provider,model.model,self._task_key(db,model,synthetic_scope))).fetchone():raise ValueError('Synthetic request already reserved')
                    grant=None
                elif standing:grant=None
                elif not grant:raise ValueError('No external data-processing consent/budget')
                if grant:
                    n,inp,out=db.execute('SELECT COUNT(*),COALESCE(SUM(input_bound),0),COALESCE(SUM(output_bound),0) FROM hybrid_attempts WHERE grant_id=?',(grant['id'],)).fetchone()
                    old={r[0] for r in db.execute('SELECT message_id FROM hybrid_grant_messages WHERE grant_id=?',(grant['id'],))}
                    if n+1>grant['request_cap'] or inp+input_bound>grant['input_cap'] or out+output_bound>grant['output_cap'] or len(old|set(ids))>grant['message_cap']:raise ValueError('Finite external inference budget exhausted')
                    db.executemany('INSERT OR IGNORE INTO hybrid_grant_messages VALUES(?,?)',[(grant['id'],id) for id in ids])
            else:
                # Local resource guard, not a cloud monetary authorization.
                n=db.execute("SELECT COUNT(*) FROM hybrid_attempts WHERE provider='ollama' AND created_at>?",(time.time()-60,)).fetchone()[0]
                if n>=4:raise ValueError('Local inference cooldown')
            if not local:
                self.charge_paid(account,model,input_bound,output_bound,db)
                if self.shared_quota and not self.shared_quota.reserve(account,'hybrid:'+model.provider):raise ValueError('Shared application request ceiling reached')
            import os,socket
            now=time.time()
            attempt=db.execute('INSERT INTO hybrid_attempts(account_id,provider,model,task_key,grant_id,input_bound,output_bound,created_at,worker_id,lease_until) VALUES(?,?,?,?,?,?,?,?,?,?)',(account,model.provider,model.model,key,grant['id'] if grant else None,input_bound,output_bound,now,f'{socket.gethostname()}:{os.getpid()}',now+120)).lastrowid
            db.executemany('INSERT INTO hybrid_attempt_messages VALUES(?,?,?)',[(attempt,account,id) for id in set(ids)])
            return attempt

    def finish(self,id,status,result=None,error=None):
        with self.connect() as db:
            row=db.execute('SELECT * FROM hybrid_attempts WHERE id=?',(id,)).fetchone()
            if not row or row['status']!='running':return
            db.execute('UPDATE hybrid_attempts SET status=?,usage=?,seconds=?,error=?,finished_at=?,lease_until=0 WHERE id=?',(status,json.dumps(result.usage) if result else None,result.seconds if result else max(0,time.time()-row['created_at']),error.state if error else None,time.time(),id))
            known=db.execute("SELECT json_extract(payload,'$.free') FROM hybrid_models WHERE provider=? AND model=?",(row['provider'],row['model'])).fetchone()
            cost=0 if row['provider']=='ollama' or row['provider']!='openai' and known and known[0] else None
            detail=json.dumps(dict(attempt_id=id,finished_at=time.time(),result=status,cost_usd=cost))
            db.execute("INSERT INTO hybrid_events(account_id,event,provider,model,detail) VALUES(?,'inference_finished',?,?,?)",(row['account_id'],row['provider'],row['model'],detail))
            if result and len(result.text)<=60000:db.execute('INSERT OR IGNORE INTO hybrid_responses VALUES(?,?)',(id,result.text))
            if error:
                db.execute('INSERT OR REPLACE INTO hybrid_attempt_diagnostics VALUES(?,?)',(id,json.dumps(error.diagnostic())))
                old=db.execute('SELECT failures FROM hybrid_health WHERE provider=? AND model=?',(row['provider'],row['model'])).fetchone();failures=(old[0] if old else 0)+1
                unavailable=error.state in ('provider_rate_limited','provider_unavailable','network_unavailable')
                wait=getattr(error,'retry_after',None) or min(3600,30*2**min(failures,7))
                disabled=error.state in ('authentication_failed','model_unavailable','invalid_model','model_access_denied')
                db.execute('INSERT INTO hybrid_health VALUES(?,?,?,?,?,?) ON CONFLICT(provider,model) DO UPDATE SET failures=excluded.failures,until=excluded.until,disabled=excluded.disabled,last_error=excluded.last_error',(row['provider'],row['model'],failures,time.time()+(wait if unavailable else 300),int(disabled),error.state))
                if error.state in ('provider_rate_limited','authentication_failed'):
                    db.execute("INSERT INTO hybrid_health VALUES(?,'*',?,?,?,?) ON CONFLICT(provider,model) DO UPDATE SET failures=excluded.failures,until=MAX(until,excluded.until),disabled=excluded.disabled,last_error=excluded.last_error",(row['provider'],failures,time.time()+wait,int(disabled),error.state))
            else:db.execute('INSERT INTO hybrid_health VALUES(?,?,0,0,0,NULL) ON CONFLICT(provider,model) DO UPDATE SET failures=0,until=0,disabled=0,last_error=NULL',(row['provider'],row['model']))
            if result:db.execute("UPDATE hybrid_providers SET last_state='metadata_connected',last_checked=? WHERE provider=?",(time.time(),row['provider']))

    def record_organization_result(self,account,key,model,verified,category_changes,*,message_id=None,newly_organized=None,issues=None):
        with self.connect() as db:
            exact=self._task_key(db,model,key);prefix=exact+'#retry'
            attempt=db.execute("SELECT id FROM hybrid_attempts WHERE account_id=? AND provider=? AND model=? AND status='done' AND (task_key=? OR substr(task_key,1,?)=?) ORDER BY id DESC LIMIT 1",(account,model.provider,model.model,exact,len(prefix),prefix)).fetchone()
            if attempt:db.execute("INSERT INTO hybrid_events(account_id,event,provider,model,detail) VALUES(?,'organization_verified',?,?,?)",(account,model.provider,model.model,json.dumps(dict(attempt_id=attempt[0],verified=bool(verified),category_changes=int(category_changes),message_id=message_id,newly_organized=newly_organized,validation_issues=issues))))

    def reconcile_other(self,account):
        with self.connect() as db:
            db.execute("DELETE FROM hybrid_routing WHERE account_id=? AND EXISTS(SELECT 1 FROM hybrid_tasks t JOIN message_content_state c USING(account_id,message_id) WHERE t.account_id=hybrid_routing.account_id AND t.message_id=hybrid_routing.message_id AND c.status='complete' AND c.digest!=t.content_key)",(account,))
            db.execute('''INSERT OR IGNORE INTO hybrid_tasks(account_id,message_id,content_key,status,reason)
                SELECT w.account_id,w.message_id,COALESCE(c.digest,'missing'),'waiting_cloud','Awaiting model eligibility check'
                FROM mail_work w JOIN message_content_state c USING(account_id,message_id)
                WHERE w.account_id=? AND w.stage='semantic' AND w.status IN ('pending','retry','uncertain') AND c.status='complete' ''',(account,))
            db.execute('''INSERT OR IGNORE INTO hybrid_tasks(account_id,message_id,content_key,status,reason)
                SELECT a.account_id,a.message_id,COALESCE(c.digest,'missing'),CASE WHEN c.status='complete' THEN 'waiting_cloud' ELSE 'waiting_body' END,
                CASE WHEN c.status='complete' THEN 'Awaiting model eligibility check' ELSE 'Email content is not available yet' END
                FROM message_category_assignments a LEFT JOIN message_content_state c USING(account_id,message_id) WHERE a.account_id=? AND a.category_id='other' ''',(account,))
            db.execute('''UPDATE hybrid_tasks SET content_key=(SELECT COALESCE(c.digest,'missing') FROM message_category_assignments a LEFT JOIN message_content_state c USING(account_id,message_id) WHERE a.account_id=hybrid_tasks.account_id AND a.message_id=hybrid_tasks.message_id),status='waiting_cloud',reason='New content awaits model eligibility',due=0,attempts=0,updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND EXISTS(SELECT 1 FROM message_content_state c WHERE c.account_id=hybrid_tasks.account_id AND c.message_id=hybrid_tasks.message_id AND c.status='complete' AND c.digest!=hybrid_tasks.content_key)''',(account,))
            db.execute("UPDATE hybrid_tasks SET status='waiting_cloud',reason='Awaiting model eligibility check' WHERE account_id=? AND status='ready_local' AND NOT EXISTS(SELECT 1 FROM hybrid_routing r WHERE r.account_id=hybrid_tasks.account_id AND r.message_id=hybrid_tasks.message_id)",(account,))
            # Explicit terminal non-readable reasons remain recoverable if new
            # content is later supplied. They never vanish from Other totals.
            db.execute('''UPDATE hybrid_tasks SET status='blocked',reason=COALESCE((SELECT error_kind FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='body'),'No readable body; attachment extraction or retry needed'),updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND status IN ('waiting_body','blocked') AND EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='body' AND w.status IN ('failed','empty','attachment_only'))''',(account,))
            db.execute("UPDATE hybrid_tasks SET status='waiting_body',reason='Reading email content' WHERE account_id=? AND status='blocked' AND EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='body' AND w.status IN ('pending','retry','running'))",(account,))
            db.execute("UPDATE hybrid_tasks SET status='body_processing',reason='Reading email content' WHERE account_id=? AND status='waiting_body' AND EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='body' AND w.status='running')",(account,))
            db.execute("UPDATE hybrid_tasks SET status='waiting_body',reason='Waiting for email content' WHERE account_id=? AND status='body_processing' AND NOT EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='body' AND w.status='running')",(account,))
            db.execute("UPDATE hybrid_tasks SET status='waiting_cloud',reason='Interrupted inference retained; awaiting retry',due=?,lease=0 WHERE account_id=? AND status IN ('local_processing','cloud_processing') AND lease<?",(time.time()+60,account,time.time()))
            db.execute("UPDATE hybrid_tasks SET status='blocked',reason='Your manual category is preserved' WHERE account_id=? AND EXISTS(SELECT 1 FROM message_category_assignments a WHERE a.account_id=hybrid_tasks.account_id AND a.message_id=hybrid_tasks.message_id AND a.source='manual' AND a.category_id='other')",(account,))
            db.execute("""UPDATE hybrid_tasks SET status='analyzed_other',reason='Analyzed; no stronger category is supported',provider=(SELECT source FROM semantic_frames f WHERE f.account_id=hybrid_tasks.account_id AND f.message_id=hybrid_tasks.message_id)
                WHERE account_id=? AND status NOT IN ('blocked','local_processing','cloud_processing') AND EXISTS(SELECT 1 FROM message_category_assignments a WHERE a.account_id=hybrid_tasks.account_id AND a.message_id=hybrid_tasks.message_id AND a.category_id='other') AND EXISTS(SELECT 1 FROM semantic_frames f JOIN message_content_state c USING(account_id,message_id) WHERE f.account_id=hybrid_tasks.account_id AND f.message_id=hybrid_tasks.message_id AND c.status='complete' AND json_extract(f.payload,'$.understood')=1 AND json_extract(f.payload,'$.analysis_reason') IS NULL AND json_extract(f.payload,'$.input_coverage.body_complete')=1 AND COALESCE(json_extract(f.payload,'$.workflow_needs_escalation'),0)=0)
                AND NOT EXISTS(SELECT 1 FROM mail_work w WHERE w.account_id=hybrid_tasks.account_id AND w.message_id=hybrid_tasks.message_id AND w.stage='local' AND w.status!='done')""",(account,))

    def resolve_semantic(self,account,id,understood):
        with self.connect() as db:db.execute("UPDATE mail_work SET status=? WHERE account_id=? AND message_id=? AND stage='semantic'",('done' if understood else 'pending',account,id))

    def escalate(self,account,id,key,capability,reason):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO hybrid_escalations VALUES(?,?,?,?,?,?,?)',(account,id,key,capability.provider,capability.model,capability.fingerprint,reason))
            db.execute('DELETE FROM hybrid_routing WHERE account_id=? AND message_id=?',(account,id))


    def excluded(self,account,id,key):
        with self.connect() as db:return [(r['provider'],r['model']) for r in db.execute("SELECT e.* FROM hybrid_escalations e JOIN hybrid_models m USING(provider,model) WHERE e.account_id=? AND e.message_id=? AND e.task_key=? AND e.fingerprint=json_extract(m.payload,'$.fingerprint')",(account,id,key))]

    def observe_legacy(self,account,result,model):
        from email_agent.application.model_errors import ModelFailure
        campaign=result.get('id')
        if not campaign:return
        with self.connect() as db:
            rows=db.execute('SELECT result FROM organizer_batches WHERE campaign_id=?',(campaign,)).fetchall()
        for row in rows:
            value=json.loads(row[0] or '{}');diagnostic=value.get('diagnostic') or {}
            if diagnostic.get('kind') not in ('provider_rate_limited','provider_unavailable','model_unavailable','authentication_failed','network_unavailable'):continue
            with self.connect() as db:
                old=db.execute("SELECT failures FROM hybrid_health WHERE provider='gemini' AND model=?",(model,)).fetchone();n=(old[0] if old else 0)+1
                db.execute("INSERT INTO hybrid_health VALUES('gemini',?,?,?,?,?) ON CONFLICT(provider,model) DO UPDATE SET failures=excluded.failures,until=excluded.until,disabled=excluded.disabled,last_error=excluded.last_error",(model,n,time.time()+min(3600,60*2**min(n,6)),int(diagnostic['kind'] in ('authentication_failed','model_unavailable')),diagnostic['kind']))

    def overview(self,account):
        self.preference(account)
        with self.connect() as db:
            providers=[dict(r) for r in db.execute('SELECT * FROM hybrid_providers')]
            active=db.execute("SELECT provider,model,status,created_at FROM hybrid_attempts WHERE account_id=? AND task_key NOT LIKE 'qual:%' AND task_key NOT LIKE 'probe:%' AND task_key NOT LIKE 'eval:%' AND (status='done' OR status='running' AND created_at>?) ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,id DESC LIMIT 1",(account,time.time()-120)).fetchone()
            counts=[dict(r) for r in db.execute('SELECT provider,status,COUNT(*) n FROM hybrid_attempts WHERE account_id=? GROUP BY provider,status',(account,))]
            grants=[dict(r) for r in db.execute('SELECT id,provider,models,request_cap,input_cap,output_cap,message_cap,enabled FROM hybrid_grants WHERE account_id=?',(account,))]
            for grant in grants:
                used=db.execute('SELECT COUNT(*),COALESCE(SUM(input_bound),0),COALESCE(SUM(output_bound),0) FROM hybrid_attempts WHERE grant_id=?',(grant['id'],)).fetchone()
                grant['used']=dict(zip(('requests','input_tokens','output_tokens'),used))
            health=[dict(r) for r in db.execute('SELECT * FROM hybrid_health')]
        available=self.candidates(account)
        local_config=self.provider('ollama')
        local_state='Connected' if local_config['last_state']=='metadata_connected' else 'Offline' if local_config['last_state']=='offline' else 'Checking'
        if local_state=='Connected' and active and active['provider']=='ollama' and active['status']=='running' and active['created_at']>time.time()-120:local_state='Busy'
        routes=[dict(provider=m.provider,model=m.model,languages=[lang for lang,score in m.quality.items() if score>=.9]) for m in available if m.generation and m.structured and m.validated and max(m.quality.values(),default=0)>=.9 and self.healthy(m.provider,m.model) and (self.preference(account)!='local' or m.provider=='ollama')]
        cloud=any(r['provider']!='ollama' for r in routes)
        return dict(preference=self.preference(account),free_only=self.free_only(account),mode='Automatic · Free only' if self.free_only(account) else 'Automatic · Authorized paid scopes',local=local_state,cloud='Available' if cloud else 'Limited',active=dict(active) if active else None,providers=providers,models=self.models(),grants=grants,health=health,attempt_counts=counts,eligible_routes=routes)

    def select(self,account,limit=1,provider=None):
        if provider is not None and provider not in ACTIVE_PROVIDERS:raise ValueError("Inactive provider lane")
        cloud_available=any(m.provider!='ollama' for m in self.candidates(account))
        ready_order="CASE WHEN a.category_id='other' THEN 0 ELSE 1 END,CASE WHEN t.status='ready_local' THEN 0 ELSE 1 END," if cloud_available else "CASE WHEN t.status='ready_local' THEN 0 ELSE 1 END,CASE WHEN a.category_id='other' THEN 0 ELSE 1 END,"
        with self.connect() as db:
            scan=db.execute('SELECT position FROM hybrid_scan_cursors WHERE account_id=? AND provider=?',(account,provider)).fetchone() if provider else None
            offset=scan[0] if scan else 0
            previous=db.execute('SELECT last_category FROM hybrid_selection WHERE account_id=?',(account,)).fetchone();last=previous[0] if previous else ''
            rows=db.execute("SELECT t.message_id,a.category_id FROM hybrid_tasks t JOIN message_content_state c USING(account_id,message_id) JOIN message_category_assignments a USING(account_id,message_id) WHERE t.account_id=? AND c.status='complete' AND NOT(a.source='manual' AND a.category_id='other') AND t.status IN ('ready_local','waiting_cloud','failed') AND t.lease<=? AND t.due<=? AND t.attempts<3 ORDER BY CASE WHEN t.reason IN ('Ready for eligible provider','Queued for evaluated local AI') THEN 0 ELSE 1 END,"+ready_order+"CASE WHEN a.category_id>? THEN 0 ELSE 1 END,a.category_id,t.attempts,t.updated_at,t.message_id LIMIT ? OFFSET ?",(account,time.time(),time.time(),last,limit,offset)).fetchall()
            if provider:db.execute('INSERT INTO hybrid_scan_cursors VALUES(?,?,?) ON CONFLICT(account_id,provider) DO UPDATE SET position=excluded.position',(account,provider,offset+len(rows) if rows else 0))
            if rows and not provider:db.execute('INSERT INTO hybrid_selection VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET last_category=excluded.last_category',(account,rows[-1]['category_id']))
            return [r['message_id'] for r in rows]

    def rewind_lane(self,account,provider,unvisited):
        # The chosen row is already leased out of the pending set. Keep the
        # unexamined suffix for the next request instead of skipping the page.
        with self.connect() as db:db.execute('UPDATE hybrid_scan_cursors SET position=MAX(0,position-?) WHERE account_id=? AND provider=?',(unvisited,account,provider))

    def routing_items(self,account,revision,limit):
        with self.connect() as db:return [r[0] for r in db.execute("SELECT t.message_id FROM hybrid_tasks t JOIN message_content_state c USING(account_id,message_id) LEFT JOIN hybrid_routing r USING(account_id,message_id) WHERE t.account_id=? AND c.status='complete' AND t.status IN ('ready_local','waiting_cloud','failed') AND t.lease<=strftime('%s','now') AND COALESCE(r.revision,'')!=? ORDER BY t.updated_at,t.message_id LIMIT ?",(account,revision,max(1,min(100,limit))))]

    def route_task(self,account,id,revision,status,reason):
        with self.connect() as db:
            db.execute("UPDATE hybrid_tasks SET status=?,reason=?,attempts=CASE WHEN ?='Ready for eligible provider' THEN 0 ELSE attempts END WHERE account_id=? AND message_id=? AND lease<=strftime('%s','now') AND status IN ('ready_local','waiting_cloud','failed')",(status,reason,reason,account,id))
            db.execute('INSERT INTO hybrid_routing VALUES(?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET revision=excluded.revision',(account,id,revision))

    def task(self,account,id,status,reason,provider=None,model=None,*,due=0,attempt=False):
        with self.connect() as db:
            db.execute('UPDATE hybrid_tasks SET status=?,reason=?,provider=COALESCE(?,provider),model=COALESCE(?,model),due=?,lease=?,attempts=attempts+?,updated_at=CURRENT_TIMESTAMP WHERE account_id=? AND message_id=?',(status,reason,provider,model,due,time.time()+110 if status.endswith('_processing') else 0,int(attempt),account,id))
            db.execute('INSERT INTO hybrid_events(account_id,message_id,event,provider,model,detail) VALUES(?,?,?,?,?,?)',(account,id,status,provider,model,reason))

    def other_progress(self,account,*,reconcile=True):
        if reconcile:self.reconcile_other(account)
        with self.connect() as db:
            counts={r['status']:r['n'] for r in db.execute("SELECT t.status,COUNT(*) n FROM hybrid_tasks t JOIN message_category_assignments a USING(account_id,message_id) WHERE t.account_id=? AND a.category_id='other' GROUP BY t.status",(account,))}
            moved=db.execute("SELECT COUNT(DISTINCT message_id) FROM classification_audit WHERE account_id=? AND created_at>=datetime('now','-1 day') AND json_extract(before_json,'$.category_id')='other' AND json_extract(after_json,'$.category_id')!='other'",(account,)).fetchone()[0]
            last=db.execute('SELECT MAX(created_at) FROM hybrid_events WHERE account_id=? AND message_id IS NOT NULL',(account,)).fetchone()[0]
        return dict(scope='Current Other, all New/Done states; moved uses distinct messages in last24h',total=sum(counts.values()),counts=counts,moved_24h=moved,last_activity=last)

    def task_rows(self,account,ids):
        if not ids:return {}
        with self.connect() as db:return {r['message_id']:dict(r) for r in db.execute('SELECT message_id,status,reason,provider,model FROM hybrid_tasks WHERE account_id=? AND message_id IN ('+','.join('?' for _ in ids)+')',(account,*ids))}

    def capacity(self,provider):
        if provider=='ollama':
            if not self.resource_admission():return False
        with self.connect() as db:
            return not db.execute("SELECT 1 FROM hybrid_attempts WHERE provider=? AND status='running' AND COALESCE(NULLIF(lease_until,0),created_at+120)>? LIMIT 1",(provider,time.time())).fetchone()

    def claim_messages(self,account,ids):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany("INSERT OR IGNORE INTO hybrid_tasks(account_id,message_id,content_key,status,reason) SELECT m.account_id,m.message_id,c.digest,'waiting_cloud','Existing collection awaits verified organization' FROM messages m JOIN message_content_state c USING(account_id,message_id) WHERE m.account_id=? AND m.message_id=? AND c.status='complete'",[(account,id) for id in ids])
            rows=[db.execute('SELECT lease FROM hybrid_tasks WHERE account_id=? AND message_id=?',(account,id)).fetchone() for id in ids]
            if any(r is None or r['lease']>time.time() for r in rows):return False
            db.executemany('UPDATE hybrid_tasks SET lease=? WHERE account_id=? AND message_id=?',[(time.time()+120,account,id) for id in ids])
            return True

    def release_messages(self,account,ids):
        with self.connect() as db:db.executemany('UPDATE hybrid_tasks SET lease=0 WHERE account_id=? AND message_id=?',[(account,id) for id in ids])

    def record_route(self,attempt,account,selected,languages,bound,excluded):
        languages=(languages,) if isinstance(languages,str) else languages
        allowed={(m.provider,m.model) for m in self.candidates(account)};details=[]
        for row in self.models():
            m=ModelCapability(**row['payload'])
            if not m.validated:continue
            reason='account/cost/budget policy' if (m.provider,m.model) not in allowed else 'language not qualified' if any(m.quality.get(l,0)<.9 for l in languages) else 'context too small' if bound+2048>m.context else 'independent escalation required' if (m.provider,m.model) in excluded else 'provider cooldown/offline' if not self.healthy(m.provider,m.model) else 'busy' if m.provider!=selected.provider and not self.capacity(m.provider) else 'eligible'
            details.append(dict(provider=m.provider,model=m.model,reason=reason))
        reason='Cloud primary' if selected.provider!='ollama' else 'Local fallback: cloud unavailable for this task'
        with self.connect() as db:db.execute("INSERT INTO hybrid_events(account_id,event,provider,model,detail) VALUES(?,'routing_selected',?,?,?)",(account,selected.provider,selected.model,json.dumps(dict(attempt_id=attempt,languages=languages,input_bound=bound,reason=reason,candidates=details))))


class LocalSecretStore:
    """Owner-only files; never returned by API, logs, registry or prompts."""
    def __init__(self,directory):self.path=Path(directory)
    def get(self,provider):
        if provider not in PROVIDERS:raise ValueError('Unknown provider')
        file=self.path/provider
        if not file.exists():return ''
        if file.is_symlink() or file.stat().st_mode&0o077:raise PermissionError('Secret file must be owner-only')
        return file.read_text().strip()
    def put(self,provider,value):
        if provider not in PROVIDERS or not isinstance(value,str) or not 8<=len(value)<=2048 or any(c.isspace() for c in value):raise ValueError('Invalid credential')
        self.path.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(self.path,0o700)
        file=self.path/(provider+'.new');fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as output:output.write(value)
        os.replace(file,self.path/provider)
