"""Security headers and request size limits for every response (spec 11)."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = "default-src 'self'; img-src 'self' https: data:; frame-ancestors 'none'; base-uri 'self'"

# Endpoints that browser-based Micropub clients call from other origins. They are authorised by a
# bearer token in a header, never by a cookie, so letting any origin read them gives nothing away.
# The cookie-based web UI is deliberately not on this list.
CORS_PATHS = (
    "/micropub",
    "/media",
    "/syndicate",
    "/auth/token",
    "/auth/introspect",
    "/auth/revoke",
    "/auth/userinfo",
    "/.well-known/oauth-authorization-server",
)

# Pages and endpoints that legitimately carry files.
UPLOAD_PREFIXES = ("/micropub", "/media", "/files/upload", "/posts/")


class _TooLarge(Exception):
    pass


class SecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    def _limit(self, scope: Scope) -> int:
        config = scope["app"].state.s.config.application
        path = scope["path"]
        return config.max_body_bytes if path.startswith(UPLOAD_PREFIXES) else config.max_form_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope["app"].state.s
        hsts = state.base.startswith("https://")
        started = False
        cors = scope["path"] in CORS_PATHS and any(k == b"origin" for k, _ in scope["headers"])

        if cors and scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (b"access-control-allow-origin", b"*"),
                        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                        (b"access-control-allow-headers", b"Authorization, Content-Type"),
                        (b"access-control-max-age", b"86400"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                ]
                kind = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if b"html" in kind:
                    # No form-action here: the consent page must be able to redirect to the app.
                    extra.append((b"content-security-policy", CSP.encode()))
                if hsts:
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                if cors:
                    extra += [
                        (b"access-control-allow-origin", b"*"),
                        (b"access-control-expose-headers", b"Location"),
                    ]
                headers.extend((k, v) for k, v in extra if k not in present)
            await send(message)

        async def reject() -> None:
            body = json.dumps(
                {"error": "invalid_request", "error_description": "The request body is too large"}
            ).encode()
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": body})

        limit = self._limit(scope)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await reject()
            return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        try:
            await self.app(scope, counted_receive, send_with_headers)
        except _TooLarge:
            if not started:
                await reject()
