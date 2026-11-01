import sqlite3
from pathlib import Path

from email_agent.domain.account import EmailAccount


class SQLiteAccountRepository:
    """Stores email account metadata in SQLite."""

    def __init__(self, database_path: Path):
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL
                )
                """
            )

    def save(self, account: EmailAccount) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO accounts (id, provider)
                VALUES (?, ?)
                ON CONFLICT(id)
                DO UPDATE SET provider = excluded.provider
                """,
                (account.id, account.provider),
            )

    def get(self, account_id: str) -> EmailAccount | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT id, provider
                FROM accounts
                WHERE id = ?
                """,
                (account_id,),
            ).fetchone()

        if row is None:
            return None

        return EmailAccount(
            id=row[0],
            provider=row[1],
        )
