"""SQLite access with numbered SQL migrations (spec 10)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        files = sorted(
            (
                f
                for f in resources.files("micropub_server.migrations").iterdir()
                if f.name.endswith(".sql")
            ),
            key=lambda f: f.name,
        )
        for f in files:
            version = int(f.name.split("_", 1)[0])
            if version <= current:
                continue
            with self.transaction() as tx:
                for statement in _statements(f.read_text()):
                    tx.execute(statement)
                tx.execute(f"PRAGMA user_version = {version}")

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[Database]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def checkpoint(self) -> None:
        self.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        self._conn.close()


def _statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
