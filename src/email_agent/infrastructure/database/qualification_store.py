"""Immutable qualification approvals, bounded jobs, certificates and recovery."""
from dataclasses import asdict,replace
import json
import time
import uuid
from email_agent.application.hybrid_ai import ModelCapability,ACTIVE_PROVIDERS
from email_agent.application.semantic_mail import digest
from email_agent.application.qualification_rubric import RUBRIC_VERSION,summarize
from email_agent.application.provider_qualification import qualification_contract
from email_agent.application.model_errors import classify_error
from email_agent.infrastructure.ai.free_catalog import verified_free,priority,policy as free_policy


class SQLiteQualificationStore:
    def __init__(self,hybrid,cases,configured):self.hybrid,self.cases,self.configured=hybrid,cases,configured
    def initialize(self):
        with self.hybrid.connect() as db:
            db.executescript('''
            INSERT OR IGNORE INTO hybrid_versions VALUES(7);
            CREATE TABLE IF NOT EXISTS qualification_plans(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'preview',approved_at REAL,old_daily INTEGER,new_daily INTEGER,reason TEXT,created_at REAL NOT NULL,guard_restored INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS qualification_providers(plan_id TEXT,provider TEXT,stage TEXT NOT NULL DEFAULT 'metadata',metadata_calls INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL,reason TEXT,due REAL NOT NULL DEFAULT 0,PRIMARY KEY(plan_id,provider));
            CREATE TABLE IF NOT EXISTS qualification_jobs(id INTEGER PRIMARY KEY,plan_id TEXT,provider TEXT,model TEXT,candidate INTEGER,part INTEGER,case_indexes TEXT,capability TEXT,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,attempt_id INTEGER,lease REAL NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,result TEXT,error TEXT,UNIQUE(plan_id,provider,candidate,part));
            CREATE TABLE IF NOT EXISTS qualification_certificates(plan_id TEXT,provider TEXT,model TEXT,payload TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(plan_id,provider,model));
            ''')
    def preview(self,providers,selected_models=None):
        selected_models=selected_models or {}
        if not set(selected_models)<=set(providers) or any(not isinstance(m,str) or not 1<=len(m)<=200 for m in selected_models.values()):raise ValueError('Selected models must belong to the requested provider scope')
        if not providers or len(providers)>9 or not set(providers)<=set(ACTIVE_PROVIDERS):raise ValueError('Choose connected Free-only providers')
        if len(providers)!=len(set(providers)) or any(not self.configured(p) for p in providers):raise ValueError('Providers must have configured credentials')
        selected=[]
        for provider in providers:
            models=[m for m in self.hybrid.models() if m['provider']==provider and (provider=='ollama' or m['payload']['free']) and (provider not in selected_models or m['model']==selected_models[provider]) and (provider!='gemini' or m['payload']['free'] or verified_free(m['model']) or self.hybrid.shared_quota and m['model']==self.hybrid.shared_quota.status()['provider_policy'].get('model'))]
            if provider=='cloudflare' and not any(self.hybrid.payment_allowed('personal',ModelCapability(**m['payload'])) for m in models):raise ValueError('Cloudflare: verify Workers Free account eligibility before approving qualification; catalog discovery alone is insufficient')
            models=self.rank(provider,models)[:1]
            if not models:raise ValueError(provider+': no usable free candidate; inspect saved model access and pricing diagnostics')
            config=self.hybrid.provider(provider)
            reuse=bool(config['last_state']=='metadata_connected' and (config['last_checked'] or 0)>time.time()-86400)
            selected.append(dict(provider=provider,revision=self.hybrid.credential_revision(provider),candidate_limit=len(models),allowed_pool=[m['model'] for m in models],candidates=[dict(model=m['model'],output_mode=m['payload'].get('output_mode','json_schema'),context=m['payload']['context']) for m in models[:2]],metadata_limit=0 if reuse else 3,reuse_discovery=reuse,free_verification='local weights' if provider=='ollama' else 'zero price metadata' if provider=='openrouter' else 'Explicit Free-tier account with billing disabled must be confirmed'))
        calls=sum(p['candidate_limit']*((len(self.cases)+2)//3) for p in selected);quota=self.hybrid.shared_quota.status() if self.hybrid.shared_quota else dict(limit=20,remaining=0)
        payload=dict(id=uuid.uuid4().hex,contract=qualification_contract(self.cases),rubric=RUBRIC_VERSION,gold_hash=digest(self.cases),providers=selected,max_requests=calls,input_tokens=calls*16384,output_tokens=calls*2048,metadata_requests=sum(p['metadata_limit'] for p in selected),cost_ceiling_usd=0,mailbox_data=False,cases_per_candidate=len(self.cases),frames_per_request=3,concurrency=1,current_daily_guard=quota['limit'],daily_remaining=quota['remaining'],suggested_daily_guard=100,free_until_quota=False,per_request_input=16384,per_request_output=2048)
        with self.hybrid.connect() as db:db.execute("INSERT INTO qualification_plans(id,payload,status,created_at) VALUES(?,?,'preview',?)",(payload['id'],json.dumps(payload),time.time()))
        return payload
    def refresh_verified_free_pool(self,id,provider,reference):
        if provider!='gemini' or not reference:raise ValueError('Official verified Free-only refresh required')
        with self.hybrid.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM qualification_plans WHERE id=?',(id,)).fetchone()
            if not row:raise ValueError('Unknown plan')
            p=json.loads(row['payload'])
            if not p.get('free_until_quota') or row['status'] not in ('approved','paused'):raise ValueError('This existing task scope does not authorize continuing Free-only discovery')
            selection=next((v for v in p['providers'] if v['provider']==provider),None)
            if not selection or selection['revision']!=self.hybrid.credential_revision(provider,db):raise ValueError('Provider/credential outside existing approval')
            models={r['model'] for r in db.execute('SELECT model FROM hybrid_models WHERE provider=?',(provider,))}
            extra=sorted(m for m in models if verified_free(m) and m not in selection['allowed_pool'])
            if not extra:return self.state(id)
            original=json.loads(json.dumps(p))
            selection['allowed_pool']+=extra;selection['candidate_limit']=len(selection['allowed_pool'])
            selection['candidates']=[dict(model=m,output_mode='json_schema') for m in sorted(selection['allowed_pool'],key=priority,reverse=True)]
            p['max_requests']+=20*len(extra);p['input_tokens']=p['max_requests']*16384;p['output_tokens']=p['max_requests']*2048
            db.execute("INSERT INTO hybrid_events(event,provider,detail) VALUES('free_pool_extended_under_standing_task',?,?)",(provider,json.dumps(dict(reference=reference,source=free_policy()['source'],added=extra,original_approval=original,paid=False,request_bounds_unchanged=True))))
            db.execute('UPDATE qualification_plans SET payload=? WHERE id=?',(json.dumps(p),id))
            db.execute("UPDATE qualification_providers SET stage='metadata',reason='Fresh official Free-tier candidates; existing progress retained',due=0 WHERE plan_id=? AND provider=?",(id,provider))
        return self.state(id)

    def recheck_repaired_wire(self,id,reference):
        if not reference:raise ValueError('Explicit existing task reference required')
        wire='native-validator-json-object-v2';changed=0
        with self.hybrid.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM qualification_plans WHERE id=?',(id,)).fetchone()
            if not row or not json.loads(row[0]).get('free_until_quota'):raise ValueError('Standing Free-only task scope required')
            models=db.execute("SELECT DISTINCT j.provider,j.model,j.candidate FROM qualification_jobs j JOIN hybrid_attempt_diagnostics d ON d.attempt_id=j.attempt_id WHERE j.plan_id=? AND j.status='failed' AND json_extract(d.payload,'$.http_status')=400 AND (json_extract(d.payload,'$.context.code')='json_validate_failed' OR json_extract(d.payload,'$.context.reason_class')='schema_compatibility')",(id,)).fetchall()
            for m in models:
                if db.execute("SELECT 1 FROM hybrid_events WHERE event='qualification_adapter_recheck' AND provider=? AND model=? AND json_extract(detail,'$.plan')=? AND json_extract(detail,'$.wire')=?",(m['provider'],m['model'],id,wire)).fetchone():continue
                db.execute("INSERT INTO hybrid_events(event,provider,model,detail) VALUES('qualification_adapter_recheck',?,?,?)",(m['provider'],m['model'],json.dumps(dict(plan=id,wire=wire,reference=reference,reason='Provider HTTP400 before native nullable-schema/Qwen adapter repair; accepted parts preserved'))))
                changed+=db.execute("UPDATE qualification_jobs SET status='retry',due=0,lease=0 WHERE plan_id=? AND provider=? AND candidate=? AND status IN ('failed','skipped') AND error='provider_request_rejected'",(id,m['provider'],m['candidate'])).rowcount
                db.execute("UPDATE qualification_providers SET stage='testing',due=0,reason='Adapter repaired; bounded wire recheck' WHERE plan_id=? AND provider=?",(id,m['provider']))
        return dict(requeued=changed,wire=wire)

    def rank(self,provider,models):
        with self.hybrid.connect() as db:
            recent={r['model']:dict(r) for r in db.execute("SELECT model,COUNT(*) n,AVG(seconds) seconds FROM hybrid_attempts WHERE provider=? AND status='done' GROUP BY model",(provider,))}
            errors={r['model']:r['last_error'] for r in db.execute('SELECT model,last_error FROM hybrid_health WHERE provider=?',(provider,))}
            disabled={r['model'] for r in db.execute("SELECT model FROM hybrid_health WHERE provider=? AND disabled=1 AND last_error IN ('model_unavailable','model_access_denied','invalid_model')",(provider,))}
        models=[m for m in models if m['model'] not in disabled]
        return sorted(models,key=lambda m:(errors.get(m['model']) not in ('model_unavailable','authentication_failed','invalid_model','model_access_denied','provider_request_rejected','malformed_output','model_identity_mismatch'),priority(m['model'],provider),bool(recent.get(m['model'])),m['payload'].get('structured',False),m['payload'].get('generation',False),m['payload'].get('tools',False),-recent.get(m['model'],{}).get('seconds',60),m['payload']['context']),reverse=True)
    def approve(self,id,gold_hash,free_providers,daily_limit,free_until_quota=False):
        with self.hybrid.connect() as db:
            db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT * FROM qualification_plans WHERE id=?',(id,)).fetchone()
            if not r:raise ValueError('Unknown qualification plan')
            p=json.loads(r['payload'])
            if r['status']!='preview':return self.state(id)
            if gold_hash!=p['gold_hash'] or p['contract']!=qualification_contract(self.cases):raise ValueError('Review the current immutable gold rubric and plan')
            if set(free_providers)!={v['provider'] for v in p['providers'] if v['provider']!='ollama'}:raise ValueError('Verify Free-only eligibility and disabled billing for every selected external provider')
            if type(daily_limit)is not int or not 0<daily_limit<=100:raise ValueError('Explicit local daily guard must be1–100')
            if db.execute("SELECT 1 FROM qualification_plans WHERE status='approved'").fetchone():raise ValueError('One approved qualification plan at a time')
            for v in p['providers']:
                if v['revision']!=self.hybrid.credential_revision(v['provider']):raise ValueError('Credential changed; review a new plan')
                db.execute('INSERT INTO qualification_providers(plan_id,provider,revision) VALUES(?,?,?)',(id,v['provider'],v['revision']))
            old=self.hybrid.shared_quota.status()['limit'] if self.hybrid.shared_quota else 20
            p['free_until_quota']=free_until_quota
            db.execute('UPDATE qualification_plans SET payload=? WHERE id=?',(json.dumps(p),id))
            if free_until_quota:daily_limit=old
            db.execute("UPDATE qualification_plans SET status='approved',approved_at=?,old_daily=?,new_daily=? WHERE id=?",(time.time(),old,daily_limit,id))
            db.execute("INSERT INTO hybrid_events(event,detail) VALUES('qualification_approved',?)",(json.dumps(dict(plan=id,reviewed_gold_hash=gold_hash,max_requests=p['max_requests'],input=p['input_tokens'],output=p['output_tokens'],free_only=True,mailbox=False,daily_guard=daily_limit)),))
        if self.hybrid.shared_quota:
            if free_until_quota:self.hybrid.shared_quota.authorize_free_automation(free_providers,'qualification:'+id)
            else:self.hybrid.shared_quota.configure(daily_limit)
        return self.state(id)
    def ready(self):
        with self.hybrid.connect() as db:
            return bool(db.execute("SELECT 1 FROM qualification_plans a JOIN qualification_providers p ON p.plan_id=a.id WHERE a.status='approved' AND p.due<=? AND (p.stage='metadata' OR p.stage='testing' AND EXISTS(SELECT 1 FROM qualification_jobs j WHERE j.plan_id=p.plan_id AND j.provider=p.provider AND j.status IN ('pending','retry') AND j.due<=?)) LIMIT 1",(time.time(),time.time())).fetchone())
    def runnable(self):
        with self.hybrid.connect() as db:
            r=db.execute("SELECT * FROM qualification_plans WHERE status='approved' ORDER BY created_at LIMIT 1").fetchone()
            if not r:
                for done in db.execute("SELECT id FROM qualification_plans WHERE status='completed' AND guard_restored=0").fetchall():self.restore_guard(done['id'])
                return None
            db.execute("UPDATE qualification_providers SET stage='blocked',reason='metadata_outcome_unknown' WHERE stage='discovering' AND due<?",(time.time(),))
            result=json.loads(r['payload']);result['providers']=[dict(v) for v in db.execute('SELECT * FROM qualification_providers WHERE plan_id=?',(r['id'],))]
            return result
    def state(self,id):
        with self.hybrid.connect() as db:
            r=db.execute('SELECT * FROM qualification_plans WHERE id=?',(id,)).fetchone()
            if not r:raise ValueError('Unknown plan')
            payload=json.loads(r['payload'])
            requests=[dict(v) for v in db.execute("SELECT provider,status,COUNT(*) count,MAX(created_at) last_activity FROM hybrid_attempts WHERE substr(task_key,1,?)=? GROUP BY provider,status",(len('qual:'+id+':'),'qual:'+id+':'))]
            return dict(payload,requests=requests,selection=payload['providers'],status=r['status'],reason=r['reason'],providers=[dict(v) for v in db.execute('SELECT * FROM qualification_providers WHERE plan_id=?',(id,))],jobs=[dict(v) for v in db.execute('SELECT provider,model,status,COUNT(*) count,MAX(error) reason,MIN(due) due FROM qualification_jobs WHERE plan_id=? GROUP BY provider,model,status',(id,))],certificates=[json.loads(v[0]) for v in db.execute('SELECT payload FROM qualification_certificates WHERE plan_id=?',(id,))])
    def latest(self):
        with self.hybrid.connect() as db:r=db.execute('SELECT id FROM qualification_plans ORDER BY created_at DESC LIMIT 1').fetchone()
        return self.state(r[0]) if r else None
    def pause(self,id,reason):
        with self.hybrid.connect() as db:db.execute("UPDATE qualification_plans SET status='paused',reason=? WHERE id=? AND status='approved'",(reason,id))
    def resume(self,id):
        state=self.state(id)
        if state['status']!='paused':raise ValueError('Only a paused existing scope can resume')
        if state['contract']!=qualification_contract(self.cases):raise ValueError('Evaluation contract changed; approve a new plan')
        if any(p['revision']!=self.hybrid.credential_revision(p['provider']) for p in state['selection']):raise ValueError('Credential changed; approve a new plan')
        with self.hybrid.connect() as db:
            if db.execute("SELECT 1 FROM qualification_plans WHERE status='approved'").fetchone():raise ValueError('Another qualification plan is active')
            db.execute("UPDATE qualification_plans SET status='approved',reason=NULL WHERE id=?",(id,))
        return self.state(id)
    def cancel(self,id):
        self.pause(id,'User stopped qualification; attempts and results retained')
        self.restore_guard(id)
        return self.state(id)
    def restore_guard(self,id):
        with self.hybrid.connect() as db:r=db.execute('SELECT old_daily,new_daily FROM qualification_plans WHERE id=?',(id,)).fetchone()
        if r and r['old_daily'] is not None and self.hybrid.shared_quota and self.hybrid.shared_quota.status()['limit']==r['new_daily']:self.hybrid.shared_quota.configure(r['old_daily'])
        with self.hybrid.connect() as db:db.execute('UPDATE qualification_plans SET guard_restored=1 WHERE id=?',(id,))
    def discovery(self,plan,provider,adapter):
        with self.hybrid.connect() as db:
            row=db.execute('SELECT * FROM qualification_providers WHERE plan_id=? AND provider=?',(plan['id'],provider)).fetchone()
            if not db.execute("UPDATE qualification_providers SET stage='discovering',due=? WHERE plan_id=? AND provider=? AND stage='metadata'",(time.time()+120,plan['id'],provider)).rowcount:return
            if row['revision']!=self.hybrid.credential_revision(provider):
                db.execute("UPDATE qualification_providers SET stage='blocked',reason='credential_changed' WHERE plan_id=? AND provider=?",(plan['id'],provider));return
        def reserve():
            with self.hybrid.connect() as db:return db.execute('UPDATE qualification_providers SET metadata_calls=metadata_calls+1 WHERE plan_id=? AND provider=? AND metadata_calls<3',(plan['id'],provider)).rowcount==1
        try:
            with self.hybrid.connect() as db:saved=json.loads(db.execute('SELECT payload FROM qualification_plans WHERE id=?',(plan['id'],)).fetchone()[0])
            policy=next(p for p in saved['providers'] if p['provider']==provider)
            if policy.get('reuse_discovery'):
                found=[dict(m['payload']) for m in self.hybrid.models() if m['provider']==provider]
            else:
                if provider in ('gemini','cloudflare'):adapter.metadata_reserve=reserve
                elif not reserve():raise ValueError('Metadata budget exhausted')
                found=adapter.discover()
            existing={(m['provider'],m['model']):m for m in self.hybrid.models()}
            with self.hybrid.connect() as db:approved=json.loads(db.execute('SELECT payload FROM qualification_plans WHERE id=?',(plan['id'],)).fetchone()[0])
            policy=next(p for p in approved['providers'] if p['provider']==provider);models=[]
            for item in found:
                if item['model'] not in policy['allowed_pool'] or provider in ('openrouter','zai','cloudflare','sambanova','siliconflow') and not item.get('free'):continue
                old=existing.get((provider,item['model']))
                cap=ModelCapability(**old['payload']) if old else ModelCapability(provider,item['model'],max(2048,min(2_000_000,int(item.get('context') or 8192))))
                cap=replace(cap,free=True,output_mode=item.get('output_mode',cap.output_mode),reasoning=bool(item.get('reasoning',False)),context=max(2048,min(2_000_000,int(item.get('context') or cap.context))))
                models.append(dict(provider=provider,model=cap.model,payload=asdict(cap)))
            ranked=self.rank(provider,models)[:policy['candidate_limit']]
            with self.hybrid.connect() as db:
                if not ranked:db.execute("UPDATE qualification_providers SET stage='blocked',reason='no_compatible_free_model_in_approved_pool' WHERE plan_id=? AND provider=?",(plan['id'],provider));return
                existing_candidates={r['model']:r['candidate'] for r in db.execute('SELECT model,MIN(candidate) candidate FROM qualification_jobs WHERE plan_id=? AND provider=? GROUP BY model',(plan['id'],provider))}
                next_candidate=max(existing_candidates.values(),default=-1)+1
                for m in ranked:
                    if m['model'] in existing_candidates:continue
                    candidate=next_candidate;next_candidate+=1
                    for part,start in enumerate(range(0,len(self.cases),3)):
                        db.execute('INSERT OR IGNORE INTO qualification_jobs(plan_id,provider,model,candidate,part,case_indexes,capability) VALUES(?,?,?,?,?,?,?)',(plan['id'],provider,m['model'],candidate,part,json.dumps(list(range(start,min(start+3,len(self.cases))))),json.dumps(m['payload'])))
                db.execute("UPDATE qualification_providers SET stage='testing',reason=NULL,due=0 WHERE plan_id=? AND provider=?",(plan['id'],provider))
        except Exception as exc:
            f=classify_error(exc)
            with self.hybrid.connect() as db:db.execute("UPDATE qualification_providers SET stage='blocked',reason=? WHERE plan_id=? AND provider=?",(f.state,plan['id'],provider))
    def claim(self,plan):
        with self.hybrid.connect() as db:
            recovered=db.execute("SELECT j.* FROM qualification_jobs j WHERE plan_id=? AND status='done' AND NOT EXISTS(SELECT 1 FROM qualification_certificates c WHERE c.plan_id=j.plan_id AND c.provider=j.provider AND c.model=j.model AND json_extract(c.payload,'$.requests')>=(SELECT COUNT(*) FROM qualification_jobs q WHERE q.plan_id=j.plan_id AND q.provider=j.provider AND q.model=j.model AND q.status='done'))",(plan['id'],)).fetchall()
        for row in recovered:
            job=dict(row);job['capability']=json.loads(job['capability']);self.certify(plan,job)
        now=time.time()
        with self.hybrid.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A lost wire outcome is never silently repeated after restart.
            db.execute("UPDATE qualification_jobs SET status='blocked',error='interrupted_outcome_unknown' WHERE status='running' AND lease<? AND NOT EXISTS(SELECT 1 FROM hybrid_attempts a WHERE a.id=qualification_jobs.attempt_id AND a.status='done')",(now,))
            if db.execute("SELECT 1 FROM qualification_jobs WHERE status='running' AND lease>?",(now,)).fetchone():return None
            # Recovered siblings retain the same accepted provider wire format.
            db.execute("""UPDATE qualification_jobs AS j SET capability=(SELECT k.capability FROM qualification_jobs k WHERE k.plan_id=j.plan_id AND k.provider=j.provider AND k.candidate=j.candidate AND json_extract(k.capability,'$.output_mode')='json_object' LIMIT 1) WHERE j.plan_id=? AND j.status IN ('pending','retry') AND json_extract(j.capability,'$.output_mode')='json_schema' AND EXISTS(SELECT 1 FROM qualification_jobs k WHERE k.plan_id=j.plan_id AND k.provider=j.provider AND k.candidate=j.candidate AND json_extract(k.capability,'$.output_mode')='json_object')""",(plan['id'],))
            jobs=db.execute("SELECT j.* FROM qualification_jobs j JOIN qualification_providers p ON p.plan_id=j.plan_id AND p.provider=j.provider WHERE j.plan_id=? AND p.stage='testing' AND (j.status IN ('pending','retry') OR j.status='running' AND j.lease<=? AND EXISTS(SELECT 1 FROM hybrid_attempts a WHERE a.id=j.attempt_id AND a.status='done')) AND j.due<=? AND p.due<=? ORDER BY j.candidate,j.part,j.id",(plan['id'],now,now,now)).fetchall()
            for row in jobs:
                prior=db.execute("SELECT 1 FROM qualification_jobs WHERE plan_id=? AND provider=? AND candidate<? AND status NOT IN ('failed','skipped','done','blocked')",(plan['id'],row['provider'],row['candidate'])).fetchone()
                if prior:continue
                if db.execute("SELECT 1 FROM qualification_jobs WHERE plan_id=? AND provider=? AND candidate=? AND part<? AND status IN ('pending','retry','running')",(plan['id'],row['provider'],row['candidate'],row['part'])).fetchone():continue
                p=db.execute('SELECT revision FROM qualification_providers WHERE plan_id=? AND provider=?',(plan['id'],row['provider'])).fetchone()
                if p[0]!=self.hybrid.credential_revision(row['provider']):
                    db.execute("UPDATE qualification_providers SET stage='blocked',reason='credential_changed' WHERE plan_id=? AND provider=?",(plan['id'],row['provider']));continue
                db.execute("UPDATE qualification_jobs SET status='running',lease=? WHERE id=?",(now+120,row['id']))
                job=dict(row);job['capability']=json.loads(job['capability']);job['case_indexes']=json.loads(job['case_indexes']);return job
            db.execute("UPDATE qualification_providers SET stage='finished',reason=COALESCE(reason,'No remaining executable evaluation jobs') WHERE plan_id=? AND stage='testing' AND NOT EXISTS(SELECT 1 FROM qualification_jobs j WHERE j.plan_id=qualification_providers.plan_id AND j.provider=qualification_providers.provider AND j.status IN ('pending','retry','running'))",(plan['id'],))
            self._complete(db,plan['id'])
        return None
    def admit(self,job,bound):
        from email_agent.application.model_errors import ModelFailure
        with self.hybrid.connect() as db:
            p=json.loads(db.execute('SELECT payload FROM qualification_plans WHERE id=?',(job['plan_id'],)).fetchone()[0])
            n,inp,out=db.execute("SELECT COUNT(*),COALESCE(SUM(input_bound),0),COALESCE(SUM(output_bound),0) FROM hybrid_attempts WHERE task_key LIKE ?",('qual:'+job['plan_id']+':%',)).fetchone()
            candidate=db.execute("SELECT COUNT(*) FROM hybrid_attempts a JOIN qualification_jobs j ON substr(a.task_key,1,length('qual:'||j.plan_id||':'||j.id||':try:'))='qual:'||j.plan_id||':'||j.id||':try:' WHERE j.plan_id=? AND j.provider=? AND j.candidate=?",(job['plan_id'],job['provider'],job['candidate'])).fetchone()[0]
            if not p.get('free_until_quota') and (n>=p['max_requests'] or inp+bound>p['input_tokens'] or out+2048>p['output_tokens'] or candidate>=20):raise ModelFailure('qualification_budget_exhausted','Approved qualification bound reached; remaining work retained')
    def bind(self,job,attempt):
        with self.hybrid.connect() as db:db.execute('UPDATE qualification_jobs SET attempt_id=? WHERE id=?',(attempt,job['id']))
    def defer_job(self,job,reason):
        with self.hybrid.connect() as db:
            due=time.time()+15
            if 'Shared application' in reason and self.hybrid.shared_quota:
                from datetime import datetime
                due=datetime.fromisoformat(self.hybrid.shared_quota.status()['resets_at']).timestamp()
            db.execute("UPDATE qualification_jobs SET status='pending',due=?,lease=0,error=? WHERE id=?",(due,reason,job['id']))
            db.execute('UPDATE qualification_providers SET reason=?,due=? WHERE plan_id=? AND provider=?',(reason,due,job['plan_id'],job['provider']))
    def finish_job(self,job,result,attempt_id):
        with self.hybrid.connect() as db:
            db.execute("UPDATE qualification_jobs SET status='done',result=?,attempt_id=COALESCE(?,attempt_id),attempts=attempts+1,lease=0,error=NULL WHERE id=?",(json.dumps(result),attempt_id,job['id']))
            db.execute("UPDATE qualification_providers SET reason=NULL,due=0 WHERE plan_id=? AND provider=? AND stage='testing'",(job['plan_id'],job['provider']))
    def fail_job(self,job,failure,attempt_id):
        transient=failure.state in ('provider_rate_limited','provider_unavailable','network_unavailable')
        with self.hybrid.connect() as db:
            current=db.execute('SELECT attempts FROM qualification_jobs WHERE id=?',(job['id'],)).fetchone()[0]
            if job['provider'] in ('groq','openrouter','cerebras','mistral') and failure.http_status==400 and (getattr(failure,'safe_context',{}).get('code')=='json_validate_failed' or getattr(failure,'safe_context',{}).get('reason_class')=='schema_compatibility' or getattr(failure,'safe_context',{}).get('reason_class')=='unsupported_request_option' and bool(set(getattr(failure,'safe_context',{}).get('rejected_fields',[])) & {'json_schema','response_format'})) and job['capability'].get('output_mode','json_schema')=='json_schema':
                cap=dict(job['capability'],output_mode='json_object')
                db.execute("UPDATE qualification_jobs SET status='retry',capability=?,attempts=attempts+?,attempt_id=?,lease=0,due=?,error='native_schema_rejected_json_mode_recovery' WHERE id=?",(json.dumps(cap),int(bool(attempt_id)),attempt_id,time.time()+60,job['id']))
                db.execute("UPDATE qualification_jobs SET capability=? WHERE plan_id=? AND provider=? AND candidate=? AND status IN ('pending','retry')",(json.dumps(cap),job['plan_id'],job['provider'],job['candidate']))
                db.execute("INSERT INTO hybrid_events(event,provider,model,detail) VALUES('qualification_wire_recovery',?,?,?)",(job['provider'],job['model'],'One schema-to-JSON recovery within approved synthetic scope; full backend validation unchanged'))
                return
            p=json.loads(db.execute('SELECT payload FROM qualification_plans WHERE id=?',(job['plan_id'],)).fetchone()[0])
            continuous=p.get('free_until_quota',False)
            retry=transient and (continuous and failure.state=='provider_rate_limited' or current<1)
            retry_after=getattr(failure,'retry_after',None)
            due=time.time()+(max(1,retry_after)+1 if retry_after is not None else min(3600,60*2**min(current+1,6)))
            db.execute('UPDATE qualification_jobs SET status=?,attempts=attempts+?,attempt_id=COALESCE(?,attempt_id),lease=0,due=?,error=? WHERE id=?',('retry' if retry else 'failed',int(bool(attempt_id)),attempt_id,due,failure.state,job['id']))
            if failure.state=='authentication_failed':
                db.execute("UPDATE qualification_providers SET stage='blocked',reason=? WHERE plan_id=? AND provider=?",(failure.state,job['plan_id'],job['provider']))
            if transient:
                db.execute('UPDATE qualification_providers SET due=?,stage=?,reason=? WHERE plan_id=? AND provider=?',(0 if not retry and failure.state in ('provider_unavailable','network_unavailable') else due,'testing' if retry or failure.state in ('provider_unavailable','network_unavailable') else 'blocked',failure.state,job['plan_id'],job['provider']))
                if not retry and failure.state in ('provider_unavailable','network_unavailable'):
                    db.execute("UPDATE qualification_jobs SET status='skipped',error=? WHERE plan_id=? AND provider=? AND candidate=? AND status IN ('pending','retry')",(failure.state,job['plan_id'],job['provider'],job['candidate']))
            else:
                db.execute("UPDATE qualification_jobs SET status='skipped',error=? WHERE plan_id=? AND provider=? AND candidate=? AND status IN ('pending','retry')",(failure.state,job['plan_id'],job['provider'],job['candidate']))
    def refresh_task_capabilities(self):
        """Evaluate independent tasks from stored real outputs; no new inference or consent."""
        version='independent-routine-capabilities-v1'
        activated=[]
        with self.hybrid.connect() as db:
            certificates=db.execute('SELECT c.*,p.payload plan FROM qualification_certificates c JOIN qualification_plans p ON p.id=c.plan_id ORDER BY c.created_at').fetchall()
        for certificate in certificates:
            old=json.loads(certificate['payload']);plan=json.loads(certificate['plan'])
            policy=next((p for p in plan['providers'] if p['provider']==certificate['provider']),None)
            if not policy or policy['revision']!=self.hybrid.credential_revision(certificate['provider']):continue
            with self.hybrid.connect() as db:
                if db.execute("SELECT 1 FROM hybrid_events WHERE event='task_capabilities_evaluated' AND provider=? AND model=? AND json_extract(detail,'$.plan')=? AND json_extract(detail,'$.version')=?",(certificate['provider'],certificate['model'],certificate['plan_id'],version)).fetchone():continue
                jobs=db.execute("SELECT * FROM qualification_jobs WHERE plan_id=? AND provider=? AND model=? AND status='done' AND result IS NOT NULL ORDER BY part",(certificate['plan_id'],certificate['provider'],certificate['model'])).fetchall()
            if not jobs:continue
            results=[json.loads(j['result']) for j in jobs]
            summary=summarize(self.cases,[o for r in results for o in r['outcomes']],results,False)
            # Keep original global certificates unchanged and archive the independent assessment.
            with self.hybrid.connect() as db:db.execute("INSERT INTO hybrid_events(event,provider,model,detail) VALUES('task_capabilities_evaluated',?,?,?)",(certificate['provider'],certificate['model'],json.dumps(dict(plan=certificate['plan_id'],version=version,category=summary['category_quality'],tasks=summary['task_quality']))))
            if not summary['quality_qualified']:continue
            cap=replace(ModelCapability(**json.loads(jobs[-1]['capability'])),generation=True,structured=True,validated=True,quality=summary['category_quality'],workflow_quality=summary['workflow_quality'],task_quality={lang:dict(v,taxonomy=0) for lang,v in summary['task_quality'].items()},evaluation_version=version,latency=summary['mean_seconds'] or 60)
            self.hybrid.register(cap,time.time()+7*86400)
            self.hybrid.record_model_check('personal',cap,dict(passed=True,source=version,original_contract=old.get('contract')),certificate['plan_id'])
            activated.append(dict(provider=cap.provider,model=cap.model,quality=cap.quality))
        return activated

    def certify(self,plan,job):
        with self.hybrid.connect() as db:
            rows=db.execute("SELECT result,status FROM qualification_jobs WHERE plan_id=? AND provider=? AND model=? ORDER BY part",(plan['id'],job['provider'],job['model'])).fetchall()
        results=[json.loads(r['result']) for r in rows if r['result']]
        outcomes=[o for r in results for o in r['outcomes']]
        summary=summarize(self.cases,outcomes,results,all(r['status']=='done' for r in rows))
        for lang in summary['task_quality']:
            taxonomy=[r['taxonomy'] for r in results if r.get('taxonomy') is not None and r['language']==lang]
            summary['task_quality'][lang]['taxonomy']=float(bool(taxonomy) and all(taxonomy))
        summary.update(provider=job['provider'],model=job['model'],plan_id=plan['id'],contract=plan['contract'])
        with self.hybrid.connect() as db:db.execute('INSERT OR REPLACE INTO qualification_certificates VALUES(?,?,?,?,?)',(plan['id'],job['provider'],job['model'],json.dumps(summary),time.time()))
        if summary['quality_qualified']:
            cap=replace(ModelCapability(**job['capability']),generation=True,structured=True,validated=True,quality=summary['category_quality'],workflow_quality=summary['workflow_quality'],task_quality=summary['task_quality'],evaluation_version=plan['contract'],latency=summary['mean_seconds'] or 60)
            self.hybrid.register(cap,time.time()+7*86400)
            self.hybrid.record_model_check('personal',cap,dict(passed=True,source='gold_multilingual_qualification',contract=plan['contract']),plan['id'])
            with self.hybrid.connect() as db:db.execute("UPDATE hybrid_tasks SET status='waiting_cloud',due=0,reason='New qualified model available' WHERE status='blocked' AND reason IN ('Complete content cannot fit any available model context; not truncated','No qualified model') AND EXISTS(SELECT 1 FROM hybrid_standing_access a WHERE a.account_id=hybrid_tasks.account_id AND a.provider=? AND a.enabled=1)",(cap.provider,))
        with self.hybrid.connect() as db:
            coverage={lang for v in db.execute('SELECT payload FROM qualification_certificates WHERE plan_id=? AND provider=?',(plan['id'],job['provider'])) for lang,values in json.loads(v[0])['languages'].items() if values['qualified']}
            if coverage>={'en','he','ar'}:
                db.execute("UPDATE qualification_jobs SET status='skipped',error='provider_already_qualified' WHERE plan_id=? AND provider=? AND status='pending'",(plan['id'],job['provider']))
                db.execute("UPDATE qualification_providers SET stage='ready',reason=NULL WHERE plan_id=? AND provider=?",(plan['id'],job['provider']))
            elif not db.execute("SELECT 1 FROM qualification_jobs WHERE plan_id=? AND provider=? AND status IN ('pending','running','retry')",(plan['id'],job['provider'])).fetchone():db.execute("UPDATE qualification_providers SET stage='finished',reason='Evaluation finished; inspect language-specific capabilities' WHERE plan_id=? AND provider=?",(plan['id'],job['provider']))
            self._complete(db,plan['id'])
    def _complete(self,db,id):
        if db.execute("SELECT 1 FROM qualification_providers WHERE plan_id=? AND stage IN ('metadata','discovering','testing')",(id,)).fetchone():return
        db.execute("UPDATE qualification_plans SET status='completed' WHERE id=? AND status='approved'",(id,))
