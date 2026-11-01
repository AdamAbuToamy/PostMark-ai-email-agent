"""Native SQLite super-journal transaction for the two publication databases.

WAL remains the normal mail read/write mode. A short exclusive publication window
uses on-disk rollback journals and FULL synchronization for cross-file recovery.
SQLite recovers hot journals after process interruption; no application replay.
"""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import time
from email_agent.application.organization_contract import PublicationBusy


def publication_prerequisites(path, categories):
    paths = [Path(path), Path(categories)]
    if paths[0].resolve() == paths[1].resolve():
        raise ValueError('Publication requires separate mail and category database files')
    for file in paths:
        if not file.is_file() or not os.access(file, os.R_OK | os.W_OK) or not os.access(file.parent, os.W_OK):
            raise ValueError('Publication needs writable database files and journal directories; repair local storage permissions and retry')


@contextmanager
def taxonomy_transaction(path, categories):
    publication_prerequisites(path, categories)
    with closing(sqlite3.connect(path, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute('ATTACH DATABASE ? AS taxonomy', (str(categories),))
        modes = {schema: db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] for schema in ('main', 'taxonomy')}
        if any(mode not in ('wal', 'delete', 'truncate', 'persist') for mode in modes.values()):
            raise ValueError('Publication requires durable on-disk SQLite journals; repair the database journal configuration')
        try:
            # Exclusive mode retains the locks through COMMIT and WAL restoration.
            # Short-lived readers may finish first; never wait indefinitely.
            deadline = time.monotonic() + 3
            for schema in modes:
                db.execute(f'PRAGMA {schema}.locking_mode=EXCLUSIVE')
                while True:
                    try:
                        mode = db.execute(f'PRAGMA {schema}.journal_mode=DELETE').fetchone()[0]
                        if mode != 'delete':
                            raise ValueError('Could not establish durable rollback journals; no publication applied')
                        break
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc).lower() or time.monotonic() >= deadline:
                            raise PublicationBusy('Publication database is busy; the saved proposal will retry after the current local write') from exc
                        time.sleep(.05)
                db.execute(f'PRAGMA {schema}.synchronous=FULL')
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            if db.in_transaction:
                db.rollback()
            # An interrupted process may leave DELETE mode. Startup safely opens
            # and recovers SQLite journals before the normal repository enables WAL.
            for schema, mode in modes.items():
                current = db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0]
                if current != mode:
                    db.execute(f'PRAGMA {schema}.journal_mode={mode}')
