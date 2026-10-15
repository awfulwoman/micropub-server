"""GitHub's workflow_run webhook: learn when the site has been rebuilt (spec 08)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import deploys
from .app import AppState
from .http import state_of
from .syndication.service import run_syndication

log = logging.getLogger("micropub_server.webhook")
MAX_SHARES = 25


def _signature_ok(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def share_waiting(s: AppState) -> None:
    """Share every waiting post. run_syndication does one post a call, so repeat until it repeats."""
    seen: set[str] = set()
    try:
        for _ in range(MAX_SHARES):
            result = await run_syndication(s)
            if result.url is None or result.url in seen:
                return
            seen.add(result.url)
    except Exception:
        log.exception("sharing after a build failed")


async def github(request: Request) -> Response:
    s = state_of(request)
    secret = s.settings.webhook_secret
    if not secret:
        return Response("Not found", status_code=404)
    body = await request.body()
    if not _signature_ok(secret, body, request.headers.get("x-hub-signature-256", "")):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return JSONResponse({"ok": True})
    if event != "workflow_run":
        return JSONResponse({"ignored": event}, status_code=202)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    run = deploys.wanted(s, payload)
    if run is None:
        return JSONResponse({"ignored": "not the deploy workflow"}, status_code=202)
    deployed = deploys.apply_run(s, run)
    task = BackgroundTask(share_waiting, s) if deployed else None
    return JSONResponse({"conclusion": run.get("conclusion")}, status_code=202, background=task)


def routes() -> list[Route]:
    return [Route("/webhooks/github", github, methods=["POST"])]
