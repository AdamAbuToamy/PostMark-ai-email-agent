import os
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from ipaddress import ip_address
from email_agent.application.message_management import mailbox_location

from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)


PROJECT_ROOT = Path(os.environ.get("EMAIL_AGENT_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
DATABASE_PATH = PROJECT_ROOT / "data" / "email_agent.db"

ACCOUNTS = ("personal", "university")


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith(("api/", "assets/")) or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    repository: SQLiteMessageRepository | None = None,
    gmail_gateway=None,
    unsubscribe_verifier=None,
    unsubscribe_sender=None,
    chat_model_factory=None,
    manual_validator=None,
    unsubscribe_browser=None,
    unsubscribe_reader=None,
    unsubscribe_recipient=None,
) -> FastAPI:
    """Build the loopback API; optional writes require separate per-action approval."""

    injected_repository = repository is not None
    if repository is None:
        repository = SQLiteMessageRepository(DATABASE_PATH)

    repository.initialize()
    data_root = repository.database_path.parent.parent if repository.database_path.parent.name == "data" else repository.database_path.parent

    app = FastAPI(
        title="Email Agent API",
        version="0.1.0",
        docs_url="/docs",
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"] + (["testserver"] if injected_repository else []))

    @app.middleware("http")
    async def local_api_boundary(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            host = request.client.host if request.client else ""
            try:
                local = ip_address(host).is_loopback
            except ValueError:
                local = injected_repository and host == "testclient"
            if not local:
                return JSONResponse({"detail": "This personal-mail API is loopback-only"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site mail API requests are not allowed"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/accounts")
    def list_accounts():
        return [
            {
                "id": account_id,
                "message_count": repository.count_by_account(
                    account_id
                ),
            }
            for account_id in ACCOUNTS
        ]

    @app.get("/api/messages")
    def list_messages(
        account_id: str,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        if account_id not in ACCOUNTS:
            raise HTTPException(
                status_code=400,
                detail="Unknown email account",
            )

        messages = repository.list_by_account(
            account_id=account_id,
            limit=limit,
        )

        return [
            {
                "id": message.id,
                "account_id": message.account_id,
                "sender": message.sender,
                "subject": message.subject,
                "internal_date_ms": message.internal_date_ms,
                "gmail_location": mailbox_location(message.label_ids),
            }
            for message in messages
        ]


    @app.post("/api/sync/{account_id}")
    def sync_account(account_id: str, request: Request):
        """Read Gmail and update the local database."""

        if account_id not in ACCOUNTS:
            raise HTTPException(
                status_code=400,
                detail="Unknown account",
            )

        # Only allow requests initiated by the local frontend.
        origin = request.headers.get("origin")
        if origin not in (
            "http://127.0.0.1:5174",
            "http://localhost:5174",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ):
            raise HTTPException(
                status_code=403,
                detail="Untrusted request origin",
            )

        from email_agent.main import build_reader
        from email_agent.application.sync_recent_emails import (
            SyncRecentEmails,
        )

        reader = build_reader(account_id)
        use_case = SyncRecentEmails(
            reader=reader,
            message_repository=repository,
        )

        from email_agent.infrastructure.gmail.request_policy import sync_lock, SyncBusyError
        from email_agent.intelligence import build_classifier
        try:
            with sync_lock(account_id, repository.database_path.parent):
                messages = use_case.execute(account_id=account_id, limit=100)
                from email_agent.intelligence import refresh_special_labels
                refreshed=refresh_special_labels(repository,account_id,reader)
                build_classifier(repository, data_root / "data" / "categories.db").run(account_id)
                management=app.state.mail_management
                management.store.record(account_id,[management.evaluate(account_id,m.id) for m in [*messages,*refreshed.messages]],'incremental_sync')
                management.store.record_read(account_id,'incremental_labels',unavailable=refreshed.unavailable,read_errors=refreshed.errors)
        except SyncBusyError as exc:
            raise HTTPException(409, "Synchronization busy; try later") from exc

        return {
            "account_id": account_id,
            "fetched": len(messages),
            "stored": repository.count_by_account(account_id),
        }


    @app.get("/api/messages/{account_id}/{message_id}/content")
    def get_message_content(account_id: str, message_id: str):
        """Read the body of a message from the requested Gmail account."""

        if account_id not in ACCOUNTS:
            raise HTTPException(
                status_code=400,
                detail="Unknown account",
            )

        # A message must first belong to this account in local storage.
        if repository.get(account_id, message_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Message not found in this account",
            )

        from googleapiclient.errors import HttpError

        from email_agent.infrastructure.gmail.auth import (
            GmailAuthenticator,
        )
        from email_agent.infrastructure.gmail.client import GmailClient
        from email_agent.infrastructure.gmail.content_reader import (
            GmailContentReader,
        )

        authenticator = GmailAuthenticator(
            credentials_path=PROJECT_ROOT / "credentials.json",
            token_path=PROJECT_ROOT / "tokens" / f"{account_id}.json",
        )

        if gmail_gateway is not None:
            client = gmail_gateway.client(account_id)
        else:
            credentials = authenticator.authenticate()
            client = GmailClient(credentials)
        reader = GmailContentReader(client)

        try:
            content = reader.get_content(message_id)
            repository.record_search_content(account_id,message_id,content.pop('_classification_body',content.get("body", "")))
            existing=repository.get(account_id,message_id)
            repository.record_list_headers(account_id, message_id, {**dict(existing.list_headers or ()),**content.pop("list_headers", {})})
            repository.update_labels(account_id, message_id, content.pop("label_ids", ()))
            classifier=getattr(app.state,'message_classifier',None)
            if classifier is not None:
                classifier.classify(repository.get(account_id,message_id))
            return content
        except ValueError as error:
            raise HTTPException(400, "Invalid or oversized MIME content") from error
        except HttpError as error:
            status = error.resp.status
            if status in (403, 404):
                raise HTTPException(
                    status_code=status,
                    detail="Gmail access denied or message unavailable",
                ) from error
            raise HTTPException(
                status_code=502,
                detail="Unable to retrieve message from Gmail",
            ) from error

    @app.get("/api/inbox/{account_id}/{category}")
    def list_inbox_category(
        account_id: str,
        category: str,
        limit: int = Query(default=100, ge=1, le=100),
    ):
        allowed_categories = {
            "primary", "updates", "forums",
            "promotions", "social",
        }

        if account_id not in ACCOUNTS:
            raise HTTPException(400, "Unknown account")

        if category not in allowed_categories:
            raise HTTPException(400, "Unknown category")

        from email_agent.main import build_reader

        reader = build_reader(account_id)
        messages = reader.list_category(
            account_id=account_id,
            category=category,
            limit=limit,
        )

        return [
            {
                "id": message.id,
                "account_id": message.account_id,
                "sender": message.sender,
                "subject": message.subject,
                "internal_date_ms": message.internal_date_ms,
            }
            for message in messages
        ]


    from email_agent.api.analysis import register_analysis_routes

    register_analysis_routes(app, repository, data_root)



    @app.get("/api/worker/status")
    def worker_status():
        from email_agent.api.worker_status import WorkerStatusReader

        reader = WorkerStatusReader(
            PROJECT_ROOT / "data" / "worker_state.db"
        )
        return reader.read()


    from email_agent.api.insights import register_insight_routes

    register_insight_routes(app, data_root)

    # Serve the production React application from the same port.
    from email_agent.api.categories import register_category_routes

    register_category_routes(app, data_root)

    from email_agent.api.intelligence import register_intelligence_routes
    register_intelligence_routes(app, repository)

    from email_agent.api.message_management import register_message_management_routes
    register_message_management_routes(app, repository, data_root / "data" / "categories.db")

    from email_agent.infrastructure.gmail.capabilities import AccountGmail
    from email_agent.api.attachments import register_attachment_routes
    from email_agent.api.batch3 import register_batch3_routes
    gmail = gmail_gateway or AccountGmail(PROJECT_ROOT)
    register_attachment_routes(app, repository, gmail.client)
    register_batch3_routes(app, repository, data_root, gmail, model_factory=chat_model_factory, unsubscribe_verifier=unsubscribe_verifier, unsubscribe_sender=unsubscribe_sender,manual_validator=manual_validator,unsubscribe_browser=unsubscribe_browser,unsubscribe_reader=unsubscribe_reader,unsubscribe_recipient=unsubscribe_recipient)

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"

    if frontend_dist.joinpath("index.html").is_file():
        app.mount(
            "/",
            SPAStaticFiles(directory=frontend_dist, html=True),
            name="frontend",
        )

    return app


app = create_app()
