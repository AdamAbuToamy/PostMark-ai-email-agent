"""Local cleanup configuration and review; no endpoint runs automatic Trash."""
from ipaddress import ip_address
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from email_agent.api.message_management import authorize_local_change
from email_agent.cleanup import build_cleanup


class CleanupSettings(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: StrictBool
    mode: Literal["review","automatic"]
    exclusions: list[str]=Field(default_factory=list,max_length=50)
    approved: StrictBool=False
    expected_revision: str=Field(max_length=64)


class CleanupMessage(BaseModel):
    model_config=ConfigDict(extra="forbid")
    message_id: str=Field(min_length=1,max_length=256,pattern=r"^[a-zA-Z0-9_-]+$")
    scope: Literal['message','sender']='message'


def register_cleanup_routes(app,messages,root,gmail,actions,service=None):
    service=service or build_cleanup(messages,root/"data"/"worker_state.db",gmail)
    from email_agent.api.mail_management import register_mail_management_routes
    register_mail_management_routes(app,messages,root,gmail,service,actions)
    def execute(account,operation,*args):
        try:
            service.validate(account)
            return operation(account,*args)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get("/api/cleanup/{account}")
    def settings(account:str,request:Request):
        try:local=request.client is not None and ip_address(request.client.host).is_loopback
        except ValueError:local=False
        if not local:raise HTTPException(403,"Cleanup history is local only")
        return execute(account,service.settings)

    @app.post("/api/cleanup/{account}")
    def configure(account:str,payload:CleanupSettings,request:Request):
        authorize_local_change(request)
        return execute(account,service.configure,payload.enabled,payload.mode,payload.exclusions,payload.approved,payload.expected_revision)

    @app.post("/api/cleanup/{account}/protect")
    def protect(account:str,payload:CleanupMessage,request:Request):
        authorize_local_change(request)
        return execute(account,service.protect,payload.message_id,payload.scope)

    @app.post("/api/cleanup/{account}/reconcile")
    def reconcile(account:str,payload:CleanupMessage,request:Request):
        authorize_local_change(request)
        return execute(account,service.reconcile,payload.message_id)

    @app.post("/api/cleanup/{account}/{operation}-preview")
    def preview(account:str,operation:Literal["trash","restore"],payload:CleanupMessage,request:Request):
        authorize_local_change(request)
        execute(account,service.store.item,payload.message_id)
        return execute(account,actions.preview,operation,[payload.message_id])
