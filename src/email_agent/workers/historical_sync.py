"""Explicit, bounded, read-only historical Gmail synchronization."""
import argparse
import json
import logging
from pathlib import Path
from email_agent.application.historical_sync import HistoricalSync
from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
from email_agent.infrastructure.gmail.request_policy import sync_lock
from email_agent.intelligence import build_classifier
from email_agent.main import build_reader

ROOT = Path(__file__).resolve().parents[3]
STATE_DB = ROOT / "data" / "historical_sync.db"
MESSAGE_DB = ROOT / "data" / "email_agent.db"
LOG = logging.getLogger(__name__)


def sync(account_id, max_pages=1, page_size=50, recheck=False):
    if account_id not in ("personal", "university") or not 1 <= max_pages <= 100 or not 1 <= page_size <= 500:
        raise ValueError("Invalid sync arguments")
    with sync_lock(account_id, MESSAGE_DB.parent):
        store = SQLiteHistoryStore(MESSAGE_DB)
        store.import_legacy(account_id, STATE_DB)
        if recheck:
            store.restart(account_id)
        if store.progress(account_id)["completed"]:
            build_classifier(SQLiteMessageRepository(MESSAGE_DB)).run(account_id)
            return store.progress(account_id)
        reader = build_reader(account_id)
        try:
            return HistoricalSync(reader, store).execute(account_id, max_pages, page_size)
        finally:
            # Interrupted classification can always be resumed locally.
            build_classifier(SQLiteMessageRepository(MESSAGE_DB)).run(account_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=("personal", "university", "all"), required=True)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--recheck", action="store_true", help="Explicitly restart traversal to refresh labels; preserves local data")
    args = parser.parse_args()
    if not 1 <= args.pages <= 100 or not 1 <= args.page_size <= 500:
        parser.error("pages: 1-100; page-size: 1-500")
    logging.basicConfig(level=logging.INFO)
    failed = False
    for account in (("personal", "university") if args.account == "all" else (args.account,)):
        try:
            print(json.dumps(sync(account, args.pages, args.page_size, args.recheck)))
        except Exception as exc:
            # Do not log remote response bodies, page tokens, or email content.
            LOG.error("%s: sync stopped (%s); checkpoint retained. Resume, or use --recheck for an expired page token.", account, type(exc).__name__)
            failed = True
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
