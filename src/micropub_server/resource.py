"""Bearer-token authentication for resource endpoints (/micropub, /media)."""

from __future__ import annotations

from starlette.requests import Request

from .auth.service import TokenInfo
from .http import bearer_token, state_of
from .publish import MicropubError


def authenticate(request: Request, form_token: str | None = None) -> TokenInfo:
    header = bearer_token(request)
    if header and form_token:
        raise MicropubError(
            "invalid_request", "Send the access token in the header or the body, not both"
        )
    token = header or form_token
    if not token:
        raise MicropubError("unauthorized", "An access token is required", 401)
    info = state_of(request).auth.verify(token)
    if info is None:
        raise MicropubError("unauthorized", "The access token is invalid, expired or revoked", 401)
    return info
