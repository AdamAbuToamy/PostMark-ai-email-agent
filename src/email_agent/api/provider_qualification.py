"""One explicit synthetic plan, independent from account mail consent."""
import json
from importlib.resources import files
from fastapi import HTTPException,Request
from pydantic import BaseModel,ConfigDict,StrictBool,Field
from email_agent.api.message_management import authorize_local_change
from email_agent.application.provider_qualification import ProviderQualification
from email_agent.application.hybrid_ai import ModelCapability
from email_agent.infrastructure.database.qualification_store import SQLiteQualificationStore


class Body(BaseModel):model_config=ConfigDict(extra='forbid')
class Preview(Body):
    providers:list[str]=Field(min_length=1,max_length=9)
    models:dict[str,str]=Field(default_factory=dict,max_length=9)
class Approval(Body):
    approved:StrictBool
    reviewed_gold_hash:str
    free_providers:list[str]=Field(max_length=8)
    daily_limit:int=Field(default=20,ge=1,le=100)
    free_until_quota:StrictBool=False
class Stop(Body):id:str
class PoolRefresh(Body):
    provider:str
    reference:str=Field(min_length=1,max_length=150)
class Access(Body):
    approved:StrictBool
    account:str
    providers:list[str]=Field(min_length=1,max_length=8)
    free_only_confirmed:StrictBool
    reference:str=Field(min_length=1,max_length=150)


def register_provider_qualification(app,hybrid,providers,configured):
    gold=json.loads(files('email_agent.resources').joinpath('semantic_gold_v3.json').read_text())
    store=SQLiteQualificationStore(hybrid,gold['cases'],configured);store.initialize()
    store.refresh_task_capabilities()
    service=ProviderQualification(store,providers,gold['cases'])
    app.state.provider_qualification=service;app.state.qualification_store=store
    app.state.hybrid_organizer.qualification_ready=store.ready
    def run(fn,*args):
        try:return fn(*args)
        except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc)) from exc
    @app.get('/api/qualification/gold')
    def rubric():return gold
    @app.get('/api/qualification/status')
    def status():return dict(plan=store.latest())
    @app.post('/api/qualification/preview')
    def preview(payload:Preview,request:Request):
        authorize_local_change(request);return run(store.preview,payload.providers,payload.models)
    @app.post('/api/qualification/{id}/approve')
    def approve(id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        if not payload.approved:raise HTTPException(400,'Approve the frozen gold rubric, Free-only verification and total request/token budget')
        return run(store.approve,id,payload.reviewed_gold_hash,payload.free_providers,payload.daily_limit,payload.free_until_quota)
    @app.post('/api/qualification/{id}/recheck-wire')
    def recheck_wire(id:str,payload:PoolRefresh,request:Request):
        authorize_local_change(request);return run(store.recheck_repaired_wire,id,payload.reference)
    @app.post('/api/qualification/{id}/refresh-free-pool')
    def refresh_pool(id:str,payload:PoolRefresh,request:Request):
        authorize_local_change(request);return run(store.refresh_verified_free_pool,id,payload.provider,payload.reference)
    @app.post('/api/qualification/stop')
    def stop(payload:Stop,request:Request):
        authorize_local_change(request);return run(store.cancel,payload.id)
    @app.post('/api/qualification/resume')
    def resume(payload:Stop,request:Request):
        authorize_local_change(request);return run(store.resume,payload.id)
    @app.post('/api/qualification/step')
    def step(request:Request):
        authorize_local_change(request);return service.step()
    @app.post('/api/qualification/access')
    def access(payload:Access,request:Request):
        authorize_local_change(request)
        if not payload.approved or not payload.free_only_confirmed:raise HTTPException(400,'Explicit full-body per-account provider consent and Free-only billing-disabled policy required')
        from email_agent.application.hybrid_ai import ACTIVE_PROVIDERS
        if not set(payload.providers)<=set(ACTIVE_PROVIDERS)-{'ollama'}:raise HTTPException(400,'Choose eligible Free-only external providers')
        if any(not configured(p) for p in payload.providers):raise HTTPException(400,'Provider credential missing')
        from email_agent.infrastructure.ai.usage import FREE_AUTOMATION_PROVIDERS
        if not set(payload.providers)<=FREE_AUTOMATION_PROVIDERS:raise HTTPException(400,'No verified Free-only standing policy for this provider')
        for name in payload.providers:
            usable=[ModelCapability(**m['payload']) for m in hybrid.models() if m['provider']==name and m['payload']['validated'] and any(v>=.9 for v in m['payload'].get('quality',{}).values())]
            if not any(hybrid.payment_allowed(payload.account,m) for m in usable):raise HTTPException(400,name+': qualify a verified-free model before granting mailbox access')
        run(hybrid.authorize_standing,payload.account,payload.providers,payload.reference)
        if hybrid.shared_quota:hybrid.shared_quota.authorize_free_automation(payload.providers,payload.reference)
        for name in payload.providers:hybrid.configure(name,True)
        return dict(account=payload.account,providers=payload.providers,qualified_models_only=True,paid=False,gmail_mutations=False)
    return service
