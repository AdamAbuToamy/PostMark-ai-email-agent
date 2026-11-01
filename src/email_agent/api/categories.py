from pathlib import Path

from fastapi import HTTPException, Request

from email_agent.application.category_registry import CategoryRegistry
from email_agent.infrastructure.database.sqlite_category_repository import SQLiteCategoryRepository


ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
}


def register_category_routes(app, project_root: Path):
    repository = SQLiteCategoryRepository(
        project_root / "data" / "categories.db"
    )
    registry = CategoryRegistry(repository)

    def authorize_change(request: Request):
        if request.headers.get("X-Email-Agent-Action") != "category-review":
            raise HTTPException(403, "Missing action authorization header")

        origin = request.headers.get("origin")
        if origin is not None:
            from urllib.parse import urlsplit

            parsed = urlsplit(origin)
            host = parsed.hostname
            port = parsed.port

            trusted_hosts = {"127.0.0.1", "localhost"}
            trusted_ports = {8000, 5174}

            if (
                parsed.scheme != "http"
                or host not in trusted_hosts
                or port not in trusted_ports
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise HTTPException(403, "Untrusted origin")

    @app.get("/api/categories")
    def list_categories(account: str | None = None):
        if account is not None and account not in ("personal", "university"):raise HTTPException(400,"Unknown account")
        return registry.categories(account)

    @app.get("/api/categories/proposals")
    def list_category_proposals():
        return registry.proposals()

    @app.post("/api/categories/proposals/{proposal_id}/approve")
    def approve_proposal(proposal_id: int, request: Request):
        authorize_change(request)

        if not registry.approve(proposal_id):
            raise HTTPException(409, "Proposal is not pending")

        return {"id": proposal_id, "status": "approved"}

    @app.post("/api/categories/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: int, request: Request):
        authorize_change(request)

        if not registry.reject(proposal_id):
            raise HTTPException(409, "Proposal is not pending")

        return {"id": proposal_id, "status": "rejected"}
