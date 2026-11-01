"""Provider-specific live proof and explicitly bounded OpenAI paid consent."""
import hashlib
from decimal import Decimal
import json
import time


class ProviderAuthorization:
    def initialize_provider_authorization(self,db):
        db.executescript('''
        INSERT OR IGNORE INTO hybrid_versions VALUES(5);
        INSERT OR IGNORE INTO hybrid_versions VALUES(6);
        CREATE TABLE IF NOT EXISTS hybrid_attempt_diagnostics(attempt_id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hybrid_standing_access(account_id TEXT,provider TEXT,revision INTEGER,enabled INTEGER NOT NULL,reference TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(account_id,provider));
        CREATE TABLE IF NOT EXISTS hybrid_credential_fingerprints(provider TEXT PRIMARY KEY,digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hybrid_payment_preferences(account_id TEXT PRIMARY KEY,free_only INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS hybrid_model_checks(account_id TEXT,provider TEXT,model TEXT,credential_revision INTEGER,result TEXT,operation TEXT,created_at REAL,PRIMARY KEY(account_id,provider,model,credential_revision));
        CREATE TABLE IF NOT EXISTS hybrid_synthetic_scopes(id TEXT PRIMARY KEY,account_id TEXT,provider TEXT,model TEXT,credential_revision INTEGER,input_cap INTEGER NOT NULL,output_cap INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS hybrid_paid_scopes(grant_id INTEGER PRIMARY KEY,account_id TEXT,provider TEXT,model TEXT,credential_revision INTEGER,input_price TEXT,output_price TEXT,max_usd TEXT,reserved_usd TEXT NOT NULL DEFAULT '0',expires REAL,reference TEXT);
        ''')
        self.repair_model_access_denials(db)
    def repair_model_access_denials(self,db):
        # A provider-declared model entitlement failure is not a bad API key.
        rows=db.execute("SELECT a.id,a.provider,a.model,d.payload FROM hybrid_attempts a JOIN hybrid_attempt_diagnostics d ON d.attempt_id=a.id WHERE a.error='authentication_failed' AND json_extract(d.payload,'$.http_status')=403 AND json_extract(d.payload,'$.context.type')='labs_not_enabled'").fetchall()
        for row in rows:
            payload=json.loads(row['payload']);payload['kind']='model_access_denied';payload['reason']='Provider Labs permission is absent for this model; credential is not disproved'
            db.execute("UPDATE hybrid_attempts SET error='model_access_denied' WHERE id=?",(row['id'],))
            db.execute('UPDATE hybrid_attempt_diagnostics SET payload=? WHERE attempt_id=?',(json.dumps(payload),row['id']))
            db.execute("UPDATE hybrid_health SET last_error='model_access_denied',disabled=1 WHERE provider=? AND model=? AND last_error='authentication_failed'",(row['provider'],row['model']))
            db.execute("INSERT INTO hybrid_events(event,provider,model,detail) VALUES('model_entitlement_diagnostic_repaired',?,?,?)",(row['provider'],row['model'],str(row['id'])))
        for provider in {r['provider'] for r in rows}:
            suffix=':credential:'+str(self.credential_revision(provider,db))
            genuine=db.execute("SELECT 1 FROM hybrid_attempts WHERE provider=? AND error='authentication_failed' AND (task_key LIKE ? OR task_key LIKE ?) LIMIT 1",(provider,'%'+suffix,'%'+suffix+'#retry%')).fetchone()
            if genuine:continue
            db.execute("DELETE FROM hybrid_health WHERE provider=? AND model='*' AND last_error='authentication_failed'",(provider,))
            quota=db.execute("SELECT MAX(until) FROM hybrid_health WHERE provider=? AND model!='*' AND last_error='provider_rate_limited' AND until>?",(provider,time.time())).fetchone()[0]
            if quota:db.execute("INSERT INTO hybrid_health VALUES(?,'*',1,?,0,'provider_rate_limited') ON CONFLICT(provider,model) DO UPDATE SET until=MAX(until,excluded.until),last_error=excluded.last_error",(provider,quota))
        db.execute('INSERT OR IGNORE INTO hybrid_versions VALUES(8)')
    def standing(self,account,provider):
        with self.connect() as db:
            r=db.execute('SELECT * FROM hybrid_standing_access WHERE account_id=? AND provider=? AND enabled=1 AND revision=?',(account,provider,self.credential_revision(provider,db))).fetchone()
            return bool(r)
    def authorize_standing(self,account,providers,reference):
        self.preference(account)
        if not reference or not providers or not set(providers)<={'gemini','groq','openrouter','cerebras','mistral','zai','cloudflare','sambanova','siliconflow'}:raise ValueError('Explicit account/provider Free-only authorization required')
        with self.connect() as db:
            for provider in providers:
                db.execute('INSERT OR REPLACE INTO hybrid_standing_access VALUES(?,?,?,1,?,?)',(account,provider,self.credential_revision(provider,db),reference,time.time()))
                db.execute('INSERT INTO hybrid_account_providers VALUES(?,?,1,0) ON CONFLICT(account_id,provider) DO UPDATE SET enabled=1',(account,provider))
                db.execute('INSERT INTO hybrid_model_preferences VALUES(?,?,?) ON CONFLICT(account_id,provider) DO UPDATE SET models=excluded.models',(account,provider,'[]'))
                db.execute("INSERT INTO hybrid_events(account_id,event,provider,detail) VALUES(?,'standing_free_access',?,?)",(account,provider,json.dumps(dict(reference=reference,qualified_models_only=True,free_only=True,no_paid_fallback=True,no_gmail_mutations=True))))
    def observe_credential(self,provider,secret,explicit=False):
        if not secret:return
        digest=hashlib.sha256(secret.encode()).hexdigest()
        with self.connect() as db:row=db.execute('SELECT digest FROM hybrid_credential_fingerprints WHERE provider=?',(provider,)).fetchone()
        if explicit or row and row[0]!=digest:self.credential_changed(provider)
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO hybrid_credential_fingerprints VALUES(?,?)',(provider,digest))

    def credential_revision(self,provider,db=None):
        if db is None:
            with self.connect() as connection:return self.credential_revision(provider,connection)
        row=db.execute('SELECT revision FROM hybrid_credential_revisions WHERE provider=?',(provider,)).fetchone()
        return row[0] if row else 0
    def record_model_check(self,account,capability,result,operation):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO hybrid_model_checks VALUES(?,?,?,?,?,?,?)',(account,capability.provider,capability.model,self.credential_revision(capability.provider,db),json.dumps(result),operation,time.time()))
    def model_checked(self,account,provider,model):
        with self.connect() as db:
            row=db.execute('SELECT result FROM hybrid_model_checks WHERE provider=? AND model=? AND credential_revision=? ORDER BY created_at DESC LIMIT 1',(provider,model,self.credential_revision(provider,db))).fetchone()
            return bool(row and json.loads(row[0]).get('passed'))
    def paid_scope(self,account,model,db=None):
        if db is None:
            with self.connect() as connection:return self.paid_scope(account,model,connection)
        row=db.execute('''SELECT p.* FROM hybrid_paid_scopes p JOIN hybrid_grants g ON g.id=p.grant_id
            WHERE p.account_id=? AND p.provider=? AND p.model=? AND g.enabled=1 AND p.expires>? AND p.credential_revision=? ORDER BY g.id DESC LIMIT 1''',(account,model.provider,model.model,time.time(),self.credential_revision(model.provider,db))).fetchone()
        return dict(row) if row else None
    def authorize_paid(self,account,model,requests,input_tokens,output_tokens,messages,input_price,output_price,max_usd,reference):
        if model.provider!='openai':raise ValueError('Paid inference is supported only by separately approved OpenAI scopes')
        amounts=[Decimal(str(v)) for v in (input_price,output_price,max_usd)]
        if any(not v.is_finite() or v<=0 for v in amounts):raise ValueError('Explicit current input/output USD prices and a positive total USD ceiling are required')
        if not any(m['provider']==model.provider and m['model']==model.model for m in self.models()):raise ValueError('Discover the model first')
        with self.connect() as db:
            old=db.execute('SELECT p.* FROM hybrid_paid_scopes p JOIN hybrid_grant_scopes s ON s.grant_id=p.grant_id WHERE s.scope_id=?',(reference,)).fetchone()
            if old and old['credential_revision']!=self.credential_revision(model.provider):raise ValueError('A changed credential needs a new explicit paid scope')
            if old and [Decimal(old[k]) for k in ('input_price','output_price','max_usd')]!=amounts:raise ValueError('Immutable paid scope cannot change prices or ceiling')
        id=self.grant(account,model.provider,[model.model],requests,input_tokens,output_tokens,messages,'Explicit OpenAI paid and per-account content-processing consent; no Gmail authority',reference)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO hybrid_paid_scopes(grant_id,account_id,provider,model,credential_revision,input_price,output_price,max_usd,expires,reference) VALUES(?,?,?,?,?,?,?,?,?,?)',(id,account,model.provider,model.model,self.credential_revision(model.provider,db),*[str(v) for v in amounts],time.time()+7*86400,reference))
        return id
    def synthetic_scope(self,id,account,model):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO hybrid_synthetic_scopes VALUES(?,?,?,?,?,16384,2048)',(id,account,model.provider,model.model,self.credential_revision(model.provider,db)))

    def free_only(self,account,value=None):
        if account not in ('personal','university') or value is not None and type(value)is not bool:raise ValueError('Invalid payment preference')
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT INTO hybrid_payment_preferences VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET free_only=excluded.free_only',(account,int(value)))
                db.execute("INSERT INTO hybrid_events(account_id,event,detail) VALUES(?,'payment_preference',?)",(account,'Free only' if value else 'Explicit paid scopes permitted'))
            row=db.execute('SELECT free_only FROM hybrid_payment_preferences WHERE account_id=?',(account,)).fetchone()
            return bool(row[0]) if row else True

    def payment_allowed(self,account,model):
        from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS
        if model.provider not in ACTIVE_PROVIDERS:return False
        if model.provider=='zai' and model.model not in ('glm-4.7-flash','glm-4.5-flash'):return False
        # OpenAI never inherits a free flag or a different provider's consent.
        return bool(not self.free_only(account) and self.paid_scope(account,model)) if model.provider=='openai' else model.free
    def charge_paid(self,account,model,input_bound,output_bound,db):
        if model.provider!='openai':return
        scope=self.paid_scope(account,model,db)
        if not scope:raise ValueError('Explicit OpenAI paid authorization is required')
        cost=(Decimal(input_bound)*Decimal(scope['input_price'])+Decimal(output_bound)*Decimal(scope['output_price']))/Decimal(1000000)
        total=Decimal(scope['reserved_usd'])+cost
        if total>Decimal(scope['max_usd']):raise ValueError('Approved paid USD ceiling reached; queued work is retained')
        db.execute('UPDATE hybrid_paid_scopes SET reserved_usd=? WHERE grant_id=?',(str(total),scope['grant_id']))
