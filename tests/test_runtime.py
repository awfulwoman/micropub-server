import io
import json
import logging

import pytest

from micropub_server.bootstrap import ConfigError, build_state
from micropub_server.cli import main
from micropub_server.config import Settings
from micropub_server.logs import JsonFormatter

CONFIG = """
store:
  type: filesystem
  directory: {site}
post_types:
  note:
    name: Note
    post: {{path: "content/notes/{{yyyy}}/{{slug}}.md"}}
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG.format(site=site))
    from micropub_server.auth.passwords import hash_password

    values = {
        "CONFIG_FILE": str(cfg),
        "DATA_DIR": str(tmp_path / "data"),
        "BASE_URL": "https://indie.awfulwoman.com/",
        "PUBLICATION_URL": "https://awfulwoman.com/",
        "SECRET": "x" * 32,
        "PASSWORD_HASH": hash_password("pw"),
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


def test_build_state(env):
    s = build_state(Settings())
    assert s.me == "https://awfulwoman.com/"
    assert s.store.name == "File system"


def test_requires_a_password_hash(env, monkeypatch):
    monkeypatch.delenv("PASSWORD_HASH")
    with pytest.raises(ConfigError, match="PASSWORD_HASH"):
        build_state(Settings())


def test_accepts_legacy_password_secret(env, monkeypatch):
    monkeypatch.delenv("PASSWORD_HASH")
    monkeypatch.setenv(
        "PASSWORD_SECRET", "$2b$04$abcdefghijklmnopqrstuuabcdefghijklmnopqrstuvwxyz01234"
    )
    build_state(Settings())


def test_requires_a_strong_secret(env, monkeypatch):
    monkeypatch.setenv("SECRET", "short")
    with pytest.raises(ConfigError, match="SECRET"):
        build_state(Settings())


def test_github_store_needs_token(env, monkeypatch, tmp_path):
    cfg = tmp_path / "gh.yaml"
    cfg.write_text("store:\n  type: github\n  owner: o\n  repo: r\n")
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        build_state(Settings())


def test_missing_config_file(env, monkeypatch):
    monkeypatch.setenv("CONFIG_FILE", "/nonexistent.yaml")
    with pytest.raises(ConfigError, match="config"):
        build_state(Settings())


def test_config_path_is_a_directory(env, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CONFIG_FILE", str(tmp_path)
    )  # what Docker creates for a missing bind-mount source
    with pytest.raises(ConfigError, match="cannot read config file"):
        build_state(Settings())


def test_publication_url_must_be_https_url(env, monkeypatch):
    monkeypatch.setenv("PUBLICATION_URL", "not a url")
    with pytest.raises(ConfigError, match="PUBLICATION_URL"):
        build_state(Settings())


# --- health -----------------------------------------------------------------
def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "store": "ok", "db": "ok", "version": "test"}


def test_health_degraded_store_still_200(client, state):
    state.store_problem = "GitHub token cannot write to o/r: it needs Contents: Read and write."
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["store"] == "degraded"


def test_health_503_when_db_broken(client, state):
    state.db.close()
    assert client.get("/health").status_code == 503


# --- logging ----------------------------------------------------------------
def test_json_formatter():
    record = logging.LogRecord("x", logging.INFO, "f", 1, "hello %s", ("w",), None)
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "hello w" and out["level"] == "info" and "time" in out


def test_request_log_never_contains_query_string(client, token, caplog):
    with caplog.at_level(logging.INFO, logger="micropub_server.access"):
        client.get("/micropub", params={"q": "config", "access_token": token})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "/micropub" in text and token not in text and "access_token" not in text


# --- cli --------------------------------------------------------------------
def test_hash_password_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("secret-pw\n"))
    assert main(["hash-password"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("$argon2id$")
    from micropub_server.auth.passwords import verify_password

    assert verify_password("secret-pw", password_hash=out)


def test_hash_password_empty_is_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert main(["hash-password"]) == 1


def test_check_ok(env, capsys):
    assert main(["check"]) == 0
    assert "ok" in capsys.readouterr().out.lower()


def test_check_reports_config_problem(env, monkeypatch, capsys):
    monkeypatch.delenv("PASSWORD_HASH")
    assert main(["check"]) == 1
    assert "PASSWORD_HASH" in capsys.readouterr().err


def test_migrate(env, tmp_path):
    assert main(["migrate"]) == 0
    assert (tmp_path / "data" / "micropub.db").exists()


def test_import_command(env, tmp_path, capsys):
    site = tmp_path / "site"
    (site / "content/notes/2025").mkdir(parents=True)
    (site / "content/notes/2025/hello.md").write_text(
        "---\ntitle: Hello\ndate: '2025-02-11T18:55:20+01:00'\n---\nBody\n"
    )
    assert main(["import", "--source", str(site), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would import 1" in out and "note: 1" in out
    assert main(["import", "--source", str(site)]) == 0
    assert "imported 1" in capsys.readouterr().out
