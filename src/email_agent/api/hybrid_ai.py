"""Loopback-only provider management; credentials never enter response DTOs."""
from dataclasses import replace
from contextlib import nullcontext
from importlib.resources import files
import json
import os
import time
import uuid
from decimal import Decimal
from fastapi import HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,SecretStr,StrictBool
from email_agent.api.message_management import authorize_local_change
from email_agent.application.hybrid_ai import ModelCapability,ACTIVE_PROVIDERS,SETUP_PROVIDERS
from email_agent.application.hybrid_evaluation import SemanticEvaluation
from email_agent.application.hybrid_organizer import HybridOrganizer
from email_agent.infrastructure.ai.hybrid_providers import LocalModelProvider,OpenAICompatibleProvider,GeminiProvider,local_endpoint
from email_agent.infrastructure.database.hybrid_store import SQLiteHybridStore,LocalSecretStore
from email_agent.infrastructure.gmail.request_policy import sync_lock,SyncBusyError
from email_agent.workers.fill_content import fill_lock


class StrictRequest(BaseModel):model_config=ConfigDict(extra='forbid')
class Preference(StrictRequest):preference:str
class ModelSelection(StrictRequest):
    models:list[str]=Field(max_length=12)

class ProviderConfig(StrictRequest):
    enabled:StrictBool
    connect_metadata:StrictBool=False
    key:SecretStr|None=None
    endpoint:str|None=Field(default=None,max_length=250)
    trusted_lan:StrictBool=False
class Discovery(StrictRequest):
    approved:StrictBool
    request_id:str=Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
class Evaluation(StrictRequest):
    approved:StrictBool
    model:str=Field(min_length=1,max_length=180)
    free_account_confirmed:StrictBool=False
    request_id:str=Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')
