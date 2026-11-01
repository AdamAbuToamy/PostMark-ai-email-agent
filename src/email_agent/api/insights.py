import json
import sqlite3
from pathlib import Path

from fastapi import HTTPException


def register_insight_routes(app, project_root: Path):
    cache_path = project_root / "data" / "analysis_cache.db"

    @app.get("/api/insights/{account_id}")
    def list_insights(account_id: str):
        if account_id not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")

        if not cache_path.exists():
            return []

        uri = cache_path.resolve().as_uri() + "?mode=ro"

        with sqlite3.connect(uri, uri=True) as db:
            rows = db.execute("""
                SELECT message_id, model, result
                FROM analyses
                WHERE account_id = ?
                ORDER BY rowid DESC
                LIMIT 100
            """, (account_id,)).fetchall()

        results = []

        for message_id, model, raw in rows:
            try:
                result = json.loads(raw)
                if not isinstance(result, dict):
                    continue
                results.append({
                    "account_id": account_id,
                    "message_id": message_id,
                    "model": model,
                    "category": result.get("category", "other"),
                    "priority": result.get("priority", "low"),
                    "summary": result.get("summary", ""),
                    "action": result.get("action", ""),
                })
            except (ValueError, TypeError):
                continue

        return results
