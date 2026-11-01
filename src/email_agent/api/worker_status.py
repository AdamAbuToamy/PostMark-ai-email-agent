import sqlite3
from pathlib import Path


class WorkerStatusReader:
    """Read worker state without modifying it."""

    def __init__(self, database_path: Path):
        self._database_path = database_path

    def read(self) -> dict:
        result = {
            "initialized": False,
            "accounts": {},
            "pending_analysis": 0,
            "total_detected": 0,
        }

        if not self._database_path.is_file():
            return result

        # Read-only connection. Do not create or modify the database.
        uri = self._database_path.resolve().as_uri() + "?mode=ro"

        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            states = db.execute("""
                SELECT account_id, initialized_at, last_success_at
                FROM account_state
            """).fetchall()

            for account_id, initialized_at, last_success in states:
                result["accounts"][account_id] = {
                    "initialized_at": initialized_at,
                    "last_success_at": last_success,
                }

            result["pending_analysis"] = db.execute("""
                SELECT COUNT(*)
                FROM inbox_events
                WHERE status = 'pending'
            """).fetchone()[0]

            result["total_detected"] = db.execute("""
                SELECT COUNT(*)
                FROM inbox_events
            """).fetchone()[0]

            result["initialized"] = bool(states)

        return result
