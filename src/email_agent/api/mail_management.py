"""Account-bound mail management; reads and local scans never mutate Gmail."""
from typing import Literal,Annotated
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from email_agent.api.message_management import authorize_local_change
from email_agent.application.mail_management import MailManagement
from email_agent.infrastructure.gmail.mail_discovery import GmailMailDiscovery
from email_agent.infrastructure.gmail.request_policy import sync_lock,SyncBusyError
from email_agent.intelligence import build_classifier


class ScanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    restart:StrictBool=False
    limit:int=Field(default=100,ge=1,le=200)
class MessageRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_id:str=Field(min_length=1,max_length=256,pattern=r'^[a-zA-Z0-9_-]+$')
class ProtectionRequest(MessageRequest):scope:Literal['message','list','sender']='message'
MessageID=Annotated[str,Field(min_length=1,max_length=256,pattern=r'^[a-zA-Z0-9_-]+$')]
class BatchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_ids:list[MessageID]=Field(min_length=1,max_length=20)
class ReconcileRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action_id:str=Field(min_length=1,max_length=100)
class ReadRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approved:StrictBool
    restart:StrictBool=False


def register_mail_management_routes(app,messages,root,gmail,cleanup,actions):
    service=MailManagement(messages,build_classifier(messages,root/'data'/'categories.db'),cleanup,actions,GmailMailDiscovery(gmail,messages.database_path,root/'data'/'historical_sync.db'))
    app.state.mail_management=service
    from email_agent.api.bulk_mail import register_bulk_mail
    register_bulk_mail(app,messages,gmail,cleanup,actions)
    from email_agent.api.organization import register_organization_routes
    register_organization_routes(app,messages,service.classifier,cleanup)
    def execute(account,operation,*args):
        try:
            cleanup.validate(account)
            return operation(account,*args)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc
        except (SyncBusyError,RuntimeError) as exc:raise HTTPException(409,'Account task is running or checkpoint changed; refresh progress') from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/mail-management/{account}/progress')
    def progress(account:str):return execute(account,service.progress)
    @app.get('/api/mail-management/{account}/messages')
    def listing(account:str,view:Literal['eligible','protected','history','trash','spam']='eligible',search:str=Query('',max_length=200),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
        return execute(account,service.listing,view,search,offset,limit)
    @app.post('/api/mail-management/{account}/scan')
    def scan(account:str,payload:ScanRequest,request:Request):
        authorize_local_change(request)
        return execute(account,service.scan,payload.restart,payload.limit)
    @app.post('/api/mail-management/{account}/keep')
    def keep(account:str,payload:MessageRequest,request:Request):
        authorize_local_change(request);return execute(account,service.keep,payload.message_id)
    @app.get('/api/mail-management/{account}/protection-options/{id}')
    def options(account:str,id:str):return execute(account,service.protection_choices,id)
    @app.post('/api/mail-management/{account}/protect')
    def protect(account:str,payload:ProtectionRequest,request:Request):
        authorize_local_change(request);return execute(account,service.protect,payload.message_id,payload.scope)
    @app.post('/api/mail-management/{account}/batch-preview')
    def batch(account:str,payload:BatchRequest,request:Request):
        authorize_local_change(request);return execute(account,service.batch,payload.message_ids)
    @app.post('/api/mail-management/{account}/restore-preview')
    def restore(account:str,payload:MessageRequest,request:Request):
        authorize_local_change(request);return execute(account,service.restore,payload.message_id)
    def read(account,operation,*args):
        with sync_lock(account,messages.database_path.parent):return operation(account,*args)
    @app.post('/api/mail-management/{account}/backfill')
    def backfill(account:str,payload:ReadRequest,request:Request):
        authorize_local_change(request)
        if not payload.approved:raise HTTPException(400,'Approve this bounded read-only Gmail page')
        return execute(account,read,service.backfill,payload.restart)
    @app.post('/api/mail-management/{account}/refresh-trash')
    def refresh_trash(account:str,payload:ReadRequest,request:Request):
        authorize_local_change(request)
        if not payload.approved:raise HTTPException(400,'Approve this bounded read-only Gmail page')
        return execute(account,read,service.refresh_trash)
    @app.post('/api/mail-management/{account}/refresh-spam')
    def refresh_spam(account:str,payload:ReadRequest,request:Request):
        authorize_local_change(request)
        if not payload.approved:raise HTTPException(400,'Approve this bounded read-only Gmail page')
        return execute(account,read,service.refresh_trash,'SPAM')
    @app.post('/api/mail-management/{account}/reconcile-action')
    def reconcile(account:str,payload:ReconcileRequest,request:Request):
        authorize_local_change(request);return execute(account,read,service.reconcile_action,payload.action_id)
    return service
