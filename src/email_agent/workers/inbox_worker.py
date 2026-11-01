from contextlib import closing
import argparse
import logging
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from email_agent.main import build_reader
from email_agent.infrastructure.database.message_repository import (
    SQLiteMessageRepository,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATABASE_PATH = PROJECT_ROOT / "data" / "email_agent.db"
STATE_PATH = PROJECT_ROOT / "data" / "worker_state.db"

ACCOUNTS = ("personal", "university")
DEFAULT_INTERVAL = 600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("email-agent-worker")

running = True


def stop_worker(_signal, _frame):
    global running
    running = False


def initialize_state():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(STATE_PATH)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                account_id TEXT PRIMARY KEY,
                initialized_at TEXT NOT NULL,
                last_success_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS seen_messages (
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                PRIMARY KEY (account_id, message_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS inbox_events (
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (account_id, message_id)
            )
        """)


def sync_account(account_id, repository):
    from email_agent.infrastructure.gmail.request_policy import sync_lock
    from email_agent.intelligence import build_classifier
    with sync_lock(account_id, repository.database_path.parent):
        touched=[]
        result = _sync_account(account_id, repository,touched)
        from email_agent.intelligence import refresh_special_labels
        refreshed=refresh_special_labels(repository,account_id,build_reader(account_id))
        classifier=build_classifier(repository)
        # Historical work has its own durable walk. Reclassifying the whole
        # mailbox every10 minutes held Gmail's account lock and starved AI.
        changed={m.id:m for m in [*touched,*refreshed.messages]}
        for message in changed.values():classifier.classify(message)
        from email_agent.cleanup import build_cleanup
        from email_agent.infrastructure.gmail.capabilities import AccountGmail
        cleanup=build_cleanup(repository,STATE_PATH,AccountGmail(PROJECT_ROOT))
        from email_agent.application.mail_management import MailManagement
        management=MailManagement(repository,classifier,cleanup,None,None)
        management.incremental(account_id)
        management.store.record(account_id,[management.evaluate(account_id,m.id) for m in refreshed.messages],'incremental_labels')
        management.store.record_read(account_id,'incremental_labels',unavailable=refreshed.unavailable,read_errors=refreshed.errors)
        cleanup.run(account_id)
        return result


def _sync_account(account_id, repository,touched=None):
    # Gmail access happens before the state transaction.
    reader = build_reader(account_id)
    messages = reader.list_recent(
        account_id=account_id,
        limit=100,
    )
    if touched is not None:touched.extend(messages)

    now = datetime.now(timezone.utc).isoformat()

    with closing(sqlite3.connect(STATE_PATH)) as db, db:
        state = db.execute(
            """
            SELECT initialized_at
            FROM account_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

        # Save message metadata to the shared application database.
        for message in messages:
            if message.account_id != account_id:
                raise ValueError("Account mismatch")
            repository.save(message)

        # Populate the baseline without creating analysis events.
        # Also safely migrate accounts initialized by the older worker.
        seen_count = db.execute(
            "SELECT COUNT(*) FROM seen_messages WHERE account_id = ?",
            (account_id,),
        ).fetchone()[0]

        if state is None or seen_count == 0:
            for message in messages:
                db.execute(
                    """
                    INSERT OR IGNORE INTO seen_messages
                    (account_id, message_id) VALUES (?, ?)
                    """,
                    (account_id, message.id),
                )

            db.execute(
                """
                INSERT INTO account_state
                    (account_id, initialized_at, last_success_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id)
                DO UPDATE SET last_success_at = excluded.last_success_at
                """,
                (account_id, now, now),
            )

            logger.info(
                "%s: baseline recorded (%d messages)",
                account_id,
                len(messages),
            )
            return 0

        new_count = 0

        for message in messages:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO seen_messages
                (account_id, message_id) VALUES (?, ?)
                """,
                (account_id, message.id),
            )

            if cursor.rowcount:
                db.execute(
                    """
                    INSERT OR IGNORE INTO inbox_events
                    (account_id, message_id, detected_at, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (account_id, message.id, now),
                )
                new_count += 1

        db.execute(
            """
            UPDATE account_state
            SET last_success_at = ?
            WHERE account_id = ?
            """,
            (now, account_id),
        )

    logger.info(
        "%s: %d newly detected messages",
        account_id,
        new_count,
    )
    return new_count


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Seconds between scans (minimum 600)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan and exit",
    )

    args = parser.parse_args()

    if args.interval < 600:
        parser.error("Minimum interval is 600 seconds")

    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)

    initialize_state()

    repository = SQLiteMessageRepository(DATABASE_PATH)
    repository.initialize()

    logger.info("Email Agent worker started")
    logger.info("Accounts: personal, university")
    logger.info("Sync interval: %d seconds", args.interval)
    logger.info("AI auto-analysis: disabled")
    logger.info("Automatic Trash: disabled unless separately enabled and authorized per account")

    while running:
        for account_id in ACCOUNTS:
            if not running:
                break

            try:
                sync_account(account_id, repository)
            except Exception as exc:
                logger.error("Synchronization failed: %s (%s)", account_id, type(exc).__name__)

        if args.once:
            break

        # Responsive shutdown even with a one-hour interval.
        for _ in range(args.interval):
            if not running:
                break
            time.sleep(1)

    logger.info("Email Agent worker stopped")


if __name__ == "__main__":
    main()
