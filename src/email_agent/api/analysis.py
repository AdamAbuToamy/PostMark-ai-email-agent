from contextlib import closing
import json
import os
import sqlite3
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel

from email_agent.application.analyze_inbox import AnalyzeInbox, validate_cached_result
from email_agent.infrastructure.ai.usage import reserve_call
from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
from email_agent.infrastructure.gmail.content_reader import GmailContentReader


MODEL_NAME = "gemini-3.5-flash-lite"
ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
}


class AnalysisRequest(BaseModel):
    approved: bool = False


def register_analysis_routes(app, repository, project_root: Path):
    db_path = project_root / "data" / "analysis_cache.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                model TEXT NOT NULL,
                result TEXT NOT NULL,
                PRIMARY KEY (account_id, message_id, model)
            )
        """)

    @app.post("/api/analyze/{account_id}")
    def analyze_account(
        account_id: str,
        payload: AnalysisRequest,
        request: Request,
    ):
        if request.headers.get("origin") not in ALLOWED_ORIGINS:
            raise HTTPException(403, "Untrusted origin")

        if account_id not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")

        if not payload.approved:
            raise HTTPException(400, "Explicit approval required")

        # Never analyze more than three messages in one request.
        messages = repository.list_by_account(account_id, limit=3)

        if not messages:
            return {"items": [], "cached": 0, "analyzed": 0}

        results = {}
        missing = []

        with closing(sqlite3.connect(db_path)) as db, db:
            for message in messages:
                row = db.execute(
                    """
                    SELECT result FROM analyses
                    WHERE account_id = ?
                      AND message_id = ?
                      AND model = ?
                    """,
                    (account_id, message.id, MODEL_NAME),
                ).fetchone()

                if row:
                    try:
                        results[message.id] = validate_cached_result(row[0])
                    except (ValueError, TypeError) as exc:
                        raise HTTPException(409, "Invalid cached analysis; manual repair required") from exc
                else:
                    missing.append(message)

        cached_count = len(results)

        if missing:
            api_key = os.getenv("GEMINI_API_KEY")

            if not api_key:
                raise HTTPException(
                    503,
                    "GEMINI_API_KEY is not configured in the API server",
                )

            if not reserve_call(project_root / "data" / "ai_usage.db", account_id, ",".join(m.id for m in missing)):
                raise HTTPException(429, "Shared daily AI request cap reached")

            from email_agent.main import build_reader

            gmail_reader = build_reader(account_id)
            content_reader = GmailContentReader(gmail_reader._client)

            data = []
            for message in missing:
                content = content_reader.get_content(message.id)
                data.append({
                    "sender": content["sender"],
                    "subject": content["subject"],
                    "body": content["body"],
                })

            model = GeminiTextModel(api_key, model=MODEL_NAME)
            analyzed = AnalyzeInbox(model).execute(data)

            with closing(sqlite3.connect(db_path)) as db, db:
                for message, result in zip(missing, analyzed):
                    results[message.id] = result

                    db.execute(
                        """
                        INSERT OR IGNORE INTO analyses
                            (account_id, message_id, model, result)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            account_id,
                            message.id,
                            MODEL_NAME,
                            json.dumps(result, ensure_ascii=False),
                        ),
                    )

        return {
            "items": [
                {
                    "message_id": message.id,
                    "account_id": account_id,
                    "subject": message.subject,
                    **{
                        key: results[message.id][key]
                        for key in (
                            "category", "priority", "summary", "action"
                        )
                    },
                }
                for message in messages
            ],
            "cached": cached_count,
            "analyzed": len(missing),
        }
