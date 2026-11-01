"""Explicit offline SQLite backup and additive migration. Never touches tokens."""
import argparse
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def prepare(root):
    from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository
    from email_agent.infrastructure.database.action_store import SQLiteActionStore
    from email_agent.infrastructure.ai.usage import SQLiteAIQuota
    from email_agent.intelligence import build_classifier
    from email_agent.infrastructure.database.sqlite_history_store import SQLiteHistoryStore
    data=root/'data'
    data.mkdir(exist_ok=True)
    backups=root/'backups'
    backups.mkdir(exist_ok=True)
    target=backups/('batch3-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    target.mkdir(mode=0o700)
    paths=sorted(set(data.glob('*.db')) | set(data.glob('*.sqlite3')))
    for path in paths:
        output=target/path.name
        # Every destination is new, private, and outside the live data directory.
        output.touch(mode=0o600,exist_ok=False)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(output)) as destination:
            source.backup(destination)
    messages=SQLiteMessageRepository(data/'email_agent.db')
    messages.initialize()
    messages.initialize_discovery()
    build_classifier(messages,data/'categories.db')
    SQLiteActionStore(messages.database_path)
    from email_agent.infrastructure.database.cleanup_store import SQLiteCleanupStore
    SQLiteCleanupStore(messages.database_path,data/"worker_state.db")
    SQLiteAIQuota(data/'ai_usage.db')
    SQLiteHistoryStore(messages.database_path)
    return target,len(paths)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-and-migrate',action='store_true')
    args=parser.parse_args()
    if not args.backup_and_migrate:
        parser.error('Stop writers, then pass --backup-and-migrate explicitly. No files changed.')
    target,count=prepare(Path(__file__).resolve().parents[1])
    print(f'Backed up {count} SQLite files privately to {target}. Additive migration complete. Tokens, credentials and .env were not accessed.')


if __name__=='__main__': main()
