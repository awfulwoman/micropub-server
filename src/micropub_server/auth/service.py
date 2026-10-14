"""IndieAuth state: pending requests, single-use codes, opaque tokens (spec 02)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import Database
from ..util import canonical_url

REQUEST_PREFIX = "urn:ietf:params:oauth:request_uri:"
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=10)
TOKEN_PREFIX = "mps_"


class AuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(f"{error}: {description}")
        self.error, self.description, self.status = error, description, status


@dataclass(frozen=True)
class CodeData:
    code_hash: str
    client_id: str
    redirect_uri: str
    me: str
    scope: str | None


@dataclass(frozen=True)
class TokenInfo:
    token_hash: str
    client_id: str
    me: str
    scope: str
    issued_at: str
    expires_at: str

    @property
    def scopes(self) -> set[str]:
        return set(self.scope.split())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


class AuthService:
    def __init__(
        self,
        db: Database,
        *,
        me: str,
        token_ttl_days: int = 90,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.db = db
        self.me = canonical_url(me)
        self.token_ttl = timedelta(days=token_ttl_days)
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _iso(self, dt: datetime) -> str:
        return dt.astimezone(UTC).isoformat(timespec="seconds")

    # --- pending authorization requests -------------------------------------
    def new_request(self, params: dict[str, Any], client: dict[str, Any] | None) -> str:
        request_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO auth_requests (id, params, client, expires_at) VALUES (?, ?, ?, ?)",
            (
                request_id,
                json.dumps(params),
                json.dumps(client),
                self._iso(self._now() + REQUEST_TTL),
            ),
        )
        return REQUEST_PREFIX + request_id

    def get_request(self, request_uri: str) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        if not request_uri.startswith(REQUEST_PREFIX):
            return None
        rows = self.db.query(
            "SELECT params, client, expires_at FROM auth_requests WHERE id = ?",
            (request_uri.removeprefix(REQUEST_PREFIX),),
        )
        if not rows or rows[0]["expires_at"] <= self._iso(self._now()):
            return None
        return json.loads(rows[0]["params"]), json.loads(rows[0]["client"])

    def delete_request(self, request_uri: str) -> None:
        self.db.execute(
            "DELETE FROM auth_requests WHERE id = ?", (request_uri.removeprefix(REQUEST_PREFIX),)
        )

    # --- codes ----------------------------------------------------------------
    def issue_code(self, params: dict[str, Any], scope: str | None) -> str:
        code = secrets.token_urlsafe(32)
        self.db.execute(
            "INSERT INTO auth_codes (code_hash, client_id, redirect_uri, me, scope, code_challenge, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _sha256(code),
                params["client_id"],
                params["redirect_uri"],
                self.me,
                scope or None,
                params.get("code_challenge"),
                self._iso(self._now() + CODE_TTL),
            ),
        )
        return code

    def redeem_code(
        self, code: str, *, client_id: str, redirect_uri: str, code_verifier: str | None
    ) -> CodeData:
        code_hash = _sha256(code)
        replayed = False
        with self.db.transaction() as tx:
            rows = tx.query("SELECT * FROM auth_codes WHERE code_hash = ?", (code_hash,))
            if not rows:
                raise AuthError("invalid_grant", "unknown or expired code")
            row = rows[0]
            if row["used_at"]:
                replayed = True
                tx.execute(
                    "UPDATE tokens SET revoked_at = ? WHERE code_hash = ? AND revoked_at IS NULL",
                    (self._iso(self._now()), code_hash),
                )
            else:
                if row["expires_at"] <= self._iso(self._now()):
                    raise AuthError("invalid_grant", "unknown or expired code")
                if canonical_url(client_id) != canonical_url(row["client_id"]):
                    raise AuthError("invalid_grant", "client_id does not match")
                if redirect_uri != row["redirect_uri"]:
                    raise AuthError("invalid_grant", "redirect_uri does not match")
                if row["code_challenge"]:
                    if not code_verifier:
                        raise AuthError("invalid_request", "code_verifier is required")
                    digest = hashlib.sha256(code_verifier.encode()).digest()
                    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                    if not hmac.compare_digest(expected, row["code_challenge"]):
                        raise AuthError("invalid_grant", "code_verifier does not match")
                tx.execute(
                    "UPDATE auth_codes SET used_at = ? WHERE code_hash = ?",
                    (self._iso(self._now()), code_hash),
                )
        if replayed:
            raise AuthError("invalid_grant", "code has already been used")
        return CodeData(code_hash, row["client_id"], row["redirect_uri"], row["me"], row["scope"])

    # --- tokens ---------------------------------------------------------------
    def issue_token(self, data: CodeData) -> dict[str, Any]:
        if not data.scope:
            raise AuthError("invalid_grant", "this code was issued for authentication only")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = self._now()
        self.db.execute(
            "INSERT INTO tokens (token_hash, code_hash, client_id, me, scope, issued_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _sha256(token),
                data.code_hash,
                data.client_id,
                data.me,
                data.scope,
                self._iso(now),
                self._iso(now + self.token_ttl),
            ),
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "scope": data.scope,
            "me": data.me,
            "expires_in": int(self.token_ttl.total_seconds()),
        }

    def verify(self, token: str) -> TokenInfo | None:
        token_hash = _sha256(token)
        rows = self.db.query("SELECT * FROM tokens WHERE token_hash = ?", (token_hash,))
        if not rows:
            return None
        row = rows[0]
        now = self._iso(self._now())
        if row["revoked_at"] or row["expires_at"] <= now or canonical_url(row["me"]) != self.me:
            return None
        self.db.execute(
            "UPDATE tokens SET last_used_at = ? WHERE token_hash = ?", (now, token_hash)
        )
        return TokenInfo(
            token_hash,
            row["client_id"],
            row["me"],
            row["scope"],
            row["issued_at"],
            row["expires_at"],
        )

    def introspect(self, token: str) -> dict[str, Any]:
        info = self.verify(token)
        if info is None:
            return {"active": False}
        return {
            "active": True,
            "me": info.me,
            "client_id": info.client_id,
            "scope": info.scope,
            "iat": _epoch(info.issued_at),
            "exp": _epoch(info.expires_at),
        }

    def revoke(self, token: str) -> None:
        self.db.execute(
            "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (self._iso(self._now()), _sha256(token)),
        )

    def list_tokens(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM tokens ORDER BY issued_at DESC")]

    # --- login rate limiting --------------------------------------------------
    LOGIN_LIMIT = 5
    LOGIN_WINDOW = timedelta(minutes=1)

    def login_allowed(self, ip: str) -> bool:
        since = self._iso(self._now() - self.LOGIN_WINDOW)
        count = self.db.query(
            "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND at > ?", (ip, since)
        )[0][0]
        return count < self.LOGIN_LIMIT

    def record_login_failure(self, ip: str) -> None:
        self.db.execute(
            "INSERT INTO login_attempts (ip, at) VALUES (?, ?)", (ip, self._iso(self._now()))
        )

    # --- housekeeping ------------------------------------------------------------
    def cleanup(self) -> None:
        now = self._iso(self._now())
        self.db.execute("DELETE FROM auth_requests WHERE expires_at <= ?", (now,))
        self.db.execute("DELETE FROM auth_codes WHERE expires_at <= ?", (now,))
        self.db.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
        self.db.execute(
            "DELETE FROM login_attempts WHERE at <= ?",
            (self._iso(self._now() - timedelta(hours=1)),),
        )
        self.db.execute(
            "DELETE FROM tokens WHERE expires_at <= ?",
            (self._iso(self._now() - timedelta(days=30)),),
        )
