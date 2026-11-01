import json
import sqlite3
from pathlib import Path


class SQLiteAnalysisReader:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)

    def read_results(self) -> list[dict]:
        if not self._db_path.is_file():
            return []

        uri = self._db_path.resolve().as_uri() + "?mode=ro"

        with sqlite3.connect(uri, uri=True) as db:
            rows = db.execute("""
                SELECT result FROM analyses
            """).fetchall()

        results = []

        for (raw,) in rows:
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    results.append(value)
            except (ValueError, TypeError):
                continue

        return results
