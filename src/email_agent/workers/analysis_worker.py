from contextlib import closing
import argparse
import fcntl
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path

from email_agent.application.analyze_inbox import AnalyzeInbox, validate_cached_result
from email_agent.infrastructure.ai.usage import reserve_call
from email_agent.infrastructure.ai.gemini_model import GeminiTextModel
from email_agent.infrastructure.gmail.content_reader import GmailContentReader
from email_agent.main import build_reader


ROOT = Path(__file__).resolve().parents[3]
STATE_DB = ROOT / "data" / "worker_state.db"
CACHE_DB = ROOT / "data" / "analysis_cache.db"
MODEL = "gemini-3.5-flash-lite"
MAX_BATCH = 3


def pending_events():
    if not STATE_DB.exists():
        return []

    with closing(sqlite3.connect(STATE_DB)) as db, db:
        return db.execute("""
            SELECT account_id, message_id
            FROM inbox_events
            WHERE status = 'pending'
            ORDER BY detected_at, account_id, message_id
            LIMIT ?
        """, (MAX_BATCH,)).fetchall()


def initialize_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(CACHE_DB)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                model TEXT NOT NULL,
                result TEXT NOT NULL,
                PRIMARY KEY (account_id, message_id, model)
            )
        """)


def cached_result(account_id, message_id):
    with closing(sqlite3.connect(CACHE_DB)) as db, db:
        row = db.execute("""
            SELECT result FROM analyses
            WHERE account_id = ? AND message_id = ? AND model = ?
        """, (account_id, message_id, MODEL)).fetchone()
    return validate_cached_result(row[0]) if row else None


def save_result(account_id, message_id, result):
    # Commit result first. If interrupted afterward, next run can
    # reuse the cache instead of calling Gemini again.
    with closing(sqlite3.connect(CACHE_DB)) as db, db:
        db.execute("""
            INSERT OR IGNORE INTO analyses
                (account_id, message_id, model, result)
            VALUES (?, ?, ?, ?)
        """, (
            account_id, message_id, MODEL,
            json.dumps(result, ensure_ascii=False),
        ))

    with closing(sqlite3.connect(STATE_DB)) as db, db:
        db.execute("""
            UPDATE inbox_events
            SET status = 'completed'
            WHERE account_id = ? AND message_id = ?
              AND status = 'pending'
        """, (account_id, message_id))



# Conservative request cap, not a monetary spending guarantee.
LEDGER_DB = ROOT / "data" / "ai_usage.db"
LOCK_FILE = ROOT / "data" / "ai_worker.lock"


@contextmanager
def single_worker(path=LOCK_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a+") as handle:
        try:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            raise RuntimeError(
                "Another AI analysis worker is already running"
            )

        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reserve_ai_call(account_id, message_id):
    return reserve_call(LEDGER_DB, account_id, message_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Authorize sending uncached email content to Gemini",
    )
    args = parser.parse_args()

    events = pending_events()
    print(f"Pending selected for this run: {len(events)}")

    if not events:
        print("No new messages to analyze.")
        return

    if not args.approve:
        print("DRY RUN: no email content read or sent to Gemini.")
        print("To authorize one batch, use --approve.")
        return

    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not configured")

    with single_worker():
        process_approved_batch(events)


def process_approved_batch(events):
    initialize_cache()

    # One message per Gemini call, up to three per invocation.
    # Reuse previously cached results whenever possible.
    for account_id, message_id in events[:MAX_BATCH]:
        try:
            result = cached_result(account_id, message_id)

            if result is None:
                reader = build_reader(account_id)
                content_reader = GmailContentReader(reader._client)
                content = content_reader.get_content(message_id)

                if not reserve_ai_call(account_id, message_id):
                    print("Daily AI request limit reached.")
                    break

                model = GeminiTextModel(
                    api_key=os.environ["GEMINI_API_KEY"],
                    model=MODEL,
                )

                result = AnalyzeInbox(model).execute([{
                    "sender": content["sender"],
                    "subject": content["subject"],
                    "body": content["body"],
                }])[0]

            save_result(account_id, message_id, result)
            print(
                f"{account_id}: analyzed message "
                f"({result['category']}, {result['priority']})"
            )
        except Exception as exc:
            # Leave pending for a later, explicitly approved retry.
            print(
                f"{account_id}: analysis failed "
                f"({type(exc).__name__})"
            )


if __name__ == "__main__":
    main()
