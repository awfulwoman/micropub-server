"""Health check and background housekeeping."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .http import state_of

log = logging.getLogger("micropub_server")
HOUSEKEEPING_INTERVAL = 3600


async def health(request: Request) -> Response:
    s = state_of(request)
    try:
        s.db.query("SELECT 1")
        db = "ok"
    except Exception:
        db = "error"
    body = {
        "status": "ok" if db == "ok" else "error",
        "store": "ok" if not s.store_problem else "degraded",
        "db": db,
        "version": s.settings.app_version,
    }
    if s.site_config_status != "disabled":
        body["site_config"] = s.site_config_status
    return JSONResponse(body, status_code=200 if db == "ok" else 503)


async def _housekeeping(state) -> None:
    while True:
        await asyncio.sleep(HOUSEKEEPING_INTERVAL)
        try:
            state.auth.cleanup()
            state.db.checkpoint()
            await state.refresh_site_config()
        except Exception:
            log.exception("housekeeping failed")


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    state = app.state.s
    state.store_problem = await state.store.check()
    await state.refresh_site_config()
    if state.store_problem:
        log.error("store check failed: %s", state.store_problem)
    state.auth.cleanup()
    task = asyncio.create_task(_housekeeping(state))
    try:
        yield
    finally:
        task.cancel()


def routes() -> list[Route]:
    return [Route("/health", health)]
