from contextlib import closing
import json
import sqlite3
from pathlib import Path

from email_agent.domain.message import EmailMessage
from email_agent.domain.search_text import normalize_search_text


class SQLiteMessageRepository:
    """SQLite storage with account-level message isolation."""

    def __init__(self, database_path: Path):
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self._database_path)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    PRIMARY KEY (account_id, message_id)
                )
            """)

        # Safe schema migration for existing databases.
        with closing(sqlite3.connect(self._database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(messages)"
                )
            }
            # v2: legacy empty labels are unknown; nonempty labels are evidence.
            if "labels_known" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN labels_known INTEGER NOT NULL DEFAULT 0")
                if "label_ids" in columns:
                    connection.execute("UPDATE messages SET labels_known = 1 WHERE label_ids != '[]'")
            connection.execute("CREATE TABLE IF NOT EXISTS message_schema_versions (version INTEGER PRIMARY KEY)")
            connection.execute("INSERT OR IGNORE INTO message_schema_versions VALUES (2)")
            connection.execute("CREATE TABLE IF NOT EXISTS message_list_headers(account_id TEXT,message_id TEXT,headers TEXT NOT NULL,PRIMARY KEY(account_id,message_id))")
            connection.execute("INSERT OR IGNORE INTO message_schema_versions VALUES (3)")
            connection.execute("""CREATE TABLE IF NOT EXISTS message_search_content (
                account_id TEXT NOT NULL,message_id TEXT NOT NULL,body_text TEXT NOT NULL,
                PRIMARY KEY(account_id,message_id))""")
            connection.execute("INSERT OR IGNORE INTO message_schema_versions VALUES (4)")
            connection.execute("CREATE TABLE IF NOT EXISTS message_content_state(account_id TEXT,message_id TEXT,status TEXT NOT NULL,characters INTEGER NOT NULL,digest TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(account_id,message_id))")
            connection.execute("INSERT OR IGNORE INTO message_schema_versions VALUES (5)")
            if "label_ids" not in columns:
                connection.execute(
                    "ALTER TABLE messages "
                    "ADD COLUMN label_ids TEXT NOT NULL DEFAULT '[]'"
                )

            if "internal_date_ms" not in columns:
                connection.execute(
                    "ALTER TABLE messages "
                    "ADD COLUMN internal_date_ms INTEGER NOT NULL DEFAULT 0"
                )

    @property
    def database_path(self) -> Path:
        return self._database_path

    def save(self, message: EmailMessage) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection, connection:
            self.save_in_transaction(connection, message)

    @staticmethod
    def save_in_transaction(connection, message: EmailMessage) -> None:
        connection.execute("""
            INSERT INTO messages
                (account_id, message_id, sender, subject, internal_date_ms, label_ids, labels_known)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, message_id)
            DO UPDATE SET
                sender = excluded.sender,
                subject = excluded.subject,
                internal_date_ms = CASE
                    WHEN excluded.internal_date_ms > 0
                    THEN excluded.internal_date_ms
                    ELSE messages.internal_date_ms
                END,
                label_ids = CASE
                    WHEN excluded.labels_known = 1
                    THEN excluded.label_ids
                    ELSE messages.label_ids
                END,
                labels_known = MAX(messages.labels_known, excluded.labels_known)
        """, (
            message.account_id,
            message.id,
            message.sender,
            message.subject,
            message.internal_date_ms,
            json.dumps(message.label_ids or ()),
            int(message.label_ids is not None),
        ))
        if message.list_headers is not None:
            connection.execute("INSERT INTO message_list_headers VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET headers=excluded.headers", (message.account_id, message.id, json.dumps(dict(message.list_headers))))

    def list_by_account(
        self,
        account_id: str,
        limit: int = 50,
    ) -> list[EmailMessage]:
        if not 1 <= limit <= 1000:
            raise ValueError("Limit must be between 1 and 1000")

        with closing(sqlite3.connect(self._database_path)) as connection, connection:
            rows = connection.execute("""
                SELECT message_id, account_id, sender, subject,
                       internal_date_ms, label_ids, labels_known
                FROM messages
                WHERE account_id = ?
                ORDER BY internal_date_ms DESC, rowid DESC
                LIMIT ?
            """, (account_id, limit)).fetchall()

        return [
            EmailMessage(
                id=row[0],
                account_id=row[1],
                sender=row[2],
                subject=row[3],
                internal_date_ms=row[4],
                label_ids=tuple(json.loads(row[5])) if row[6] else None,
            )
            for row in rows
        ]

    def count_by_account(self, account_id: str) -> int:
        with closing(sqlite3.connect(self._database_path)) as connection, connection:
            row = connection.execute("""
                SELECT COUNT(*)
                FROM messages
                WHERE account_id = ?
            """, (account_id,)).fetchone()

        return int(row[0])

    def get(self, account_id: str, message_id: str) -> EmailMessage | None:
        with closing(sqlite3.connect(self._database_path)) as db, db:
            row = db.execute("SELECT m.message_id,m.account_id,m.sender,m.subject,m.internal_date_ms,m.label_ids,m.labels_known,h.headers FROM messages m LEFT JOIN message_list_headers h ON h.account_id=m.account_id AND h.message_id=m.message_id WHERE m.account_id=? AND m.message_id=?", (account_id, message_id)).fetchone()
        if row is None:
            return None
        return EmailMessage(*row[:5], tuple(json.loads(row[5])) if row[6] else None, tuple(json.loads(row[7]).items()) if row[7] is not None else None)

    def scan(self, account_id: str, after: str = "", limit: int = 100) -> list[EmailMessage]:
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid page size")
        with closing(sqlite3.connect(self._database_path)) as db, db:
            rows = db.execute("SELECT message_id, account_id, sender, subject, internal_date_ms, label_ids, labels_known FROM messages WHERE account_id=? AND message_id>? ORDER BY message_id LIMIT ?", (account_id, after, limit)).fetchall()
        return [EmailMessage(*r[:5], tuple(json.loads(r[5])) if r[6] else None) for r in rows]

    def update_labels(self, account_id, message_id, labels):
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("UPDATE messages SET label_ids=?,labels_known=1 WHERE account_id=? AND message_id=?", (json.dumps(list(labels)), account_id, message_id))

    def record_list_headers(self, account_id, message_id, headers):
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS message_list_headers(account_id TEXT,message_id TEXT,headers TEXT NOT NULL,PRIMARY KEY(account_id,message_id))")
            if db.execute("SELECT 1 FROM messages WHERE account_id=? AND message_id=?", (account_id, message_id)).fetchone():
                db.execute("INSERT INTO message_list_headers VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET headers=excluded.headers", (account_id, message_id, json.dumps(headers)))

    def record_search_content(self, account, message_id, body_text, *, complete=False):
        if not isinstance(body_text,str):
            raise ValueError("Indexed content must be plain text")
        if len(body_text)>2_000_000:raise ValueError("Body exceeds safe local text bound; not stored as complete")
        with closing(sqlite3.connect(self._database_path)) as db, db:
            if not db.execute("SELECT 1 FROM messages WHERE account_id=? AND message_id=?",(account,message_id)).fetchone():
                raise LookupError("Message not found in this account")
            has_state=db.execute("SELECT 1 FROM sqlite_master WHERE name='message_content_state'").fetchone() is not None
            if has_state and not complete and db.execute("SELECT 1 FROM message_content_state WHERE account_id=? AND message_id=? AND status='complete'",(account,message_id)).fetchone():return
            before=db.execute('SELECT body_text FROM message_search_content WHERE account_id=? AND message_id=?',(account,message_id)).fetchone()
            previous_state=db.execute('SELECT status FROM message_content_state WHERE account_id=? AND message_id=?',(account,message_id)).fetchone() if has_state else None
            db.execute("INSERT INTO message_search_content VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET body_text=excluded.body_text",(account,message_id,body_text))
            if has_state:
                import hashlib
                db.execute("INSERT INTO message_content_state VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET status=excluded.status,characters=excluded.characters,digest=excluded.digest,source=excluded.source",(account,message_id,'complete' if complete else 'unverified',len(body_text),hashlib.sha256(body_text.encode()).hexdigest(),'gmail_raw' if complete else 'legacy_or_preview'))
            changed=before is None or before[0]!=body_text or bool(previous_state and previous_state[0]=='complete')!=complete
            if changed and db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_work'").fetchone():
                db.execute("UPDATE mail_work SET status='pending',fingerprint='content_changed',attempts=0,lease=0,due=0,error_kind=NULL WHERE account_id=? AND message_id=? AND stage='local'",(account,message_id))
            if (before is None or before[0]!=body_text) and db.execute("SELECT 1 FROM sqlite_master WHERE name='content_review_queue'").fetchone():
                db.execute("UPDATE content_review_queue SET state='pending',basis=NULL,changed=0 WHERE account_id=? AND message_id=?",(account,message_id))

    def search_local(self, account_id, search="", category=None, state="all", label=None, limit=50, classification=None):
        return self._search_local(account_id,search,category,state,label,limit,classification,0,False)["messages"]

    def search_local_page(self, account_id, search="", category=None, state="all", label=None, limit=50, classification=None, offset=0):
        return self._search_local(account_id,search,category,state,label,limit,classification,offset,True)

    def _search_local(self, account_id, search, category, state, label, limit, classification, offset, counted):
        if classification not in (None,"unclassified") or state not in ("new","done","all"):
            raise ValueError("Unsupported search filter")
        if type(limit) is not int or not 1<=limit<=(100 if counted else 1000) or type(offset) is not int or not 0<=offset<=1000000:
            raise ValueError("Invalid local search page")
        if not isinstance(search,str) or len(search)>200:
            raise ValueError("Search text must be at most 200 characters")
        source="""FROM messages m LEFT JOIN message_category_assignments a
            ON a.account_id=m.account_id AND a.message_id=m.message_id
            LEFT JOIN local_message_state s ON s.account_id=m.account_id AND s.message_id=m.message_id
            LEFT JOIN message_search_content c ON c.account_id=m.account_id AND c.message_id=m.message_id
            WHERE m.account_id=? AND (?='' OR instr(fold(m.sender),fold(?)) OR instr(fold(m.subject),fold(?)) OR instr(fold(COALESCE(c.body_text,'')),fold(?)))
            AND (? IS NULL OR a.message_id IS NULL) AND (? IS NULL OR a.category_id=?)
            AND (?='all' OR COALESCE(s.state,'new')=?)
            AND (? IS NULL OR (m.labels_known=1 AND EXISTS(SELECT 1 FROM json_each(m.label_ids) WHERE value=?)))"""
        parameters=(account_id,search,search,search,search,classification,category,category,state,state,label,label)
        with closing(sqlite3.connect(self._database_path)) as db:
            db.row_factory=sqlite3.Row
            db.create_function("fold",1,normalize_search_text,deterministic=True)
            db.execute("BEGIN")
            total=db.execute("SELECT COUNT(*) "+source,parameters).fetchone()[0] if counted else None
            rows=db.execute("""SELECT m.message_id,m.account_id,m.sender,m.subject,m.internal_date_ms,
                a.category_id,a.status AS classification_status,a.source,COALESCE(s.state,'new') AS local_state """+source+
                " ORDER BY m.internal_date_ms DESC,m.message_id DESC LIMIT ? OFFSET ?",(*parameters,limit,offset)).fetchall()
        messages=[dict(row) for row in rows]
        return {"messages":messages,"matched_count":total,"offset":offset,"limit":limit,
                "next_offset":offset+len(messages) if counted and offset+len(messages)<total else None,
                "coverage":"Sender, subject and locally indexed opened-message text; no Gmail or AI request."}

    def list_header_rows(self, account_id):
        with closing(sqlite3.connect(self._database_path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='message_list_headers'").fetchone():
                return []
            return [(r[0], json.loads(r[1])) for r in db.execute("SELECT h.message_id,h.headers FROM message_list_headers h JOIN message_category_assignments a ON a.account_id=h.account_id AND a.message_id=h.message_id JOIN messages m ON m.account_id=h.account_id AND m.message_id=h.message_id WHERE h.account_id=? AND a.category_id='promotions' ORDER BY m.internal_date_ms DESC,m.message_id DESC LIMIT 1000", (account_id,))]

    def missing_list_headers(self, account_id, limit=20):
        with closing(sqlite3.connect(self._database_path)) as db:
            return [row[0] for row in db.execute("""SELECT a.message_id FROM message_category_assignments a
                LEFT JOIN message_list_headers h ON h.account_id=a.account_id AND h.message_id=a.message_id
                WHERE a.account_id=? AND a.category_id='promotions' AND h.message_id IS NULL
                ORDER BY a.message_id LIMIT ?""", (account_id,limit))]

    def initialize_discovery(self):
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_metadata_scan(account_id TEXT NOT NULL,message_id TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL,retry_at REAL NOT NULL,PRIMARY KEY(account_id,message_id))")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_scan_lease(account_id TEXT PRIMARY KEY,expires REAL NOT NULL,owner TEXT NOT NULL)")
            if "owner" not in {r[1] for r in db.execute("PRAGMA table_info(subscription_scan_lease)")}:
                db.execute("ALTER TABLE subscription_scan_lease ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE TABLE IF NOT EXISTS subscription_schema_versions(version INTEGER PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO subscription_schema_versions VALUES (1)")

    def subscription_rows(self, account, limit=1000):
        with closing(sqlite3.connect(self._database_path)) as db:
            db.row_factory=sqlite3.Row
            rows=db.execute("""SELECT m.message_id,m.account_id,m.sender,m.subject,m.internal_date_ms,m.label_ids,a.status,h.headers
                FROM messages m JOIN message_category_assignments a ON a.account_id=m.account_id AND a.message_id=m.message_id
                LEFT JOIN message_list_headers h ON h.account_id=m.account_id AND h.message_id=m.message_id
                WHERE m.account_id=? AND a.category_id='promotions'
                ORDER BY m.internal_date_ms DESC,m.message_id DESC LIMIT ?""",(account,limit)).fetchall()
        return [{**dict(r),"headers":json.loads(r["headers"]) if r["headers"] is not None else None,"label_ids":json.loads(r["label_ids"])} for r in rows]

    def claim_discovery(self, account):
        import time
        import uuid
        self.initialize_discovery()
        owner=str(uuid.uuid4())
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT expires FROM subscription_scan_lease WHERE account_id=?",(account,)).fetchone()
            if row and row[0]>time.time(): return None
            db.execute("INSERT INTO subscription_scan_lease(account_id,expires,owner) VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET expires=excluded.expires,owner=excluded.owner",(account,time.time()+900,owner))
            return owner

    def release_discovery(self, account, owner):
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("DELETE FROM subscription_scan_lease WHERE account_id=? AND owner=?",(account,owner))

    def discovery_candidates(self, account, limit=20):
        import time
        self.initialize_discovery()
        with closing(sqlite3.connect(self._database_path)) as db:
            return [r[0] for r in db.execute("""SELECT a.message_id FROM message_category_assignments a
                LEFT JOIN message_list_headers h ON h.account_id=a.account_id AND h.message_id=a.message_id
                LEFT JOIN subscription_metadata_scan s ON s.account_id=a.account_id AND s.message_id=a.message_id
                WHERE a.account_id=? AND a.category_id='promotions' AND h.message_id IS NULL
                AND (s.message_id IS NULL OR (s.status='failed' AND s.attempts<3 AND s.retry_at<=?))
                ORDER BY a.message_id LIMIT ?""",(account,time.time(),limit))]

    def record_discovery(self, account, id, status, headers):
        import time
        with closing(sqlite3.connect(self._database_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM messages WHERE account_id=? AND message_id=?",(account,id)).fetchone():
                raise LookupError("Message not found in this account")
            if status=='succeeded':
                db.execute("INSERT INTO message_list_headers VALUES (?,?,?) ON CONFLICT(account_id,message_id) DO UPDATE SET headers=excluded.headers",(account,id,json.dumps(headers)))
            db.execute("INSERT INTO subscription_metadata_scan VALUES (?,?,?,1,?) ON CONFLICT(account_id,message_id) DO UPDATE SET status=excluded.status,attempts=attempts+1,retry_at=excluded.retry_at",(account,id,status,time.time()+300 if status=='failed' else 0))

    def discovery_status(self, account):
        import time
        self.initialize_discovery()
        with closing(sqlite3.connect(self._database_path)) as db:
            total,indexed=db.execute("""SELECT COUNT(*),COUNT(h.message_id) FROM message_category_assignments a
                LEFT JOIN message_list_headers h ON h.account_id=a.account_id AND h.message_id=a.message_id
                WHERE a.account_id=? AND a.category_id='promotions'""",(account,)).fetchone()
            failed,skipped,exhausted=db.execute("SELECT COALESCE(SUM(s.status='failed'),0),COALESCE(SUM(s.status='skipped'),0),COALESCE(SUM(s.status='failed' AND s.attempts>=3),0) FROM subscription_metadata_scan s JOIN message_category_assignments a ON a.account_id=s.account_id AND a.message_id=s.message_id LEFT JOIN message_list_headers h ON h.account_id=s.account_id AND h.message_id=s.message_id WHERE s.account_id=? AND a.category_id='promotions' AND h.message_id IS NULL",(account,)).fetchone()
            lease=db.execute("SELECT expires FROM subscription_scan_lease WHERE account_id=?",(account,)).fetchone()
            ready=db.execute("""SELECT COUNT(*) FROM message_category_assignments a
                LEFT JOIN message_list_headers h ON h.account_id=a.account_id AND h.message_id=a.message_id
                LEFT JOIN subscription_metadata_scan s ON s.account_id=a.account_id AND s.message_id=a.message_id
                WHERE a.account_id=? AND a.category_id='promotions' AND h.message_id IS NULL
                AND (s.message_id IS NULL OR (s.status='failed' AND s.attempts<3 AND s.retry_at<=?))""",(account,time.time())).fetchone()[0]
        return {"total":total,"indexed":indexed,"missing":total-indexed,"failed":failed,"skipped":skipped,"exhausted":exhausted,"ready":ready,"running":bool(lease and lease[0]>time.time()),"page_size":20,"complete":total-indexed-skipped==0,"coverage_limit":1000}
