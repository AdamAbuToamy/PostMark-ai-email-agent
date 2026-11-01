"""Immutable task consent and attempts, with conservative proven-usage settlement."""
import json
import time
from email_agent.infrastructure.database.continuous_store import ContinuousStore


class AutomationAIStore(ContinuousStore):
    def initialize_ai(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS automation_ai_grants(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,model TEXT NOT NULL,max_messages INTEGER NOT NULL,request_cap INTEGER NOT NULL,input_cap INTEGER NOT NULL,output_cap INTEGER NOT NULL,enabled INTEGER NOT NULL,reason TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS automation_ai_messages(grant_id TEXT,account_id TEXT,message_id TEXT,PRIMARY KEY(grant_id,account_id,message_id));
            CREATE TABLE IF NOT EXISTS automation_ai_consent(grant_id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS automation_ai_attempts(id INTEGER PRIMARY KEY,grant_id TEXT,campaign_id TEXT,kind TEXT,input_reserved INTEGER NOT NULL,output_reserved INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'reserved',actual_tokens INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(campaign_id,kind));
            CREATE TABLE IF NOT EXISTS automation_ai_settlements(attempt_id INTEGER PRIMARY KEY,input_charged INTEGER NOT NULL CHECK(input_charged>=0),output_charged INTEGER NOT NULL CHECK(output_charged>=0),source TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            # Additive migration: preserve original reservations, failures and consent.
            # Old generation rows contain total usage only. Charge that total to
            # input AND up to the output cap, safely overcounting both dimensions.
            for row in db.execute("SELECT * FROM automation_ai_attempts WHERE status='done' AND id NOT IN (SELECT attempt_id FROM automation_ai_settlements)").fetchall():
                self._settle_usage(db,row)

    @staticmethod
    def _settle_usage(db,row):
        total=row['actual_tokens']
        if row['status']!='done' or type(total)is not int or not 0<total<=row['input_reserved']:return
        output=0 if row['kind']=='count' else min(total,row['output_reserved'])
        db.execute('INSERT OR IGNORE INTO automation_ai_settlements(attempt_id,input_charged,output_charged,source) VALUES(?,?,?,?)',(row['id'],total,output,'provider_count' if row['kind']=='count' else 'provider_total_conservative_both_dimensions'))

    @staticmethod
    def _charged(db,grant):
        return db.execute('''SELECT COUNT(*),COALESCE(SUM(CASE WHEN a.status='done' THEN COALESCE(s.input_charged,a.input_reserved) ELSE a.input_reserved END),0),
            COALESCE(SUM(CASE WHEN a.status='done' THEN COALESCE(s.output_charged,a.output_reserved) ELSE a.output_reserved END),0)
            FROM automation_ai_attempts a LEFT JOIN automation_ai_settlements s ON s.attempt_id=a.id WHERE a.grant_id=?''',(grant,)).fetchone()

    def grant(self,id,account,model,max_messages,requests,input_tokens,output_tokens,*,approval_reference):
        limits=(max_messages,requests,input_tokens,output_tokens)
        if account not in ('personal','university') or not isinstance(approval_reference,str) or not approval_reference.strip() or any(type(v)is not int or v<1 for v in limits):raise ValueError('An explicit account-specific scope, budget and consent reference are required')
        with self.connect() as db:
            row=db.execute('SELECT * FROM automation_ai_grants WHERE id=?',(id,)).fetchone()
            values=(account,model,max_messages,requests,input_tokens,output_tokens)
            if row:
                if tuple(row[k] for k in ('account_id','model','max_messages','request_cap','input_cap','output_cap'))!=values:raise ValueError('Immutable consent cannot expand or reset')
                db.execute('INSERT OR IGNORE INTO automation_ai_consent(grant_id,payload) VALUES(?,?)',(id,json.dumps(dict(account=account,model=model,max_messages=max_messages,requests=requests,input_tokens=input_tokens,output_tokens=output_tokens,free_only=True,full_body=True,structural_autoapproval=False,approval_reference=approval_reference))))
                return
            db.execute('INSERT INTO automation_ai_grants(id,account_id,model,max_messages,request_cap,input_cap,output_cap,enabled,reason) VALUES(?,?,?,?,?,?,?,1,?)',(id,*values,'Explicit account-specific user consent: complete bodies, Free-only, no structural autoapproval'))
            db.execute('INSERT INTO automation_ai_consent(grant_id,payload) VALUES(?,?)',(id,json.dumps(dict(account=account,model=model,max_messages=max_messages,requests=requests,input_tokens=input_tokens,output_tokens=output_tokens,free_only=True,full_body=True,structural_autoapproval=False,approval_reference=approval_reference))))

    def current_grant(self,account):
        with self.connect() as db:
            row=db.execute('SELECT * FROM automation_ai_grants WHERE account_id=? AND enabled=1 ORDER BY created_at DESC LIMIT 1',(account,)).fetchone()
            return dict(row) if row else None

    def reserve_ai(self,grant,campaign,kind,input_tokens,output_tokens,ids):
        if kind not in ('count','generate') or min(input_tokens,output_tokens)<0:raise ValueError('Invalid reservation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            g=db.execute('SELECT * FROM automation_ai_grants WHERE id=? AND enabled=1',(grant,)).fetchone()
            if not g:raise ValueError('No active AI grant')
            if db.execute('SELECT 1 FROM automation_ai_attempts WHERE campaign_id=? AND kind=?',(campaign,kind)).fetchone():raise ValueError('Attempt already reserved; no hidden replay')
            used,inp,out=self._charged(db,grant)
            messages={r[0] for r in db.execute('SELECT message_id FROM automation_ai_messages WHERE grant_id=?',(grant,))}
            if used+1>g['request_cap'] or inp+input_tokens>g['input_cap'] or out+output_tokens>g['output_cap'] or len(messages|set(ids))>g['max_messages']:raise ValueError('Task AI budget exhausted; retain pending queue')
            db.executemany('INSERT OR IGNORE INTO automation_ai_messages VALUES(?,?,?)',[(grant,g['account_id'],id) for id in ids])
            db.execute('INSERT INTO automation_ai_attempts(grant_id,campaign_id,kind,input_reserved,output_reserved) VALUES(?,?,?,?,?)',(grant,campaign,kind,input_tokens,output_tokens))

    def record_ai(self,campaign,kind,status,tokens=None):
        tokens=tokens if type(tokens)is int and tokens>0 else None
        with self.connect() as db:
            db.execute("UPDATE automation_ai_attempts SET status=?,actual_tokens=? WHERE campaign_id=? AND kind=? AND status='reserved'",(status,tokens,campaign,kind))
            row=db.execute('SELECT * FROM automation_ai_attempts WHERE campaign_id=? AND kind=?',(campaign,kind)).fetchone()
            if row:self._settle_usage(db,row)

    def ai_progress(self,account):
        grant=self.current_grant(account)
        if not grant:return dict(authorized=False,reason='No standing task budget; local processing continues')
        with self.connect() as db:
            counts=dict(db.execute('SELECT status,COUNT(*) FROM automation_ai_attempts WHERE grant_id=? GROUP BY status',(grant['id'],)))
            n,inp,out=self._charged(db,grant['id'])
            original=dict(db.execute('SELECT COALESCE(SUM(input_reserved),0) input,COALESCE(SUM(output_reserved),0) output FROM automation_ai_attempts WHERE grant_id=?',(grant['id'],)).fetchone())
            messages=db.execute('SELECT COUNT(*) FROM automation_ai_messages WHERE grant_id=?',(grant['id'],)).fetchone()[0]
        return dict(authorized=True,requests_reserved=n,request_cap=grant['request_cap'],input_reserved=inp,input_cap=grant['input_cap'],output_reserved=out,output_cap=grant['output_cap'],original_reservations=original,messages=messages,message_cap=grant['max_messages'],attempt_states=counts,reason=grant['reason'])

    def ai_wait(self,account,reason):
        with self.connect() as db:db.execute('UPDATE automation_ai_grants SET reason=? WHERE account_id=? AND enabled=1',(reason,account))

    def minimum_scope_fits(self,grant):
        # Reject an impossible scope before creating endless saved previews.
        # Failed/unknown reservations remain charged. Proven successful usage
        # is settled without resetting request counts or changing consent caps.
        from email_agent.application.semantic_mail import SYSTEM,SCHEMA
        usage=self.ai_progress(grant['account_id'])
        minimum=2*(len((SYSTEM+json.dumps(SCHEMA)).encode())+16384)
        return usage['requests_reserved']+2<=grant['request_cap'] and usage['input_reserved']+minimum<=grant['input_cap'] and usage['output_reserved']+4096<=grant['output_cap']

    def select_semantic(self,account,limit=4,priority=()):
        with self.connect() as db:
            placeholders=','.join('?' for _ in priority) or 'NULL'
            known=""" AND NOT EXISTS(SELECT 1 FROM automation_ai_attempts aa JOIN organizer_batches b ON b.campaign_id=aa.campaign_id
                WHERE aa.status='reserved' AND json_extract(b.input_json,'$.account')=w.account_id
                AND EXISTS(SELECT 1 FROM json_each(b.input_json,'$.private_ids') ids WHERE ids.value=w.message_id)) """
            category=db.execute("SELECT a.category_id FROM mail_work w JOIN message_category_assignments a USING(account_id,message_id) WHERE w.account_id=? AND w.stage='semantic' AND w.status IN ('pending','retry') AND w.due<=? AND w.attempts<3 "+known+" GROUP BY a.category_id ORDER BY CASE WHEN a.category_id IN ("+placeholders+") THEN 0 ELSE 1 END,COUNT(*) DESC,a.category_id LIMIT 1",(account,time.time(),*priority)).fetchone()
            if not category:return []
            return [r[0] for r in db.execute("SELECT w.message_id FROM mail_work w JOIN message_category_assignments a USING(account_id,message_id) WHERE w.account_id=? AND a.category_id=? AND w.stage='semantic' AND w.status IN ('pending','retry') AND w.due<=? AND w.attempts<3 "+known+" ORDER BY w.message_id LIMIT ?",(account,category[0],time.time(),limit))]

    def dispatch_ready(self,grant,scope):
        from email_agent.application.semantic_mail import SYSTEM,SCHEMA
        from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore
        batches=SQLiteSemanticOrganizerStore(self.path,self.path.parent/'categories.db').batches(scope['id'])
        upper=sum(len((SYSTEM+json.dumps({k:b['input'][k] for k in ('messages','catalog','local_organization') if k in b['input']},ensure_ascii=False)+json.dumps(SCHEMA)).encode())+16384 for b in batches)
        progress=self.ai_progress(grant['account_id'])
        with self.connect() as db:old={r[0] for r in db.execute('SELECT message_id FROM automation_ai_messages WHERE grant_id=?',(grant['id'],))}
        ids={id for b in batches for id in b['input']['private_ids'].values()}
        # Both token-count and generation calls count against the request cap;
        # both inputs are charged conservatively until successful usage is known.
        return progress['requests_reserved']+2*len(batches)<=grant['request_cap'] and progress['input_reserved']+2*upper<=grant['input_cap'] and progress['output_reserved']+4096*len(batches)<=grant['output_cap'] and len(old|ids)<=grant['max_messages']

    def settle_semantic(self,account,ids,campaign):
        from email_agent.infrastructure.database.semantic_organizer_store import SQLiteSemanticOrganizerStore,input_fingerprint
        store=SQLiteSemanticOrganizerStore(self.path,self.path.parent/'categories.db')
        batches=store.batches(campaign);provider_failed=any(b['status']=='failed' for b in batches)
        for id in ids:
            row=store.message_row(account,id);frame=(row or {}).get('semantic') or {}
            accepted=frame.get('source')=='gemini' and frame.get('input_coverage',{}).get('body_complete') and row['input_fingerprint']==input_fingerprint(row)
            with self.connect() as db:
                work=db.execute("SELECT * FROM mail_work WHERE account_id=? AND message_id=? AND stage='semantic'",(account,id)).fetchone()
                if not work:continue
                count=work['attempts']+1
                status='done' if accepted and frame.get('understood') and not frame.get('analysis_reason') else 'uncertain' if accepted or not provider_failed else 'retry' if count<3 else 'failed'
                db.execute("UPDATE mail_work SET status=?,attempts=?,due=?,error_kind=? WHERE account_id=? AND message_id=? AND stage='semantic'",(status,count,time.time()+3600,frame.get('analysis_reason') if accepted else 'provider_or_validation_failure',account,id))
                db.execute("INSERT INTO automation_events(account_id,message_id,stage,status,reason) VALUES(?,?,'semantic',?,?)",(account,id,status,campaign))
