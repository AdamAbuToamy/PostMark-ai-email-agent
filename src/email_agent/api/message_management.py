"""Local mailbox endpoints. Remote Gmail actions are intentionally absent."""
from ipaddress import ip_address
from pathlib import Path

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel

from email_agent.application.message_management import MessageManagement
from email_agent.intelligence import build_classifier

ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:5174", "http://localhost:5174",
}


class LocalStatusRequest(BaseModel):
    state: str


class CategoryChangeRequest(BaseModel):
    category_id: str
    application_ref: str = ""


def authorize_local_change(request: Request):
    # This is a loopback-only operation and CSRF check, not LAN authentication.
    try:
        local_peer = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_peer = False
    if not local_peer:
        raise HTTPException(403, "Local message actions require a loopback connection")
    if request.headers.get("X-Email-Agent-Action") != "message-management":
        raise HTTPException(403, "Missing local action header")
    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        raise HTTPException(403, "Untrusted origin")


def register_message_management_routes(app, messages, categories_path: Path):
    classifier = build_classifier(messages, categories_path)
    service = MessageManagement(messages, classifier.assignments, classifier.registry, classifier)

    def execute(operation, *args):
        try:
            return operation(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/category-mailbox/{account_id}/categories")
    def categories(account_id: str):
        return execute(service.categories, account_id)

    @app.get("/api/category-mailbox/{account_id}/{category_id}")
    def mailbox(account_id: str, category_id: str, state: str = "new",
                search: str = Query("", max_length=200), sort: str = "newest",
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), section: str = Query('all',max_length=30)):
        result=execute(service.mailbox, account_id, category_id, state, search, sort, offset, limit, section)
        hybrid=getattr(app.state,'hybrid_store',None)
        if hybrid and category_id=='other':
            result['processing']=hybrid.other_progress(account_id)
            statuses=hybrid.task_rows(account_id,[item['message_id'] for item in result['items']])
            for item in result['items']:item['processing']=statuses.get(item['message_id'])
        return result

    @app.get("/api/local-messages/{account_id}/{message_id}")
    def details(account_id: str, message_id: str):
        return execute(service.details, account_id, message_id)

    @app.patch("/api/local-messages/{account_id}/{message_id}/status")
    def status(account_id: str, message_id: str, payload: LocalStatusRequest, request: Request):
        authorize_local_change(request)
        return execute(service.set_status, account_id, message_id, payload.state)

    @app.patch("/api/local-messages/{account_id}/{message_id}/category")
    def change_category(account_id: str, message_id: str, payload: CategoryChangeRequest, request: Request):
        authorize_local_change(request)
        return execute(service.change_category, account_id, message_id, payload.category_id, payload.application_ref)
