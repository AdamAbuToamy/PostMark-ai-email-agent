"""Read-only views of local decisions. Mutations use the local audited CLI."""
from fastapi import HTTPException, Query
from email_agent.infrastructure.database.sqlite_message_category_repository import SQLiteMessageCategoryRepository
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore


def register_intelligence_routes(app, messages):
    assignments = SQLiteMessageCategoryRepository(messages.database_path)
    assignments.initialize()
    history = SQLiteHistoryStore(messages.database_path)

    def validate(account):
        if account not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")

    @app.get("/api/classifications/{account_id}/progress")
    def progress(account_id: str):
        validate(account_id)
        state = history.progress(account_id)
        return {**assignments.progress(account_id), "history": {
            "pages_completed": state["pages_completed"],
            "messages_processed": state["messages_processed"],
            "completed": bool(state["completed"]),
        }}

    @app.get("/api/classifications/{account_id}")
    def items(account_id: str, category: str | None = None, after: str = "",
              limit: int = Query(100, ge=1, le=100)):
        validate(account_id)
        rows = assignments.items(account_id, category, after, limit)
        return {"items": rows, "next_after": rows[-1]["message_id"] if len(rows) == limit else None}

    @app.get("/api/placement-reviews/{account_id}")
    def reviews(account_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        validate(account_id)
        rows = assignments.reviews(account_id, after, limit)
        return {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None}
