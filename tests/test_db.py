import sqlite3

from micropub_server.db import Database


def test_migrates_fresh_database(tmp_path):
    db = Database(tmp_path / "t.db")
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"posts", "media", "auth_codes", "tokens", "auth_requests", "cache"} <= tables
    assert db.query("PRAGMA user_version")[0][0] >= 1


def test_migrations_idempotent(tmp_path):
    Database(tmp_path / "t.db")
    Database(tmp_path / "t.db")  # second open must not fail or re-run


def test_wal_and_foreign_keys(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"
    assert db.query("PRAGMA foreign_keys")[0][0] == 1


def test_execute_and_query(tmp_path):
    db = Database(tmp_path / "t.db")
    db.execute(
        "INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?)", ("k", "v", "2099-01-01")
    )
    rows = db.query("SELECT * FROM cache WHERE key = ?", ("k",))
    assert rows[0]["value"] == "v"


def test_transaction_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO cache VALUES ('a', 'b', 'c')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.query("SELECT * FROM cache") == []


def test_unique_url(tmp_path):
    db = Database(tmp_path / "t.db")
    ins = (
        "INSERT INTO posts (uid, url, path, post_type, published, properties) VALUES (?,?,?,?,?,?)"
    )
    db.execute(ins, ("1", "https://x/a/", "p/a.md", "note", "2026-01-01", "{}"))
    try:
        db.execute(ins, ("2", "https://x/a/", "p/b.md", "note", "2026-01-01", "{}"))
    except sqlite3.IntegrityError:
        return
    raise AssertionError("duplicate url accepted")
