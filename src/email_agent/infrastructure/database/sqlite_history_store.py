import sqlite3
from contextlib import closing
from email_agent.infrastructure.database.message_repository import SQLiteMessageRepository


class SQLiteHistoryStore:
    def __init__(self, path):
        self.path = path
        SQLiteMessageRepository(path).initialize()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS history_progress (
                account_id TEXT PRIMARY KEY, next_page_token TEXT,
                pages_completed INTEGER NOT NULL DEFAULT 0,
                messages_processed INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0)""")

            db.execute("CREATE TABLE IF NOT EXISTS history_observations(account_id TEXT NOT NULL,message_id TEXT NOT NULL,fetched INTEGER NOT NULL,PRIMARY KEY(account_id,message_id))")
            db.execute("CREATE TABLE IF NOT EXISTS history_observation_runs(account_id TEXT PRIMARY KEY,legacy_pages INTEGER NOT NULL DEFAULT 0)")
            db.execute("INSERT OR IGNORE INTO history_observation_runs SELECT account_id,pages_completed FROM history_progress")
            db.execute("CREATE TABLE IF NOT EXISTS history_schema_versions(version INTEGER PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO history_schema_versions VALUES (1)")
            db.execute("CREATE TABLE IF NOT EXISTS incremental_label_cursor(account_id TEXT PRIMARY KEY,cursor INTEGER NOT NULL DEFAULT 0)")
            db.execute("INSERT OR IGNORE INTO history_schema_versions VALUES (2)")

    def coverage(self, account_id):
        with closing(sqlite3.connect(self.path)) as db:
            discovered, fetched, indexed = db.execute("""SELECT COUNT(*),COALESCE(SUM(o.fetched),0),COUNT(m.message_id)
                FROM history_observations o LEFT JOIN messages m USING(account_id,message_id)
                WHERE o.account_id=?""", (account_id,)).fetchone()
            classified = 0
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='message_category_assignments'").fetchone():
                classified = db.execute("""SELECT COUNT(*) FROM history_observations o JOIN message_category_assignments a USING(account_id,message_id)
                    WHERE o.account_id=? AND o.fetched=1 AND a.status='classified'""", (account_id,)).fetchone()[0]
            legacy = db.execute('SELECT legacy_pages FROM history_observation_runs WHERE account_id=?', (account_id,)).fetchone()
        state = self.progress(account_id)
        return dict(discovered=discovered,fetched=fetched,indexed=indexed,classified=classified,
            unavailable_observed=discovered-fetched,metrics_partial=bool(legacy and legacy[0]),
            remaining_pages=0 if state['completed'] else None,
            scope='All Mail including Spam and Trash',completion_basis='Gmail pagination token exhaustion only')

    def progress(self, account_id):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM history_progress WHERE account_id=?", (account_id,)).fetchone()
        return dict(row) if row else dict(account_id=account_id, next_page_token=None,
            pages_completed=0, messages_processed=0, completed=0)

    def import_legacy(self, account_id, legacy_path):
        if not legacy_path.exists():
            return
        with closing(sqlite3.connect(legacy_path.resolve().as_uri() + "?mode=ro", uri=True)) as old:
            if not old.execute("SELECT 1 FROM sqlite_master WHERE name='history_progress'").fetchone():
                return
            row = old.execute("SELECT account_id,next_page_token,pages_completed,messages_processed,completed FROM history_progress WHERE account_id=?", (account_id,)).fetchone()
        if row:
            with closing(sqlite3.connect(self.path)) as db, db:
                inserted = db.execute("INSERT OR IGNORE INTO history_progress VALUES (?,?,?,?,?)", row)
                if inserted.rowcount:
                    db.execute("INSERT OR REPLACE INTO history_observation_runs VALUES (?,?)", (account_id,row[2]))

    def restart(self, account_id):
        # Explicit recheck only; preserves every stored message and assignment.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM history_observations WHERE account_id=?', (account_id,))
            db.execute('INSERT OR REPLACE INTO history_observation_runs VALUES (?,0)', (account_id,))
            db.execute("INSERT INTO history_progress(account_id) VALUES (?) ON CONFLICT(account_id) DO UPDATE SET next_page_token=NULL,pages_completed=0,messages_processed=0,completed=0", (account_id,))

    def commit_page(self, account_id, expected_token, messages, next_token):
        self.commit_observed_page(account_id, expected_token, messages, next_token, None)

    def commit_observed_page(self, account_id, expected_token, messages, next_token, discovered):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO history_progress(account_id) VALUES (?)", (account_id,))
            token, done = db.execute("SELECT next_page_token,completed FROM history_progress WHERE account_id=?", (account_id,)).fetchone()
            if token != expected_token or done:
                raise RuntimeError("Historical checkpoint changed")
            db.execute('INSERT OR IGNORE INTO history_observation_runs VALUES (?,?)', (account_id,0))
            if discovered is None:
                db.execute('UPDATE history_observation_runs SET legacy_pages=legacy_pages+1 WHERE account_id=?', (account_id,))
            if discovered is not None:
                if len(set(discovered)) != len(discovered) or not {m.id for m in messages} <= set(discovered):
                    raise ValueError('Invalid historical observation identifiers')
                fetched = {message.id for message in messages}
                for message_id in discovered:
                    db.execute("INSERT INTO history_observations VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET fetched=MAX(history_observations.fetched,excluded.fetched)", (account_id,message_id,int(message_id in fetched)))
            for message in messages:
                if message.account_id != account_id:
                    raise ValueError("Account mismatch")
                SQLiteMessageRepository.save_in_transaction(db, message)
            db.execute("UPDATE history_progress SET next_page_token=?,pages_completed=pages_completed+1,messages_processed=messages_processed+?,completed=? WHERE account_id=?", (next_token,len(messages),int(next_token is None),account_id))

    def label_page(self, account, limit):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            row=db.execute('SELECT cursor FROM incremental_label_cursor WHERE account_id=?',(account,)).fetchone()
            cursor=row[0] if row else 0
            query="""SELECT rowid,message_id FROM messages WHERE account_id=? AND rowid>? AND labels_known=1
                AND EXISTS(SELECT 1 FROM json_each(messages.label_ids) WHERE value IN ('SPAM','TRASH'))
                ORDER BY rowid LIMIT ?"""
            rows=db.execute(query,(account,cursor,limit)).fetchall()
            if not rows:rows=db.execute(query,(account,0,limit)).fetchall()
        return cursor,rows

    def commit_labels(self, account, cursor, rows, messages):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO incremental_label_cursor(account_id) VALUES (?)',(account,))
            current=db.execute('SELECT cursor FROM incremental_label_cursor WHERE account_id=?',(account,)).fetchone()[0]
            if cursor!=current:raise RuntimeError('Label refresh checkpoint changed')
            ids={id for _,id in rows}
            for message in messages:
                if message.account_id!=account or message.id not in ids:raise ValueError('Invalid label refresh message')
                SQLiteMessageRepository.save_in_transaction(db,message)
            db.execute('UPDATE incremental_label_cursor SET cursor=? WHERE account_id=?',(rows[-1][0] if rows else 0,account))
