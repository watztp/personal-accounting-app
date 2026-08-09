from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "accounting.sqlite3"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


sqlite3.register_adapter(dict, lambda value: json.dumps(value, ensure_ascii=False, default=str))
sqlite3.register_adapter(list, lambda value: json.dumps(value, ensure_ascii=False, default=str))
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_converter("DATE", lambda raw: date.fromisoformat(raw.decode("utf-8")))


def database_path() -> Path:
    configured = os.getenv("ACCOUNTING_DB_PATH", "").strip()
    path = Path(configured) if configured else DEFAULT_DATABASE_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _translate_sql(sql: str) -> str:
    translated = re.sub(r"%\((\w+)\)s", r":\1", sql)
    translated = translated.replace("%s", "?")
    translated = re.sub(r"\bILIKE\b", "LIKE", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", translated, flags=re.IGNORECASE)
    return translated


class SQLiteCursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def execute(self, sql: str, params: Any = ()) -> SQLiteCursor:
        self._cursor.execute(_translate_sql(sql), params)
        return self

    def executemany(self, sql: str, params: Any) -> SQLiteCursor:
        self._cursor.executemany(_translate_sql(sql), params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def __enter__(self) -> SQLiteCursor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._cursor.close()


class SQLiteConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def cursor(self) -> SQLiteCursor:
        return SQLiteCursor(self._connection.cursor())

    def execute(self, sql: str, params: Any = ()) -> SQLiteCursor:
        cursor = self.cursor()
        return cursor.execute(sql, params)

    def executescript(self, sql: str) -> None:
        self._connection.executescript(sql)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def get_connection(path: Path | str | None = None) -> SQLiteConnection:
    target = Path(path) if path is not None else database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(
        target,
        timeout=30,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA busy_timeout = 30000")
    raw.execute("PRAGMA journal_mode = WAL")
    return SQLiteConnection(raw)


def ensure_schema(conn: SQLiteConnection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
