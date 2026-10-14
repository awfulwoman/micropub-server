import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from micropub_server.auth.service import AuthError, AuthService
from micropub_server.db import Database

ME = "https://awfulwoman.com/"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock):
    return AuthService(Database(tmp_path / "t.db"), me=ME, token_ttl_days=90, clock=clock)


def challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def make_code(svc, scope="create update", verifier="v" * 43, **kw):
    params = {
        "client_id": "https://app.example/",
        "redirect_uri": "https://app.example/cb",
        "state": "s",
        **kw,
    }
    if verifier:
        params["code_challenge"] = challenge(verifier)
        params["code_challenge_method"] = "S256"
    return svc.issue_code(params, scope)


REDEEM = {
    "client_id": "https://app.example/",
    "redirect_uri": "https://app.example/cb",
    "code_verifier": "v" * 43,
}


# --- requests ----------------------------------------------------------------
def test_request_roundtrip_and_expiry(svc, clock):
    uri = svc.new_request({"client_id": "x"}, {"name": "App"})
    assert uri.startswith("urn:ietf:params:oauth:request_uri:")
    params, client = svc.get_request(uri)
    assert params == {"client_id": "x"} and client == {"name": "App"}
    clock.advance(minutes=11)
    assert svc.get_request(uri) is None


def test_request_unknown(svc):
    assert svc.get_request("urn:ietf:params:oauth:request_uri:nope") is None
    assert svc.get_request("garbage") is None


# --- codes -------------------------------------------------------------------
def test_redeem_code_happy_path(svc):
    code = make_code(svc)
    data = svc.redeem_code(code, **REDEEM)
    assert data.me == ME and data.scope == "create update"


def test_code_is_single_use(svc):
    code = make_code(svc)
    svc.redeem_code(code, **REDEEM)
    with pytest.raises(AuthError) as e:
        svc.redeem_code(code, **REDEEM)
    assert e.value.error == "invalid_grant"


def test_code_replay_revokes_issued_tokens(svc):
    code = make_code(svc)
    token = svc.issue_token(svc.redeem_code(code, **REDEEM))["access_token"]
    assert svc.verify(token) is not None
    with pytest.raises(AuthError):
        svc.redeem_code(code, **REDEEM)
    assert svc.verify(token) is None


def test_code_expires(svc, clock):
    code = make_code(svc)
    clock.advance(minutes=11)
    with pytest.raises(AuthError) as e:
        svc.redeem_code(code, **REDEEM)
    assert e.value.error == "invalid_grant"


@pytest.mark.parametrize(
    "override",
    [
        {"client_id": "https://evil.example/"},
        {"redirect_uri": "https://app.example/other"},
        {"code_verifier": "w" * 43},
    ],
)
def test_code_mismatches_rejected(svc, override):
    code = make_code(svc)
    with pytest.raises(AuthError) as e:
        svc.redeem_code(code, **{**REDEEM, **override})
    assert e.value.error == "invalid_grant"


def test_missing_verifier_when_challenge_set(svc):
    code = make_code(svc)
    with pytest.raises(AuthError) as e:
        svc.redeem_code(
            code,
            client_id=REDEEM["client_id"],
            redirect_uri=REDEEM["redirect_uri"],
            code_verifier=None,
        )
    assert e.value.error == "invalid_request"


def test_pkce_optional(svc):
    code = make_code(svc, verifier=None)
    svc.redeem_code(
        code, client_id=REDEEM["client_id"], redirect_uri=REDEEM["redirect_uri"], code_verifier=None
    )


def test_client_id_compared_canonically(svc):
    code = make_code(svc)
    svc.redeem_code(code, **{**REDEEM, "client_id": "HTTPS://App.example"})


def test_unknown_code(svc):
    with pytest.raises(AuthError):
        svc.redeem_code("nope", **REDEEM)


# --- tokens ------------------------------------------------------------------
def test_issue_token_shape(svc):
    out = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))
    assert out["token_type"] == "Bearer"
    assert out["access_token"].startswith("mps_")
    assert out["scope"] == "create update" and out["me"] == ME
    assert out["expires_in"] == 90 * 86400


def test_authentication_only_code_cannot_get_token(svc):
    code = make_code(svc, scope=None)
    data = svc.redeem_code(code, **REDEEM)
    with pytest.raises(AuthError) as e:
        svc.issue_token(data)
    assert e.value.error == "invalid_grant"


def test_tokens_stored_hashed(svc):
    token = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    stored = [r["token_hash"] for r in svc.db.query("SELECT token_hash FROM tokens")]
    assert token not in stored and len(stored) == 1


def test_verify_token_and_last_used(svc):
    token = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    info = svc.verify(token)
    assert info.scope == "create update" and info.client_id == "https://app.example/"
    assert svc.db.query("SELECT last_used_at FROM tokens")[0][0] is not None


def test_verify_rejects_unknown_expired_revoked(svc, clock):
    assert svc.verify("mps_nope") is None
    token = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    svc.revoke(token)
    assert svc.verify(token) is None
    token2 = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    clock.advance(days=91)
    assert svc.verify(token2) is None


def test_revoke_unknown_is_silent(svc):
    svc.revoke("mps_unknown")


def test_introspect(svc, clock):
    token = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    out = svc.introspect(token)
    assert out["active"] is True and out["me"] == ME and out["scope"] == "create update"
    assert out["client_id"] == "https://app.example/" and out["exp"] > out["iat"]
    assert svc.introspect("mps_nope") == {"active": False}


def test_token_for_other_me_is_rejected(svc, tmp_path, clock):
    token = svc.issue_token(svc.redeem_code(make_code(svc), **REDEEM))["access_token"]
    other = AuthService(svc.db, me="https://other.example/", token_ttl_days=90, clock=clock)
    assert other.verify(token) is None


def test_cleanup_removes_expired(svc, clock):
    svc.new_request({"a": 1}, None)
    make_code(svc)
    clock.advance(hours=2)
    svc.cleanup()
    assert svc.db.query("SELECT * FROM auth_requests") == []
    assert svc.db.query("SELECT * FROM auth_codes") == []
