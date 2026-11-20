"""Encrypted SQLite (SQLCipher 4) connection management."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlcipher3 import dbapi2 as sqlcipher

from .schema import MIGRATIONS

_HEX_KEY = re.compile(r"^[0-9a-fA-F]{64}$")


class DatabaseError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _apply_key(conn: Any, key_hex: str) -> None:
    if not _HEX_KEY.match(key_hex):
        raise DatabaseError("database key must be 32 bytes hex-encoded")
    # Raw key syntax: avoids KDF and never stores the key in SQL logs.
    conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")


class Database:
    """One encrypted database per profile. Connections are per-thread."""

    def __init__(self, path: str | Path, key_hex: str):
        self.path = str(path)
        self._key = key_hex
        self._local = threading.local()
        self._write_lock = threading.RLock()
        conn = self.connect()
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher.DatabaseError as exc:
            raise DatabaseError("cannot open database: wrong key or corrupted file") from exc
        self.migrate()

    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlcipher.connect(self.path, timeout=15, check_same_thread=False, isolation_level=None)
            _apply_key(conn, self._key)
            try:
                conn.execute("PRAGMA cipher_log_level = NONE")
            except sqlcipher.DatabaseError:
                pass
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 15000")
            conn.row_factory = sqlcipher.Row
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlcipher.DatabaseError as exc:
                raise DatabaseError("cannot open database: wrong key or corrupted file") from exc
            self._local.conn = conn
        return conn

    # -- helpers -----------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] | dict = ()):
        return self.connect().execute(sql, params)

    def query(self, sql: str, params: Iterable[Any] | dict = ()) -> list[dict]:
        cur = self.connect().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] | dict = ()) -> dict | None:
        row = self.connect().execute(sql, params).fetchone()
        return dict(row) if row else None

    def transaction(self):
        return _Tx(self)

    # -- migrations --------------------------------------------------------------
    def migrate(self) -> int:
        conn = self.connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        n = 0
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            with self.transaction():
                for stmt in _split_sql(sql):
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)",
                    (version, name, utcnow()),
                )
            n += 1
        return n

    def schema_version(self) -> int:
        row = self.query_one("SELECT max(version) AS v FROM schema_migrations")
        return int(row["v"] or 0) if row else 0

    # -- preferences -------------------------------------------------------------
    def get_pref(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM preferences WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_pref(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO preferences(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), utcnow()),
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class _Tx:
    def __init__(self, db: Database):
        self.db = db
        self.conn = db.connect()

    def __enter__(self):
        self.db._write_lock.acquire()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.db._write_lock.release()
        return False


def _split_sql(script: str) -> list[str]:
    parts, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--") and not buf:
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt and stmt != ";":
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts
