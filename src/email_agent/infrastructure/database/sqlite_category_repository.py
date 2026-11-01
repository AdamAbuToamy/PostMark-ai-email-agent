import sqlite3
from contextlib import closing

from email_agent.domain.category_icons import validate_category_icon, default_category_icon
from pathlib import Path


class SQLiteCategoryRepository:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS category_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                )
            """)
            self._ensure_icon_columns(db)
            db.execute('CREATE TABLE IF NOT EXISTS local_category_accounts(category_id TEXT PRIMARY KEY,account_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS category_aliases(alias TEXT PRIMARY KEY,target TEXT NOT NULL)')


    def _ensure_icon_columns(self, db) -> None:
        from email_agent.domain.category_icons import (
            BUILTIN_CATEGORY_ICONS,
            DEFAULT_CATEGORY_ICON,
        )

        for table in ("categories", "category_proposals"):
            columns = {
                row[1]
                for row in db.execute(f"PRAGMA table_info({table})")
            }

            if "icon_key" not in columns:
                db.execute(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN icon_key TEXT NOT NULL "
                    "DEFAULT 'folder-open'"
                )

            id_column = (
                "id" if table == "categories" else "category_id"
            )

            for category_id, icon_key in BUILTIN_CATEGORY_ICONS.items():
                db.execute(
                    f"""
                    UPDATE {table}
                    SET icon_key = ?
                    WHERE {id_column} = ?
                      AND icon_key = ?
                    """,
                    (icon_key, category_id, DEFAULT_CATEGORY_ICON),
                )

    def ensure_category(
        self, category_id: str, name: str, source: str
    ) -> None:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("""
                INSERT OR IGNORE INTO categories (id, name, source, icon_key)
                VALUES (?, ?, ?, ?)
            """, (category_id, name, source, default_category_icon(category_id)))

    def list_categories(self, account_id=None) -> list[dict]:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.row_factory = sqlite3.Row
            return [
                dict(row) for row in db.execute("""
                    SELECT id, name, source, icon_key
                    FROM categories c WHERE source!='retired' AND (? IS NULL OR NOT EXISTS(SELECT 1 FROM local_category_accounts v WHERE v.category_id=c.id) OR EXISTS(SELECT 1 FROM local_category_accounts v WHERE v.category_id=c.id AND v.account_id=?))
                    ORDER BY name
                """,(account_id,account_id))
            ]

    def resolve(self, category_id):
        with closing(sqlite3.connect(self._db_path)) as db:
            seen=set()
            while category_id not in seen:
                seen.add(category_id)
                row=db.execute('SELECT target FROM category_aliases WHERE alias=?',(category_id,)).fetchone()
                if not row:return category_id
                category_id=row[0]
            raise ValueError('Category alias cycle; organization requires repair')

    def list_proposals(self) -> list[dict]:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.row_factory = sqlite3.Row
            return [
                dict(row) for row in db.execute("""
                    SELECT id, category_id, name, reason, status, icon_key
                    FROM category_proposals
                    ORDER BY id DESC
                """)
            ]

    def propose(
        self,
        category_id: str,
        name: str,
        reason: str,
        icon_key: str,
    ) -> bool:
        validate_category_icon(icon_key)

        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")

            existing = db.execute("""
                SELECT 1 FROM categories WHERE id = ?
            """, (category_id,)).fetchone()

            if existing:
                return False

            cursor = db.execute("""
                INSERT OR IGNORE INTO category_proposals
                    (category_id, name, reason, icon_key)
                VALUES (?, ?, ?, ?)
            """, (category_id, name, reason, icon_key))

            return cursor.rowcount == 1

    def approve(self, proposal_id: int) -> bool:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")

            proposal = db.execute("""
                SELECT category_id, name, icon_key
                FROM category_proposals
                WHERE id = ? AND status = 'pending'
            """, (proposal_id,)).fetchone()

            if proposal is None:
                return False

            db.execute("""
                INSERT OR IGNORE INTO categories
                    (id, name, icon_key, source)
                VALUES (?, ?, ?, 'approved')
            """, proposal)

            db.execute("""
                UPDATE category_proposals
                SET status = 'approved'
                WHERE id = ? AND status = 'pending'
            """, (proposal_id,))

            return True

    def reject(self, proposal_id: int) -> bool:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            cursor = db.execute("""
                UPDATE category_proposals
                SET status = 'rejected'
                WHERE id = ? AND status = 'pending'
            """, (proposal_id,))

            return cursor.rowcount == 1
