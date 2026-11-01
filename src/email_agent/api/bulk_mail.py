"""User-only bulk mutation endpoints. No organizer/tool-registry exposure."""
from dataclasses import replace
from contextlib import nullcontext
from typing import Literal,Annotated
from fastapi import HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from email_agent.api.message_management import authorize_local_change
from email_agent.application.bulk_mail import BulkMail
from email_agent.application.mail_management import assess
from email_agent.infrastructure.database.bulk_mail_store import SQLiteBulkMailStore
from email_agent.infrastructure.gmail.request_policy import SyncBusyError

class Scope(BaseModel):
    model_config=ConfigDict(extra='forbid')
    view:Literal['trash','spam','eligible','category']
    category:str|None=Field(default=None,max_length=100)
    state:Literal['new','done','all']='all'
    search:str=Field(default='',max_length=200)
class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation:Literal['trash','restore','delete']
    scope:Scope
    ids:list[Annotated[str,Field(min_length=1,max_length=256,pattern=r'^[a-zA-Z0-9_-]+$')]]|None=Field(default=None,max_length=50000)
    criterion:Literal['sender','subscription','category','semantic_type','group']|None=None
    anchor:str|None=Field(default=None,max_length=256)
class Confirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approved:StrictBool
    confirmation:str=Field(default='',max_length=100)

def register_bulk_mail(app,messages,gmail,cleanup,actions):
    store=SQLiteBulkMailStore(messages.database_path,actions.actions)
    def protect(account,message,decision,operation,implicit):
        # Removing Trash from this assessment does not waive content/user protection.
        candidate=replace(message,label_ids=tuple(v for v in message.label_ids or () if v not in ('TRASH','SPAM')) if message.label_ids is not None else None)
        protection=cleanup.store.protection(account,message,ignore_system=True)
        if protection in ('Already in Gmail Trash','Historical discovery; no historical cleanup approval'):protection=None
        result=assess(candidate,decision,cleanup.store.policy(account)['exclusions'],protection,actions.assignments.cleanup_evidence(account,message.id))
        return None if result['verdict']=='eligible' else result['reason']
    def allowed(account,operation):
        cap=gmail.capabilities(account)
        return bool(cap.get('gmail_permanent_delete') if operation=='delete' else cap.get('gmail_modify'))
    service=BulkMail(messages,actions.assignments,store,lambda account,operation:gmail.delete_actions(account) if operation=='delete' else gmail.actions(account),allowed,protect,actions.operation_lock or (lambda account:nullcontext()))
    def call(account,operation,*args):
        if account not in ('personal','university'):raise HTTPException(400,'Unknown account')
        try:return operation(account,*args)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc
        except SyncBusyError as exc:raise HTTPException(409,'Gmail synchronization is busy; operation is saved and can resume') from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    @app.post('/api/bulk-mail/{account}/preview')
    def preview(account:str,payload:Selection,request:Request):
        authorize_local_change(request)
        return call(account,service.preview,payload.operation,payload.scope.model_dump(),payload.ids,payload.criterion,payload.anchor)
    @app.get('/api/bulk-mail/{account}/{id}')
    def status(account:str,id:str,request:Request,offset:int=Query(0,ge=0)):
        authorize_local_change(request);return call(account,store.get,id,offset)
    @app.post('/api/bulk-mail/{account}/{id}/confirm')
    def confirm(account:str,id:str,payload:Confirmation,request:Request):
        authorize_local_change(request)
        current=call(account,store.get,id)
        if not allowed(account,current['operation']):raise HTTPException(403,'Separate Gmail permission is required; no messages were changed')
        return call(account,store.approve,id,payload.approved,payload.confirmation)
    @app.post('/api/bulk-mail/{account}/{id}/step')
    def step(account:str,id:str,request:Request):
        authorize_local_change(request);return call(account,service.step,id)
    @app.post('/api/bulk-mail/{account}/{id}/reconcile')
    def reconcile(account:str,id:str,request:Request):
        authorize_local_change(request);return call(account,service.reconcile,id)
    app.state.bulk_mail=service
