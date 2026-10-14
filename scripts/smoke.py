#!/usr/bin/env python3
"""Smoke-test a built image: start it, sign in through IndieAuth, post a note.

Usage: uv run python scripts/smoke.py micropub-server:smoke
Needs Docker. Exits non-zero on the first failure.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

PASSWORD = "smoke-test-password"
CLIENT_ID = "https://app.example/"
REDIRECT_URI = "https://app.example/callback"
VERIFIER = "smoke-verifier-" + "x" * 30
CONFIG = """\
store:
  type: filesystem
  directory: /site
post_types:
  note:
    name: Note
    post:
      path: content/notes/{yyyy}/{MM}/{dd}/{HH}{mm}.md
      url: notes/{yyyy}/{MM}/{dd}/{HH}{mm}/
"""


def sh(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw)


def step(message: str) -> None:
    print(f"- {message}", flush=True)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def main(image: str) -> None:
    # Under the repo, not /tmp: Colima and Docker Desktop share $HOME but not the system temp dir.
    root = Path(__file__).resolve().parent.parent / ".smoke"
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    site = work / "site"
    site.mkdir()
    site.chmod(0o777)  # the container runs as uid 1000
    (work / "config.yaml").write_text(CONFIG)

    step("hash the password with the image")
    hashed = subprocess.run(
        ["docker", "run", "--rm", "-i", image, "hash-password"],
        input=PASSWORD + "\n", text=True, capture_output=True, check=True,
    ).stdout.strip()  # fmt: skip
    if not hashed.startswith("$argon2id$"):
        fail(f"unexpected hash output: {hashed!r}")

    name = f"micropub-smoke-{os.getpid()}"
    step("start the container (read-only, non-root)")
    sh(
        "docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8000", "--read-only",
        "--tmpfs", "/tmp",
        "-v", f"{work / 'config.yaml'}:/config/config.yaml:ro",
        "-v", f"{site}:/site",
        "-e", "BASE_URL=http://localhost:8000/",
        "-e", "PUBLICATION_URL=https://awfulwoman.com/",
        "-e", "SECRET=" + "s" * 32,
        "-e", f"PASSWORD_HASH={hashed}",
        image,
    )  # fmt: skip
    try:
        port = sh("docker", "port", name, "8000/tcp").stdout.split(":")[-1].strip()
        base = f"http://127.0.0.1:{port}"
        run(base, name, site)
    finally:
        logs = subprocess.run(["docker", "logs", name], text=True, capture_output=True)
        if os.environ.get("SMOKE_LOGS"):
            print(logs.stdout, logs.stderr)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def run(base: str, name: str, site: Path) -> None:
    step("wait for /health")
    deadline = time.time() + 60
    while True:
        try:
            r = httpx.get(f"{base}/health", timeout=2)
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            print(subprocess.run(["docker", "logs", name], text=True, capture_output=True).stdout)
            fail("server did not become healthy within 60 seconds")
        time.sleep(0.5)
    health = r.json()
    if health["status"] != "ok" or health["db"] != "ok" or health["store"] != "ok":
        fail(f"unhealthy: {health}")

    step("check the container reports healthy to Docker")
    for _ in range(40):
        state = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name).stdout.strip()
        if state == "healthy":
            break
        time.sleep(2)
    else:
        fail(f"docker health status is {state!r}")

    step("sign in through IndieAuth (PKCE) and get a token")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=")
    with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as c:
        r = c.get("/auth", params={
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
            "state": "smoke", "scope": "create update delete", "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        })  # fmt: skip
        if r.status_code != 302:
            fail(f"/auth returned {r.status_code}: {r.text[:200]}")
        consent = urlsplit(r.headers["location"])
        r = c.post(
            f"{consent.path}?{consent.query}",
            data={"password": PASSWORD, "scope": ["create", "update", "delete"]},
        )
        if r.status_code != 302:
            fail(f"consent returned {r.status_code}: {r.text[:200]}")
        code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
        r = c.post("/auth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI, "code_verifier": VERIFIER,
        })  # fmt: skip
        if r.status_code != 200:
            fail(f"token returned {r.status_code}: {r.text}")
        token = r.json()["access_token"]

        step("post a note through Micropub")
        r = c.post("/micropub", data={"h": "entry", "content": "Smoke test"},
                   headers={"Authorization": f"Bearer {token}"})  # fmt: skip
        if r.status_code != 202:
            fail(f"micropub returned {r.status_code}: {r.text}")
        location = r.headers["location"]
        if not location.startswith("https://awfulwoman.com/notes/"):
            fail(f"unexpected Location: {location}")

        step("check the file was written")
        files = list(site.rglob("*.md"))
        if len(files) != 1 or "Smoke test" not in files[0].read_text():
            fail(f"expected one note file, found {files}")

        step("update the note, then delete it")
        auth = {"Authorization": f"Bearer {token}"}
        r = c.post(
            "/micropub",
            json={"action": "update", "url": location, "replace": {"content": ["Edited"]}},
            headers=auth,
        )
        if r.status_code != 200 or "Edited" not in files[0].read_text():
            fail(f"update failed: {r.status_code} {r.text}")
        r = c.post("/micropub", data={"action": "delete", "url": location}, headers=auth)
        if r.status_code != 200 or list(site.rglob("*.md")):
            fail(f"delete failed: {r.status_code} {r.text}")
        r = c.post("/micropub", data={"action": "undelete", "url": location}, headers=auth)
        if r.status_code != 200 or "Edited" not in next(site.rglob("*.md")).read_text():
            fail(f"undelete failed: {r.status_code} {r.text}")

        step("check /new-password style routes do not exist and queries work")
        if c.get("/auth/new-password").status_code != 404:
            fail("/auth/new-password should not exist")
        cfg = c.get(
            "/micropub", params={"q": "config"}, headers={"Authorization": f"Bearer {token}"}
        )
        if cfg.status_code != 200 or "note" not in json.dumps(cfg.json()):
            fail(f"q=config failed: {cfg.status_code} {cfg.text[:200]}")
    print("smoke test passed")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