class Grant(StrictRequest):
    approved:StrictBool
    models:list[str]=Field(min_length=1,max_length=12)
    requests:int=Field(ge=1,le=10000)
    input_tokens:int=Field(ge=1,le=10_000_000)
    output_tokens:int=Field(ge=1,le=2_000_000)
    messages:int=Field(ge=1,le=10000)
    free_only_confirmed:StrictBool
    request_id:str=Field(min_length=1,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')


class PaymentPreference(StrictRequest):
    approved:StrictBool
    free_only:StrictBool

class PaidAuthorization(Grant):
    free_only_confirmed:StrictBool=False
    paid_approved:StrictBool
    input_price:Decimal=Field(gt=0,le=100000)
    output_price:Decimal=Field(gt=0,le=100000)
    max_usd:Decimal=Field(gt=0,le=10000)


def register_hybrid_routes(app,root,organizer,dispatcher,automation,legacy_settings,shared_quota=None):
    from email_agent.infrastructure.ai.resource_monitor import resources
    store=SQLiteHybridStore(organizer.messages.database_path,shared_quota,resources.local_allowed);store.initialize()
    secrets=LocalSecretStore(root/'data'/'hybrid-secrets')
    environment={'gemini':'GEMINI_API_KEY','groq':'GROQ_API_KEY','mistral':'MISTRAL_API_KEY','zai':'ZAI_API_KEY'}
    def key(provider):return secrets.get(provider) or os.environ.get(environment.get(provider,''),'')
    for name in environment:store.observe_credential(name,key(name))
    with store.connect() as db:
        prior_probes=db.execute('SELECT * FROM hybrid_probe_runs WHERE result IS NOT NULL ORDER BY rowid').fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_probe_runs'").fetchone() else []
    for prior in prior_probes:
        if prior['provider'] not in ACTIVE_PROVIDERS:continue
        result=json.loads(prior['result'])
        if prior['revision']!=store.credential_revision(prior['provider']) or not result.get('inference_completed') or not result.get('structured_valid'):continue
        existing=next((m for m in store.models() if m['provider']==prior['provider'] and m['model']==prior['model']),None)
        if existing and not store.model_checked(prior['account_id'],prior['provider'],prior['model']):
            cap=ModelCapability(**existing['payload'])
            store.register(replace(cap,generation=True,structured=True),existing['free_until'])
            store.record_model_check(prior['account_id'],cap,dict(passed=True,source='persisted_synthetic_schema',semantic_correct=result.get('semantic_correct'),quality_qualified=False),prior['id'])
    # Reuse provenance from the exact legacy grant, not the whole model list.
    # Successful historical generation proves compatibility, not benchmark accuracy.
    if key('gemini') and store.legacy_credentials_unchanged('gemini'):
        with store.connect() as db:
            evidence=db.execute("SELECT g.account_id,c.model,COUNT(*) n FROM organizer_campaigns c JOIN automation_ai_attempts a ON a.campaign_id=c.id JOIN automation_ai_grants g ON g.id=a.grant_id WHERE a.kind='generate' AND a.status='done' GROUP BY g.account_id,c.model").fetchall()
        for proof in evidence:
            existing=next((m for m in store.models() if m['provider']=='gemini' and m['model']==proof['model']),None)
            if existing and not store.model_checked(proof['account_id'],'gemini',proof['model']):
                cap=ModelCapability(**existing['payload'])
                store.register(replace(cap,generation=True,structured=True),existing['free_until'])
                store.record_model_check(proof['account_id'],cap,dict(passed=True,source='persisted_legacy_generation',successful_requests=proof['n'],quality_qualified=cap.validated),'legacy-provenance')
    def provider(name):
        if name not in ACTIVE_PROVIDERS:raise ValueError('Provider permanently retired from active use; credentials and history retained')
        config=store.provider(name)
        if name=='ollama':return LocalModelProvider(config['endpoint'],key(name),bool(config['trust_lan']),context=max(4096,min(32768,int(os.getenv('POSTMARK_LOCAL_CONTEXT','16384')))),resource_check=resources.local_allowed)
        if name=='siliconflow' and config['endpoint'] is None:raise ValueError('Choose SiliconFlow International (.com) or China (.cn) before connecting')
        if not key(name):raise ValueError('Configure the provider credential privately')
        return GeminiProvider(key(name)) if name=='gemini' else OpenAICompatibleProvider(name,key(name),endpoint=config['endpoint'])
    def legacy(account):
        if not store.legacy_credentials_unchanged('gemini') or not store.provider('gemini')['enabled'] or not store.account_allows(account,'gemini',True):return dict(status='cloud_consent_revoked')
        grant=automation.current_grant(account)
        if not grant:return dict(status='waiting_consent',message='Local organization continues; cloud account consent is required')
        if not store.model_allowed(account,'gemini',grant['model']):return dict(status='model_not_selected')
        if not store.healthy('gemini',grant['model']):return dict(status='cloud_cooling_down',message='Cloud AI is temporarily unavailable. Local organization continues.')
        result=dispatcher.step(account);store.observe_legacy(account,result,grant['model']);return result
    if any(automation.current_grant(account) and legacy_settings(account).get('processing_consent') for account in ('personal','university')):store.adopt_legacy()
    def maintain_local():
        config=store.provider('ollama')
        if not config['enabled'] or (config['last_checked'] or 0)>time.time()-300 or not any(m['provider']=='ollama' and m['payload']['validated'] for m in store.models()):return
        try:
            installed={m['model']:m for m in provider('ollama').discover()}
            for old in store.models():
                if old['provider']!='ollama':continue
                cap=ModelCapability(**old['payload']);now=installed.get(cap.model)
                if not now or now['fingerprint']!=cap.fingerprint:store.register(replace(cap,validated=False,quality={}))
            state='metadata_connected'
        except Exception:state='offline'
        with store.connect() as db:db.execute('UPDATE hybrid_providers SET last_state=?,last_checked=? WHERE provider=\'ollama\'',(state,time.time()))
    hybrid=HybridOrganizer(store,organizer,provider,legacy,maintain_local)
    def scope_progress(account_id,ids,state,model):
        from email_agent.application.semantic_organizer import row_fingerprint
        store.reconcile_other(account_id)
        for id in ids:
            if state=='cloud_processing':store.task(account_id,id,'cloud_processing','Gemini processing approved scope','gemini',model);continue
            row=organizer.store.message_row(account_id,id);frame=(row or {}).get('semantic') or {}
            accepted=row and row.get('input_fingerprint')==row_fingerprint(row) and frame.get('source')=='gemini' and frame.get('input_coverage',{}).get('body_complete') and frame.get('understood') and not frame.get('analysis_reason')
            status=('classified' if row['decision']['category_id']!='other' else 'analyzed_other') if accepted else 'waiting_cloud' if row and row['content_complete'] else 'waiting_body'
            store.task(account_id,id,status,'Persisted cloud interpretation' if accepted else 'Further content or semantic evidence needed','gemini',model,due=time.time()+300 if not accepted else 0)
    organizer.on_scope=scope_progress
    app.state.hybrid_organizer=hybrid;app.state.hybrid_store=store

    def account(value):
        if value not in ('personal','university'):raise HTTPException(400,'Unknown account')
    def execute(operation,*args):
        try:return operation(*args)
        except (ValueError,PermissionError,LookupError) as exc:raise HTTPException(400,str(exc)) from exc
        except SyncBusyError as exc:raise HTTPException(409,'Another bounded worker is active; saved checkpoints are retained') from exc
        except Exception as exc:
            from email_agent.application.model_errors import classify_error
            failure=classify_error(exc)
            raise HTTPException(502,'Provider check failed: '+failure.state) from exc

    @app.get('/api/hybrid/{account_id}')
    def overview(account_id:str):
        account(account_id);result=store.overview(account_id)
        result['providers']=[p for p in result['providers'] if p['provider'] in SETUP_PROVIDERS]
        result['models']=[m for m in result['models'] if m['provider'] in SETUP_PROVIDERS]
        result['models'].sort(key=lambda m:(m['provider'],m['provider']=='nvidia' and 'nemotron' not in m['model'].lower(),m['model']))
        grant=automation.current_grant(account_id)
        old=legacy_settings(account_id);policy=old.get('usage',{}).get('provider_policy',{})

        for model in result['models']:model['eligible']=bool(model['payload']['validated'] and model['payload']['structured'] and store.payment_allowed(account_id,ModelCapability(**model['payload'])) and (model['provider'] in ('ollama','openai') or model['free_until']>time.time()))
        with store.connect() as db:
            verified={(r['provider'],r['model'],r['credential_revision']):bool(json.loads(r['result']).get('passed')) for r in db.execute("SELECT provider,model,credential_revision,result FROM hybrid_model_checks ORDER BY created_at")}
        route_providers={r['provider'] for r in result['eligible_routes']}
        result['application_request_budget']=shared_quota.status() if shared_quota else None
        for item in result['providers']:item['configured']=bool(key(item['provider'])) if item['provider']!='ollama' else True
        for item in result['providers']:
            name=item['provider']
            with store.connect() as db:
                choice=db.execute('SELECT models FROM hybrid_model_preferences WHERE account_id=? AND provider=?',(account_id,name)).fetchone()
            revision=store.credential_revision(name)
            with store.connect() as db:
                key_test=db.execute("SELECT 1 FROM hybrid_discovery WHERE provider=? AND status='done' AND credential_revision=? LIMIT 1",(name,revision)).fetchone() if 'credential_revision' in {r[1] for r in db.execute('PRAGMA table_info(hybrid_discovery)')} else None
            item['model_checks']={m['model']:verified.get((name,m['model'],revision),False) for m in result['models'] if m['provider']==name}
            item['paid_authorized']=any(store.paid_scope(account_id,ModelCapability(**m['payload'])) for m in result['models'] if m['provider']==name)
            item['key_validated']=bool(item['configured'] and key_test and item['last_state']=='metadata_connected' and not any(h['provider']==name and h['last_error']=='authentication_failed' and h['disabled'] for h in result['health'])) if name!='ollama' else None
            item['selected_models']=json.loads(choice[0]) if choice else []
            item['account_authorized']=name=='ollama' and not item['trust_lan'] or bool(store.account_allows(account_id,name) and (store.standing(account_id,name) or any(g['provider']==name and g['enabled'] for g in result['grants']) or name=='gemini' and grant and old.get('processing_consent')))
            item['standing_free_access']=store.standing(account_id,name)
            with store.connect() as db:
                discovery=db.execute("SELECT result FROM hybrid_discovery WHERE provider=? AND credential_revision=? AND status='done' ORDER BY rowid DESC LIMIT 1",(name,revision)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_discovery'").fetchone() else None
            item['discovery_info']=json.loads(discovery[0]).get('provider_info',{}) if discovery and discovery[0] else {}
            recent=bool(item['last_checked'] and item['last_checked']>time.time()-86400)
            healthy=not any(h['provider']==name and (h['model']=='*' or h['last_error'] in ('network_unavailable','provider_unavailable')) and (h['disabled'] or h['until']>time.time()) for h in result['health'])
            item['routing_active']=bool(item['enabled'] and (result['preference']!='local' or name=='ollama') and item['account_authorized'] and automation.enabled(account_id) and organizer.store.local_policy(account_id)['enabled'] and name in route_providers)
            qualified=any(m['provider']==name and m['payload']['validated'] and any(v>=.9 for v in m['payload'].get('quality',{}).values()) for m in result['models'])
            with store.connect() as db:
                testing=db.execute("SELECT 1 FROM qualification_providers p JOIN qualification_plans q ON q.id=p.plan_id WHERE q.status='approved' AND p.provider=? AND p.stage IN ('metadata','discovering','testing') LIMIT 1",(name,)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='qualification_plans'").fetchone() else None
            item['readiness']='Not configured' if not item['configured'] else 'Ready' if item['routing_active'] else 'Disabled' if not item['enabled'] else 'Testing' if testing else 'Available later' if not healthy else 'Awaiting account permission' if not item['account_authorized'] else 'Idle' if qualified and name in route_providers else 'Available later' if qualified else 'Needs qualification'
            item['connection']='Not configured' if not item['configured'] else 'Temporarily unavailable' if not healthy or item['last_state']=='offline' else 'Connected' if recent and item['last_state']=='metadata_connected' else 'Not tested'
            if name=='nvidia':stage,action='Free Developer / Evaluation','Synthetic evaluation only; real mailbox routing is not permitted by the developer-endpoint terms'
            elif not item['configured']:stage,action='Needs a key','Add API key'
            elif name=='sambanova':stage,action='No usable free model','Keep disabled; no verified free inference entitlement'
            elif name=='cloudflare' and not (item['discovery_info'].get('account_free_verified') and item['discovery_info'].get('verified_at',0)>time.time()-86400):stage,action='Needs free-tier verification','Account-scoped Billing Read is the documented subscription-read permission. Verify Workers Free, then test connection; no Billing Write or payment activation is required'
            elif name=='siliconflow' and not item['key_validated']:stage,action='Connection not verified','Save the key and connect to verify metadata access. No inference is sent'
            elif name=='siliconflow' and not any(m['provider']==name and store.payment_allowed(account_id,ModelCapability(**m['payload'])) for m in result['models']):stage,action='No verified free model','Check account access and current zero-price eligibility; no paid fallback'
            elif name=='siliconflow' and not qualified:stage,action='Connected; not qualified','No compatible quality evidence for this provider/model yet. No automatic tests or mailbox submission'
            elif not qualified:stage,action='Needs model qualification','Approve the bounded synthetic qualification plan; a saved key does not authorize mail'
            elif not item['account_authorized']:stage,action='Needs account permission','Authorize this account after reviewing qualified language/task coverage'
            elif not item['enabled']:stage,action='Disabled','Enable provider; existing account permissions stay unchanged'
            elif not healthy:stage,action='Temporarily rate-limited','Wait for the recorded cooldown; other qualified providers continue'
            elif item['routing_active']:stage,action='Ready for email processing','Eligible queued work resumes automatically'
            else:stage,action='Waiting for eligible work','Check task/language fit and approved capacity in Advanced'
            item.update(setup_status=stage,next_action=action)
            with store.connect() as db:
                evaluation=db.execute("SELECT model,status,result FROM hybrid_evaluation_runs WHERE account_id=? AND provider=? AND revision=? ORDER BY rowid DESC LIMIT 1",(account_id,name,revision)).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_evaluation_runs'").fetchone() else None
            ev=json.loads(evaluation['result']) if evaluation and evaluation['result'] else {}
            item['last_evaluation']=dict(model=evaluation['model'],status=evaluation['status'],attempted=ev.get('attempted'),cases=ev.get('cases'),blocked_reason=ev.get('blocked_reason'),quality_qualified=ev.get('quality_qualified',False)) if evaluation else None


        result['local_policy']=organizer.store.local_policy(account_id)
        result['organization_enabled']=automation.enabled(account_id)
        if not result['organization_enabled'] or not result['local_policy']['enabled']:result['cloud']='Limited'
        result['other']=store.other_progress(account_id,reconcile=False)
        with store.connect() as db:
            states={r['status']:r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM hybrid_tasks WHERE account_id=? GROUP BY status',(account_id,))}
            ready_cloud=db.execute("SELECT COUNT(*) FROM hybrid_tasks WHERE account_id=? AND status='waiting_cloud' AND reason='Ready for eligible provider'",(account_id,)).fetchone()[0]
        status='paused' if not result['organization_enabled'] or not result['local_policy']['enabled'] else 'processing' if states.get('local_processing',0)+states.get('cloud_processing',0) else 'queued' if states.get('ready_local',0) or ready_cloud else 'waiting' if any(states.get(s,0) for s in ('waiting_body','waiting_cloud','blocked','failed')) else 'idle'
        result['work']=dict(status=status,states=states)
        return result

    app.state.ai_status_snapshot=overview

    @app.get('/api/usage')
    def usage_report(account:str='personal',start:float|None=None,end:float|None=None,provider:str|None=None,model:str|None=None,task:str|None=None,offset:int=0):
        from email_agent.infrastructure.database.usage_report import report
        try:
            result=report(store,account,start,end,provider,model,task,offset)
            result['resources']=resources.snapshot()
            return result
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/hybrid/{account_id}/capacity')
    def capacity_audit(account_id:str):
        account(account_id)
        from email_agent.infrastructure.database.capacity_audit import capacity_snapshot
        result=capacity_snapshot(store,account_id)
        def model_memory():
            local=provider('ollama');local.http.timeout=2
            return local.http.request('/api/ps')
        result['resources']['models']=resources.model_snapshot(model_memory)
        return result

    @app.get('/api/hybrid/{account_id}/routing')
    def routing(account_id:str):
        account(account_id)
        from email_agent.infrastructure.database.routing_diagnostics import routing_diagnostics
        result=routing_diagnostics(store,account_id,lambda name:name=='ollama' or bool(key(name)))
        result['legacy_budget']=automation.ai_progress(account_id)
        result['legacy_scope_fits']=bool((g:=automation.current_grant(account_id)) and automation.minimum_scope_fits(g))
        if g and store.legacy_credentials_unchanged('gemini') and store.account_allows(account_id,'gemini',True) and legacy_settings(account_id).get('processing_consent'):
            gemini=next(p for p in result['providers'] if p['provider']=='gemini')
            gemini['reasons']=[r for r in gemini['reasons'] if r!='account_consent_or_budget_missing']
            gemini['legacy_authorized']=True
            if not result['legacy_scope_fits'] and not gemini['routable']:gemini['reasons'].append('legacy_budget_cannot_fit_next_complete_request')
        return result

    @app.get('/api/hybrid/{account_id}/other')
    def other(account_id:str):account(account_id);return store.other_progress(account_id)

    @app.get('/api/hybrid/{account_id}/tests')
    def tests(account_id:str):
        account(account_id)
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_probe_runs'").fetchone():return dict(tests=[])
            results=[]
            for row in db.execute('SELECT * FROM hybrid_probe_runs WHERE account_id=? ORDER BY rowid DESC LIMIT 30',(account_id,)):
                item={k:row[k] for k in ('provider','model','revision','status')}
                attempt=db.execute("SELECT id,status,error FROM hybrid_attempts WHERE account_id=? AND provider=? AND substr(task_key,1,?)=? ORDER BY id DESC LIMIT 1",(account_id,row['provider'],len(row['id'])+1,row['id']+':')).fetchone()
                item['result']=json.loads(row['result']) if row['result'] else dict(status='unconfirmed',requests=int(bool(attempt)),reason='Previous Postmark preflight interrupted; no provider request recorded' if not attempt else 'Attempt recorded; no completed test result. Not replayed.')
                item['inference_completed']=bool(attempt and attempt['status']=='done')
                results.append(item)
        return dict(tests=results)
    @app.post('/api/hybrid/{account_id}/preference')
    def preference(account_id:str,payload:Preference,request:Request):
        authorize_local_change(request);account(account_id)
        return dict(preference=execute(store.preference,account_id,payload.preference))
    @app.post('/api/hybrid/{account_id}/providers/{name}')
    def configure(account_id:str,name:str,payload:ProviderConfig,request:Request):
        authorize_local_change(request);account(account_id);execute(store.provider,name)
        if name not in SETUP_PROVIDERS:raise HTTPException(400,'Provider retired; saved credentials and history are retained')
        if payload.key:
            execute(secrets.put,name,payload.key.get_secret_value())
            store.observe_credential(name,payload.key.get_secret_value())
        if name=='ollama' and payload.endpoint:execute(local_endpoint,payload.endpoint,key(name),payload.trusted_lan)
        elif payload.endpoint:raise HTTPException(400,'Cloud origins are fixed; custom credential destinations are forbidden')
        if name!='ollama' and payload.enabled and not key(name):raise HTTPException(400,'A private credential is required')
        execute(store.configure,name,False if payload.key and name!='ollama' else payload.enabled,payload.endpoint,payload.trusted_lan)
        if payload.connect_metadata:
            return discover(account_id,name,Discovery(approved=True,request_id=uuid.uuid4().hex),request)
        return dict(saved=True,provider=name,configured=bool(key(name)) if name!='ollama' else True)
    @app.post('/api/hybrid/{account_id}/providers/{name}/models')
    def select_models(account_id:str,name:str,payload:ModelSelection,request:Request):
        authorize_local_change(request);account(account_id)
        return execute(store.select_models,account_id,name,payload.models)

    @app.post('/api/hybrid/{account_id}/providers/{name}/discover')
    def discover(account_id:str,name:str,payload:Discovery,request:Request):
        authorize_local_change(request);account(account_id)
        if name not in SETUP_PROVIDERS:raise HTTPException(400,'Provider retired from active use')
        if not payload.approved:raise HTTPException(400,'Approve up to3 metadata requests; no generation or mail submission')
        # Exact operation id is persistent; repeated clicks/restarts never
        # replay model discovery behind a successful or unknown response.
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_discovery(id TEXT PRIMARY KEY,account_id TEXT,provider TEXT,status TEXT,requests INTEGER NOT NULL DEFAULT 0,result TEXT)')
            if 'credential_revision' not in {r[1] for r in db.execute('PRAGMA table_info(hybrid_discovery)')}:db.execute('ALTER TABLE hybrid_discovery ADD COLUMN credential_revision INTEGER NOT NULL DEFAULT 0')
            old=db.execute('SELECT * FROM hybrid_discovery WHERE id=?',(payload.request_id,)).fetchone()
            if old:
                if (old['account_id'],old['provider'],old['credential_revision'])!=(account_id,name,store.credential_revision(name)):raise HTTPException(400,'Metadata scope cannot change account/provider')
                return json.loads(old['result']) if old['result'] else dict(status=old['status'],requests=old['requests'])
            db.execute("INSERT INTO hybrid_discovery(id,account_id,provider,status,credential_revision) VALUES(?,?,?,'running',?)",(payload.request_id,account_id,name,store.credential_revision(name)))
        def reserve_metadata():
            with store.connect() as db:
                return db.execute('UPDATE hybrid_discovery SET requests=requests+1 WHERE id=? AND requests<3',(payload.request_id,)).rowcount==1
        try:selected=execute(provider,name)
        except HTTPException as exc:
            with store.connect() as db:db.execute("UPDATE hybrid_discovery SET status='failed',result=? WHERE id=?",(json.dumps({'error':exc.detail}),payload.request_id))
            raise
        if name in ('gemini','cloudflare'):selected.metadata_reserve=reserve_metadata
        elif not reserve_metadata():raise HTTPException(409,'Metadata scope exhausted')
        try:
            found=selected.discover();existing={(m['provider'],m['model']):m for m in store.models()}
            proven=[]
            for item in found:
                prior=existing.get((name,item['model']));oldcap=ModelCapability(**prior['payload']) if prior else None
                fingerprint=item.get('fingerprint','')
                if oldcap and oldcap.fingerprint==fingerprint:
                    cap=oldcap
                else:cap=ModelCapability(name,item['model'],min(2_000_000,max(2048,int(item.get('context') or 8192))),generation=bool(item.get('generation',True)),tools=bool(item.get('tools')),structured=bool(item.get('structured')),free=bool(item.get('free')),fingerprint=fingerprint,output_mode=item.get('output_mode','json_schema'),reasoning=bool(item.get('reasoning',False)))
                from email_agent.infrastructure.ai.free_catalog import verified_free
                free_proven=bool(item.get('free') or name=='gemini' and cap.free and verified_free(cap.model) or prior and cap==oldcap and cap.free and prior['free_until']>time.time())
                store.register(cap,time.time()+86400 if item.get('free') and cap.free or name=='gemini' and cap.free and verified_free(cap.model) else prior['free_until'] if prior and cap==oldcap else 0)
                if free_proven and store.reuse_discovered_model(account_id,cap,bool(oldcap and oldcap.fingerprint==fingerprint)):proven.append(cap.model)
            if proven and name in ACTIVE_PROVIDERS:
                store.select_models(account_id,name,[])  # Existing quality/latency router chooses; no user model guessing.
                store.configure(name,True)  # Account grants and credential-bound consent remain independent.

            result=dict(status='metadata_discovered',proven_models=proven,mailbox_permission=store.standing(account_id,name),models=len(found),provider_info=getattr(selected,'discovery_info',{}),note='Listing alone does not verify structured generation, free account policy or multilingual quality')
            with store.connect() as db:
                db.execute("UPDATE hybrid_discovery SET status='done',result=? WHERE id=?",(json.dumps(result),payload.request_id))
                db.execute("UPDATE hybrid_providers SET last_state='metadata_connected',last_checked=? WHERE provider=?",(time.time(),name))
            return result
        except Exception as exc:
            from email_agent.application.model_errors import classify_error
            failure=classify_error(exc)
            error=dict(error=str(exc) if isinstance(exc,ValueError) else failure.state,http_status=getattr(failure,'http_status',None),region=('international' if store.provider(name)['endpoint']=='https://api.siliconflow.com/v1' else 'china') if name=='siliconflow' else None)
            with store.connect() as db:
                db.execute("UPDATE hybrid_discovery SET status='failed',result=? WHERE id=?",(json.dumps(error),payload.request_id))
                db.execute("UPDATE hybrid_providers SET last_state='offline',last_checked=? WHERE provider=?",(time.time(),name))
            if name=='siliconflow':raise HTTPException(502,'SiliconFlow '+str(error['http_status'] or error['error'])+' on the selected '+str(error['region'])+' API; check the regional account/key. No other region was tried') from None
            return execute(lambda:(_ for _ in ()).throw(exc))

    @app.post('/api/hybrid/{account_id}/providers/{name}/grant')
    def grant(account_id:str,name:str,payload:Grant,request:Request):
        if name not in ACTIVE_PROVIDERS:raise HTTPException(400,'Provider retired; no new operations allowed')
        authorize_local_change(request);account(account_id)
        if name=='openai':raise HTTPException(400,'OpenAI requires a separate paid authorization; it can never inherit Free-only consent')
        if not payload.approved or not payload.free_only_confirmed:raise HTTPException(400,'Explicit provider/account data consent and Free-only finite budget required')
        return dict(grant=execute(store.grant,account_id,name,payload.models,payload.requests,payload.input_tokens,payload.output_tokens,payload.messages,'Settings explicit scope; complete-body processing and quality escalation; Free-only, no Gmail authority',payload.request_id))
    @app.post('/api/hybrid/{account_id}/providers/{name}/revoke')
    def revoke(account_id:str,name:str,request:Request):
        authorize_local_change(request);account(account_id);execute(store.revoke,account_id,name);return dict(revoked=True)

    @app.post('/api/hybrid/{account_id}/payment-preference')
    def payment_preference(account_id:str,payload:PaymentPreference,request:Request):
        authorize_local_change(request);account(account_id)
        if not payload.approved:raise HTTPException(400,'Explicit payment preference approval required')
        return dict(free_only=execute(store.free_only,account_id,payload.free_only))

    @app.post('/api/hybrid/{account_id}/providers/openai/paid-grant')
    def paid_grant(account_id:str,payload:PaidAuthorization,request:Request):
        authorize_local_change(request);account(account_id)
        if not payload.approved or not payload.paid_approved or payload.free_only_confirmed or len(payload.models)!=1:raise HTTPException(400,'Explicit paid and account-processing consent for exactly one model required')
        model=next((ModelCapability(**m['payload']) for m in store.models() if m['provider']=='openai' and m['model']==payload.models[0]),None)
        if not model:raise HTTPException(400,'Discover the exact model first')
        id=execute(store.authorize_paid,account_id,model,payload.requests,payload.input_tokens,payload.output_tokens,payload.messages,payload.input_price,payload.output_price,payload.max_usd,payload.request_id)
        return dict(grant=id,provider_enabled=bool(store.provider('openai')['enabled']),free_only=store.free_only(account_id))

    @app.post('/api/hybrid/{account_id}/providers/{name}/probe')
    def probe(account_id:str,name:str,payload:Evaluation,request:Request):
        if name not in ACTIVE_PROVIDERS:raise HTTPException(400,'Provider retired; no new operations allowed')
        authorize_local_change(request);account(account_id)
        if not payload.approved:raise HTTPException(400,'Approve one synthetic request; no mailbox text is submitted')
        found=next((m for m in store.models() if m['provider']==name and m['model']==payload.model),None)
        if not found:raise HTTPException(400,'Discover the model first')
        cap=ModelCapability(**found['payload'])
        if name=='openai' and not store.payment_allowed(account_id,cap):raise HTTPException(400,'Explicit OpenAI paid scope and payment-mode approval required')
        if name not in ('ollama','openai') and not payload.free_account_confirmed:raise HTTPException(400,'Explicitly verify the account/model free eligibility first')
        if name=='openrouter' and not cap.free:raise HTTPException(400,'Unknown or nonzero OpenRouter pricing is ineligible')
        if name in ('gemini','groq','cerebras','mistral'):cap=replace(cap,free=True)
        operation='probe:'+payload.request_id
        store.synthetic_scope(operation,account_id,cap)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_probe_runs(id TEXT PRIMARY KEY,account_id TEXT,provider TEXT,model TEXT,revision INTEGER,status TEXT,result TEXT)')
            old=db.execute('SELECT * FROM hybrid_probe_runs WHERE id=?',(operation,)).fetchone()
            if old:
                if (old['account_id'],old['provider'],old['model'],old['revision'])!=(account_id,name,cap.model,store.credential_revision(name)):raise HTTPException(400,'Probe scope cannot change account, credential or model')
                return json.loads(old['result']) if old['result'] else dict(status=old['status'],requests=0,note='Existing reservation retained; no replay')
            db.execute("INSERT INTO hybrid_probe_runs VALUES(?,?,?,?,?,'reserved',NULL)",(operation,account_id,name,cap.model,store.credential_revision(name)))
        def perform():
            with nullcontext():
                class OneRequest:
                    def analyze(self,model,system,data,schema):
                        from email_agent.application.hybrid_ai import organization_schema
                        bound=organization_schema(data,schema)
                        try:attempt=store.reserve(account_id,model,operation,[],len((system+json.dumps(data,ensure_ascii=False)+json.dumps(bound)).encode())+512,2048,synthetic_scope=operation)
                        except ValueError as exc:
                            from email_agent.application.model_errors import ModelFailure
                            failure=ModelFailure('inference_budget_blocked',str(exc));failure.safe_context={'stage':'reservation'};raise failure from exc
                        try:
                            result=provider(name).analyze(model,system,data,schema)
                            store.finish(attempt,'done',result);return result
                        except Exception as exc:
                            from email_agent.application.model_errors import classify_error
                            store.finish(attempt,'failed',error=classify_error(exc));raise
                cases=json.loads(files('email_agent.resources').joinpath('semantic_benchmark.json').read_text())[:1]
                result=SemanticEvaluation(OneRequest(),cap,cases).run()
                passed=bool(result['valid_schema']==1 and result['correct']==1 and not result['unsafe_promotional'])
                with store.connect() as db:
                    attempt=db.execute('SELECT id,status,error FROM hybrid_attempts WHERE account_id=? AND provider=? AND model=? AND task_key=? ORDER BY id DESC LIMIT 1',(account_id,name,cap.model,store._task_key(db,cap,operation))).fetchone()
                inference_completed=bool(attempt and attempt['status']=='done')
                summary=dict(status='passed' if passed else 'failed',passed=passed,source='synthetic_probe',provider=name,model=cap.model,request_id=payload.request_id,requests=int(bool(attempt)),inference_completed=inference_completed,structured_valid=result['valid_schema']==1,semantic_correct=result['correct']==1,reason=result['blocked_reason'] or ('semantic_validation_failed' if not passed else None),diagnostic=result.get('diagnostic'),issues=[i for o in result['outcomes'] for i in o['issues']],quality_qualified=False)
                summary['compatibility_passed']=bool(inference_completed and summary['structured_valid'])
                if not result['blocked_reason']:store.record_model_check(account_id,cap,dict(summary,passed=summary['compatibility_passed']),operation)
                if summary['compatibility_passed']:
                    store.register(replace(cap,generation=True,structured=True),found['free_until'])
                with store.connect() as db:db.execute("UPDATE hybrid_probe_runs SET status=?,result=? WHERE id=?",(summary['status'],json.dumps(summary),operation))
                return summary
        return execute(perform)

    @app.post('/api/hybrid/{account_id}/providers/{name}/evaluate')
    def evaluate(account_id:str,name:str,payload:Evaluation,request:Request):
        if name not in ACTIVE_PROVIDERS:raise HTTPException(400,'Provider retired; no new operations allowed')
        authorize_local_change(request);account(account_id)
        if not payload.approved:raise HTTPException(400,'Approve the40 synthetic-case evaluation; no mailbox bodies are sent')
        if name=='openai' and not store.payment_allowed(account_id,next((ModelCapability(**m['payload']) for m in store.models() if m['provider']==name and m['model']==payload.model),ModelCapability(name,payload.model,8192))):raise HTTPException(400,'Explicit paid scope and paid-mode authorization required')
        if name not in ('ollama','openai') and not payload.free_account_confirmed:raise HTTPException(400,'Verify this account/model free tier with billing disabled before evaluation')
        found=next((m for m in store.models() if m['provider']==name and m['model']==payload.model),None)
        if not found:raise HTTPException(400,'Discover the installed/available model first')
        cap=ModelCapability(**found['payload'])
        if name not in ('ollama','openai') and not cap.free:raise HTTPException(400,'This model has no verified free eligibility; a checkbox cannot make a paid or unknown-cost model free')
        base=execute(provider,name);operation='eval:'+payload.request_id
        if name=='openrouter' and not cap.free:raise HTTPException(400,'This model has no verified zero-price free eligibility')
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_evaluation_runs(id TEXT PRIMARY KEY,account_id TEXT,provider TEXT,model TEXT,status TEXT,result TEXT)')
            if 'revision' not in {r[1] for r in db.execute('PRAGMA table_info(hybrid_evaluation_runs)')}:db.execute('ALTER TABLE hybrid_evaluation_runs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            old=db.execute('SELECT * FROM hybrid_evaluation_runs WHERE id=?',(operation,)).fetchone()
            if old:
                if (old['account_id'],old['provider'],old['model'],old['revision'])!=(account_id,name,cap.model,store.credential_revision(name)):raise HTTPException(400,'Evaluation scope cannot change account or model')
                return json.loads(old['result']) if old['result'] else dict(status=old['status'],message='Evaluation already reserved; no hidden replay')
            db.execute("INSERT INTO hybrid_evaluation_runs(id,account_id,provider,model,status,result,revision) VALUES(?,?,?,?,'running',NULL,?)",(operation,account_id,name,cap.model,store.credential_revision(name)))
        class BudgetedProbe:
            def __init__(self):self.n=0
            def analyze(self,model,system,data,schema):
                self.n+=1
                if self.n>40:raise ValueError('Synthetic evaluation request limit reached')
                from email_agent.application.hybrid_ai import organization_schema
                bound=organization_schema(data,schema)
                synthetic=operation+':'+str(self.n) if name!='openai' else None
                if synthetic:store.synthetic_scope(synthetic,account_id,model)
                try:attempt=store.reserve(account_id,model,operation+':'+str(self.n),[],len((system+json.dumps(data,ensure_ascii=False)+json.dumps(bound)).encode())+512,2048,synthetic_scope=synthetic)
                except ValueError as exc:
                    from email_agent.application.model_errors import ModelFailure
                    raise ModelFailure('inference_budget_blocked','Approved evaluation budget cannot fit the next request') from exc
                try:
                    result=base.analyze(model,system,data,schema);store.finish(attempt,'done',result);return result
                except Exception as exc:
                    from email_agent.application.model_errors import classify_error
                    store.finish(attempt,'failed',error=classify_error(exc));raise
        def perform():
            with nullcontext():
                cases=json.loads(files('email_agent.resources').joinpath('semantic_benchmark.json').read_text())
                # Qualification must not read or disclose mailbox-derived names
                # or observed types. The benchmark supplies its public catalog.
                result=SemanticEvaluation(base if name=='ollama' else BudgetedProbe(),cap,cases).run()
                verified=bool(result['completed'] and result['valid_schema'] and max(result['category_quality'].values(),default=0)>=.9 and result['unsafe_promotional']==0)
                result['quality_qualified']=verified
                from email_agent.application.hybrid_evaluation import evaluated_capability
                upgraded=evaluated_capability(cap,result,verified);free=upgraded.free
                preserve=not result['completed'] and result['blocked_reason'] in ('inference_budget_blocked','provider_rate_limited','provider_unavailable','network_unavailable','authentication_failed')
                if not preserve:store.register(upgraded,time.time()+7*86400 if free else 0)
                with store.connect() as db:
                    db.execute('INSERT INTO hybrid_evaluations(provider,model,fingerprint,result) VALUES(?,?,?,?)',(name,cap.model,cap.fingerprint,json.dumps(result)))
                    db.execute("UPDATE hybrid_evaluation_runs SET status='done',result=? WHERE id=?",(json.dumps(result),operation))
                if result['valid_schema'] and result['completed']:store.record_model_check(account_id,cap,dict(passed=True,source='quality_evaluation',cases=result['attempted']),operation)
                return result
        return execute(perform)
    from email_agent.api.provider_qualification import register_provider_qualification
    register_provider_qualification(app,store,provider,lambda name:name=='ollama' or bool(key(name)))
    return hybrid,store
