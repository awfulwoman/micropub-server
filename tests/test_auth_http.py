from urllib.parse import parse_qs, urlsplit

import respx

from .conftest import CLIENT, ME, PASSWORD, REDIRECT, VERIFIER, pkce_challenge, sign_in

AUTH_PARAMS = {
    "response_type": "code",
    "client_id": CLIENT,
    "redirect_uri": REDIRECT,
    "state": "xyz",
    "scope": "create update",
    "code_challenge": pkce_challenge(),
    "code_challenge_method": "S256",
}


def test_metadata_document(client):
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    m = r.json()
    assert m["issuer"] == "https://indie.awfulwoman.com/"
    assert m["authorization_endpoint"] == "https://indie.awfulwoman.com/auth"
    assert m["token_endpoint"] == "https://indie.awfulwoman.com/auth/token"
    assert m["introspection_endpoint"].endswith("/auth/introspect")
    assert m["revocation_endpoint"].endswith("/auth/revoke")
    assert m["code_challenge_methods_supported"] == ["S256"]
    assert m["authorization_response_iss_parameter_supported"] is True
    assert "create" in m["scopes_supported"]


def test_docs_page_without_params(client):
    r = client.get("/auth")
    assert r.status_code == 200 and "authorization_endpoint" in r.text


def test_full_flow_issues_working_token(client):
    t = sign_in(client)
    assert t["token_type"] == "Bearer" and t["me"] == ME
    assert t["scope"] == "create update media"
    assert t["access_token"].startswith("mps_")


def test_redirect_carries_state_and_iss(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    consent = r.headers["location"]
    r = client.post(
        urlsplit(consent).path + "?" + urlsplit(consent).query,
        data={"password": PASSWORD, "scope": ["create"]},
    )
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["xyz"] and q["iss"] == ["https://indie.awfulwoman.com/"] and "code" in q


def test_consent_page_shows_form(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert 'type="password"' in page.text and "create" in page.text


def test_wrong_password_rerenders_422(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    consent = r.headers["location"]
    page = client.post(
        urlsplit(consent).path + "?" + urlsplit(consent).query,
        data={"password": "nope", "scope": ["create"]},
    )
    assert page.status_code == 422


def test_login_rate_limit(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    codes = [
        client.post(url, data={"password": "bad", "scope": ["create"]}).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [422] * 5 and codes[5] == 429
    # even the right password is refused while limited
    assert client.post(url, data={"password": PASSWORD, "scope": ["create"]}).status_code == 429


def test_user_can_grant_fewer_scopes(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    r = client.post(url, data={"password": PASSWORD, "scope": ["create"]})
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    t = client.post(
        "/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
    ).json()
    assert t["scope"] == "create"


def test_deny_redirects_with_access_denied(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    r = client.post(url, data={"deny": "1"})
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.status_code == 302 and q["error"] == ["access_denied"] and q["state"] == ["xyz"]


def test_code_cannot_be_used_twice(client):
    r = client.get("/auth", params=AUTH_PARAMS)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    r = client.post(url, data={"password": PASSWORD, "scope": ["create"]})
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "code_verifier": VERIFIER,
    }
    first = client.post("/auth/token", data=body)
    second = client.post("/auth/token", data=body)
    assert first.status_code == 200
    assert second.status_code == 400 and second.json()["error"] == "invalid_grant"
    # replay revoked the first token
    intro = client.post(
        "/auth/introspect",
        data={"token": first.json()["access_token"]},
        headers={"Authorization": "Bearer x"},
    )
    assert intro.status_code == 401


def test_profile_only_redemption_at_auth_endpoint(client):
    params = {**AUTH_PARAMS}
    del params["scope"]
    r = client.get("/auth", params=params)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    r = client.post(url, data={"password": PASSWORD})
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    r = client.post(
        "/auth",
        data={
            "code": code,
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"me": ME}


def test_authentication_only_code_rejected_at_token_endpoint(client):
    params = {k: v for k, v in AUTH_PARAMS.items() if k != "scope"}
    r = client.get("/auth", params=params)
    url = urlsplit(r.headers["location"]).path + "?" + urlsplit(r.headers["location"]).query
    r = client.post(url, data={"password": PASSWORD})
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    r = client.post(
        "/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_token_endpoint_rejects_get(client):
    assert client.get("/auth/token").status_code == 405


# --- validation of /auth parameters -----------------------------------------------
def test_missing_required_parameter(client):
    p = {k: v for k, v in AUTH_PARAMS.items() if k != "state"}
    r = client.get("/auth", params=p)
    assert r.status_code == 400 and "state" in r.text


def test_bad_response_type(client):
    assert client.get("/auth", params={**AUTH_PARAMS, "response_type": "token"}).status_code == 400


def test_pkce_method_must_be_s256(client):
    assert (
        client.get("/auth", params={**AUTH_PARAMS, "code_challenge_method": "plain"}).status_code
        == 400
    )


def test_challenge_without_method(client):
    p = {k: v for k, v in AUTH_PARAMS.items() if k != "code_challenge_method"}
    assert client.get("/auth", params=p).status_code == 400


def test_client_id_with_dot_segments(client):
    assert (
        client.get(
            "/auth", params={**AUTH_PARAMS, "client_id": "https://app.example/../x"}
        ).status_code
        == 400
    )


@respx.mock
def test_foreign_redirect_uri_rejected_unless_declared(client):
    respx.get("https://app.example/").respond(
        200, text="<html></html>", headers={"content-type": "text/html"}
    )
    r = client.get("/auth", params={**AUTH_PARAMS, "redirect_uri": "https://evil.example/cb"})
    assert r.status_code == 400 and "redirect_uri" in r.text


def test_unknown_request_uri(client):
    assert (
        client.get(
            "/auth/consent", params={"request_uri": "urn:ietf:params:oauth:request_uri:nope"}
        ).status_code
        == 400
    )
    assert client.get("/auth/consent").status_code == 400


# --- introspection, revocation, userinfo ------------------------------------------------
def test_introspection_requires_authorization(client, token):
    assert client.post("/auth/introspect", data={"token": token}).status_code == 401


def test_introspection_active_and_inactive(client, token):
    h = {"Authorization": f"Bearer {token}"}
    ok = client.post("/auth/introspect", data={"token": token}, headers=h).json()
    assert ok["active"] is True and ok["scope"] == "create update media"
    assert client.post("/auth/introspect", data={"token": "mps_nope"}, headers=h).json() == {
        "active": False
    }


def test_revocation(client, token):
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/revoke", data={"token": token}).status_code == 200
    assert client.post("/auth/introspect", data={"token": token}, headers=h).status_code == 401
    assert client.post("/auth/revoke", data={"token": "mps_unknown"}).status_code == 200


def test_userinfo_needs_profile_scope(client, no_network):
    no_network.get(ME).respond(200, headers={"content-type": "text/html"}, text="<html></html>")
    t = sign_in(client, scope="create")["access_token"]
    r = client.get("/auth/userinfo", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 403 and r.json()["error"] == "insufficient_scope"
    t2 = sign_in(client, scope="create profile")["access_token"]
    assert (
        client.get("/auth/userinfo", headers={"Authorization": f"Bearer {t2}"}).status_code == 200
    )
