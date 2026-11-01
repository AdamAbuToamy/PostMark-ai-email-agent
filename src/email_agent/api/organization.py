"""Grouped local previews use the existing loopback and CSRF boundary."""
import copy
import logging
import time
import threading
from fastapi import Request, Query, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from typing import Literal, Annotated
from email_agent.application.organization import Organization
from email_agent.infrastructure.database.organization_store import SQLiteOrganizationStore
from email_agent.api.message_management import authorize_local_change
from email_agent.infrastructure.gmail.request_policy import sync_lock, SyncBusyError


class GroupDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['approve','keep','exclude','dismiss']


def register_organization_routes(app, messages, classifier, cleanup):
    service = Organization(SQLiteOrganizationStore(messages.database_path,cleanup.store.management),
        classifier.registry, lambda account: cleanup.store.policy(account)['exclusions'])

    snapshots={};locks={a:threading.Lock() for a in ('personal','university')}
    @app.get('/api/organization/{account}')
    def overview(account: str, offset: int = Query(0,ge=0), limit: int = Query(12,ge=1,le=50), state: str = Query('all',max_length=20)):
        try:
            service.validate(account)
            if state not in ('all','pending','applied','dismissed','organized','protected','untouched'):raise ValueError('Invalid group state')
            key=(account,offset,limit,state)
            with locks[account]:
                saved=snapshots.get(key)
                if saved and time.time()-saved['observed_at']<5:return copy.deepcopy(saved)
                try:
                    result=service.overview(account,offset,limit,state)
                    from email_agent.infrastructure.database.continuous_store import ContinuousStore
                    result['summary']['organized']=ContinuousStore(messages.database_path,messages).progress(account)['organized']
                    result.update(observed_at=time.time(),stale=False)
                except Exception:
                    logging.getLogger(__name__).exception('Organization read unavailable for %s; queue preserved',account)
                    if not saved:raise HTTPException(503,'Organization preview temporarily unavailable; worker progress is independent') from None
                    result=copy.deepcopy(saved);result['stale']=True;return result
                if len(snapshots)>=32:snapshots.pop(next(iter(snapshots)))
                snapshots[key]=copy.deepcopy(result)
                return result
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.post('/api/organization/{account}/decision')
    def decision(account: str, payload: GroupDecision, request: Request):
        authorize_local_change(request)
        try:
            service.validate(account)
            with sync_lock(account,messages.database_path.parent):
                result = service.decide_group(account,payload.key,payload.fingerprint,payload.action)
                classifier.run(account)
                for key in list(snapshots):
                    if key[0]==account:snapshots.pop(key,None)
                return result
        except (ValueError,SyncBusyError) as exc:
            raise HTTPException(409,str(exc)) from exc
    return service

SemanticMessageID = Annotated[str, Field(min_length=1,max_length=256,pattern=r"^[a-zA-Z0-9_-]+$")]


class SemanticPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message_ids: list[SemanticMessageID] = Field(min_length=1,max_length=10)
    request_cap: StrictInt = Field(ge=1,le=10)
    category_id: str | None = Field(default=None,pattern=r'^[a-z][a-z0-9_]{1,49}$')
    include_excerpt: StrictBool = False

class SemanticApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    approved: StrictBool


def register_semantic_routes(app, service, directory):
    @app.post('/api/organization/{account}/semantic-preview')
    def preview(account: str, payload: SemanticPreview, request: Request):
        authorize_local_change(request)
        try:
            if payload.category_id:
                if payload.include_excerpt:raise ValueError('Section advice uses metadata only')
                return service.preview_sections(account,payload.category_id,payload.message_ids,payload.request_cap)
            return service.preview(account,payload.message_ids,payload.request_cap,payload.include_excerpt)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.post('/api/organization/{account}/semantic-run')
    def run(account: str, payload: SemanticApproval, request: Request):
        authorize_local_change(request)
        try:
            Organization.validate(account)
            with sync_lock(account,directory):
                return service.run(account,payload.plan_id,payload.approved)
        except (ValueError,SyncBusyError) as exc:raise HTTPException(409,str(exc)) from exc
