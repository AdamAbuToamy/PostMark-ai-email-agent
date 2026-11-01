"""Thin local organizer delivery; all AI/structure changes are explicit scopes."""
from fastapi import Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt
from typing import Literal
from email_agent.api.message_management import authorize_local_change
from email_agent.infrastructure.gmail.request_policy import sync_lock,SyncBusyError


class LocalRun(BaseModel):
    model_config=ConfigDict(extra='forbid')
    limit:StrictInt=Field(default=200,ge=1,le=200)

class Scope(BaseModel):
    model_config=ConfigDict(extra='forbid')
    accounts:list[Literal['personal','university']]=Field(min_length=1,max_length=2)
    request_cap:StrictInt=Field(default=8,ge=1,le=8)
    max_messages:StrictInt=Field(default=32,ge=1,le=64)

    content_mode:Literal['excerpt','full']='full'
    message_ids:list[str]|None=Field(default=None,max_length=64)

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approved:StrictBool

class BackgroundControl(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool

class LocalPolicy(Approval):
    enabled:StrictBool

class LocalTool(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tool:str=Field(min_length=1,max_length=50)
    arguments:dict[str,str]=Field(default_factory=dict,max_length=4)

class Decision(Approval):
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')

class Recurring(Approval):
    enabled:StrictBool
    request_cap:StrictInt=Field(default=2,ge=1,le=2)
    max_messages:StrictInt=Field(default=8,ge=1,le=8)


def register_organizer_routes(app,service,directory,body_step=None):
    def execute(call,*args):
        try:return call(*args)
        except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/organizer/{account}/local-policy')
    def local_policy(account:str,payload:LocalPolicy,request:Request):
        authorize_local_change(request);service.account(account)
        try:
            with sync_lock(account,directory):
                result=execute(service.store.local_policy,account,payload.enabled,payload.approved)
                from email_agent.infrastructure.database.continuous_store import ContinuousStore
                ContinuousStore(service.messages.database_path,service.messages).configure_organization(account,payload.enabled)
                return result
        except SyncBusyError as exc:raise HTTPException(409,'Account busy; retry after the current batch') from exc

    @app.post('/api/organizer/{account}/tools')
    def tool(account:str,payload:LocalTool,request:Request):
        authorize_local_change(request);service.account(account)
        from email_agent.application.local_organization_agent import LocalOrganizationAgent,OrganizationCommand
        try:
            with sync_lock(account,directory):return execute(LocalOrganizationAgent(service,service.store).execute,account,OrganizationCommand(payload.tool,payload.arguments))
        except SyncBusyError as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/organizer/{account}/control')
    def control(account:str,payload:BackgroundControl,request:Request):
        authorize_local_change(request);service.account(account)
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
        SQLiteHistoryStore(service.messages.database_path)
        store=ContinuousStore(service.messages.database_path,service.messages);store.initialize();store.configure_organization(account,payload.enabled)
        return store.progress(account)

    @app.get('/api/organizer/{account}')
    def overview(account:str):
        result=execute(service.overview,account)
        from email_agent.infrastructure.database.continuous_store import ContinuousStore
        store=ContinuousStore(service.messages.database_path,service.messages)
        with store.connect() as db:available=db.execute("SELECT 1 FROM sqlite_master WHERE name='automation_accounts'").fetchone()
        if available:
            result['automation']=store.progress(account)
            from email_agent.infrastructure.database.automation_ai_store import AutomationAIStore
            ai=AutomationAIStore(service.messages.database_path,service.messages)
            result['automation']['ai']=ai.ai_progress(account)
        return result

    @app.post('/api/organizer/{account}/local')
    def local(account:str,payload:LocalRun,request:Request):
        authorize_local_change(request)
        try:
            service.account(account)
            with sync_lock(account,directory):return service.organize_local(account,payload.limit)
        except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/organizer/preview')
    def preview(payload:Scope,request:Request):
        authorize_local_change(request)
        return execute(service.preview,payload.accounts,payload.request_cap,payload.max_messages,0,payload.content_mode,payload.message_ids)

    @app.post('/api/organizer/campaigns/{id}/run')
    def run(id:str,payload:Approval,request:Request):
        authorize_local_change(request)
        from contextlib import ExitStack
        from email_agent.workers.fill_content import fill_lock
        def bounded():
            accounts=service.store.campaign(id)['scope']['accounts']
            with ExitStack() as stack:
                stack.enter_context(fill_lock(directory))
                for account in sorted(accounts):stack.enter_context(sync_lock(account,directory))
                return service.run(id,payload.approved)
        return execute(bounded)

    @app.get('/api/organizer/campaigns/{id}/progress')
    def progress(id:str):return execute(service.campaign_result,id)

    @app.post('/api/organizer/{account}/proposals/{id}/decision')
    def decide(account:str,id:str,payload:Decision,request:Request):
        authorize_local_change(request)
        try:
            service.account(account)
            with sync_lock(account,directory):return service.decide(account,id,payload.fingerprint,payload.approved)
        except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/organizer/{account}/recurring')
    def recurring(account:str,payload:Recurring,request:Request):
        authorize_local_change(request)
        return execute(service.configure_recurring,account,payload.enabled,payload.request_cap,payload.max_messages,payload.approved)

    @app.get('/api/organizer/{account}/messages/{id}')
    def evidence(account:str,id:str):
        execute(service.account,account)
        return dict(timeline=service.store.timeline(account,id))

    if body_step:
        @app.post('/api/organizer/{account}/read-missing')
        def bodies(account:str,payload:Approval,request:Request):
            authorize_local_change(request)
            if payload.approved is not True:raise HTTPException(400,'Confirm at most5 readonly message reads')
            try:
                service.account(account)
                with sync_lock(account,directory):return body_step(account)
            except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc
