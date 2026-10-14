import re

import pytest

from micropub_server.syndication.mastodon import MastodonTarget

from .conftest import PASSWORD, jpeg_bytes, sign_in


@pytest.fixture
def ui(client):
    assert client.post("/login", data={"password": PASSWORD}).status_code == 303
    return client


def csrf(client, path="/posts"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


# --- share -----------------------------------------------------------------------------
def test_share_opens_the_bookmark_form_filled_in(ui):
    r = ui.get(
        "/share",
        params={
            "url": "https://example.com/article",
            "title": "A great read",
            "text": "Worth a look",
        },
    )
    assert r.status_code == 200
    assert 'value="https://example.com/article"' in r.text and 'value="A great read"' in r.text
    assert "&gt; Worth a look" in r.text  # the selected text is quoted


def test_share_when_signed_out_goes_through_login_and_keeps_the_values(client):
    r = client.get("/share", params={"url": "https://example.com/a", "title": "T"})
    assert r.status_code == 303 and r.headers["location"].startswith(
        "/login?next=%2Fshare%3Furl%3D"
    )
    r = client.post(r.headers["location"], data={"password": PASSWORD})
    assert r.status_code == 303 and r.headers["location"].startswith("/share?url=https")


def test_phones_often_put_the_address_in_the_text_field(ui):
    r = ui.get(
        "/share", params={"text": "Look at this https://example.com/page please", "title": "Page"}
    )
    assert 'value="https://example.com/page"' in r.text


def test_share_creates_a_bookmark(ui, site):
    page = ui.get("/share", params={"url": "https://example.com/a", "title": "A page"})
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = ui.post(
        "/posts/new/bookmark",
        data={
            "csrf": token,
            "status": "publish",
            "bookmark-of": "https://example.com/a",
            "name": "A page",
        },
    )
    assert r.status_code == 303 and (site / "content/bookmarks/2026/10/14/1910.md").exists()


def test_the_share_target_is_in_the_manifest(client):
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["share_target"] == {
        "action": "/share",
        "method": "GET",
        "params": {"url": "url", "title": "title", "text": "text"},
    }


def test_new_post_page_offers_the_bookmarklet(ui):
    html = ui.get("/posts/new").text
    assert "javascript:location.href=" in html and "indie.awfulwoman.com/share" in html


# --- files ------------------------------------------------------------------------------------
def upload(ui, color=(1, 2, 3), name="canal.jpg"):
    page = ui.get("/files/upload")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return ui.post(
        "/files/upload",
        data={"csrf": token},
        files=[("file", (name, jpeg_bytes(color=color), "image/jpeg"))],
    )


def test_files_page_starts_empty(ui):
    assert "No files yet" in ui.get("/files").text


def test_upload_a_file_and_see_its_address(ui, site):
    r = upload(ui)
    assert r.status_code == 303
    html = ui.get(r.headers["location"]).text
    assert re.search(r"https://awfulwoman\.com/media/2026/10/[0-9a-f]{32}\.jpg", html)
    assert len(list((site / "static/media").rglob("*.jpg"))) == 1


def test_upload_rejects_unsupported_files(ui):
    page = ui.get("/files/upload")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = ui.post(
        "/files/upload",
        data={"csrf": token},
        files=[("file", ("x.pdf", b"%PDF-1.4", "application/pdf"))],
    )
    assert r.status_code == 422 and "not supported" in r.text


def test_upload_needs_csrf(ui, site):
    r = ui.post(
        "/files/upload",
        data={"csrf": "wrong"},
        files=[("file", ("a.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    assert r.status_code == 403 and not (site / "static").exists()


def test_files_page_lists_uploads_with_thumbnails(ui):
    upload(ui)
    html = ui.get("/files").text
    assert "<img" in html and "/media/2026/10/" in html


def test_deleting_a_file(ui, site, state):
    upload(ui)
    uid = state.db.query("SELECT uid FROM media")[0][0]
    assert "Delete" in ui.get(f"/files/{uid}/delete").text
    r = ui.post(f"/files/{uid}/delete", data={"csrf": csrf(ui, f"/files/{uid}/delete")})
    assert r.status_code == 303 and state.db.query("SELECT * FROM media") == []
    assert not any((site / "static").rglob("*.jpg")) if (site / "static").exists() else True


def test_a_file_used_by_a_post_names_it_before_deleting(ui, state):
    r = ui.post(
        "/posts/new/photo",
        data={
            "csrf": csrf(ui, "/posts/new/photo"),
            "status": "publish",
            "name": "Canal walk",
            "content": "x",
        },
        files=[("photo", ("canal.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    uid = state.db.query("SELECT uid FROM media")[0][0]
    page = ui.get(f"/files/{uid}/delete").text
    assert "Canal walk" in page and "used by" in page.lower()
    assert r.status_code == 303


def test_files_need_a_session(client):
    assert (
        client.get("/files").status_code == 303 and client.get("/files/upload").status_code == 303
    )


# --- apps (tokens) ------------------------------------------------------------------------------
def test_apps_page_lists_tokens_and_revokes_them(ui, client):
    sign_in(client, scope="create update")
    html = ui.get("/tokens").text
    assert "https://app.example/" in html and "create update" in html and "Revoke" in html
    tid = re.search(r'action="/tokens/([0-9a-f]+)/revoke"', html).group(1)
    r = ui.post(f"/tokens/{tid}/revoke", data={"csrf": csrf(ui, "/tokens")})
    assert r.status_code == 303
    assert "revoked" in ui.get("/tokens").text.lower()


def test_a_revoked_token_stops_working(ui, client):
    token = sign_in(client, scope="create")["access_token"]
    assert (
        client.get(
            "/micropub", params={"q": "config"}, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )
    tid = re.search(r'action="/tokens/([0-9a-f]+)/revoke"', ui.get("/tokens").text).group(1)
    ui.post(f"/tokens/{tid}/revoke", data={"csrf": csrf(ui, "/tokens")})
    assert (
        client.get(
            "/micropub", params={"q": "config"}, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )


def test_revoke_needs_csrf(ui, client):
    sign_in(client)
    tid = re.search(r'action="/tokens/([0-9a-f]+)/revoke"', ui.get("/tokens").text).group(1)
    assert ui.post(f"/tokens/{tid}/revoke", data={"csrf": "wrong"}).status_code == 403


def test_token_secrets_never_appear_on_the_page(ui, client):
    token = sign_in(client)["access_token"]
    assert token not in ui.get("/tokens").text


# --- status -------------------------------------------------------------------------------------------
def test_status_page(ui, state):
    state.targets = [MastodonTarget(url="https://indieweb.social", user="awfulwoman", token="t")]
    html = ui.get("/status").text
    assert "test" in html  # the version
    assert "File system" in html and "@awfulwoman on indieweb.social" in html


def test_status_reports_a_store_problem(ui, state, monkeypatch):
    async def bad():
        return "GitHub token cannot write to o/r: it needs Contents: Read and write."

    monkeypatch.setattr(state.store, "check", bad)
    html = ui.get("/status").text
    assert "cannot write" in html


def test_status_counts_posts(ui):
    ui.post(
        "/posts/new/note",
        data={"csrf": csrf(ui, "/posts/new/note"), "status": "draft", "content": "x"},
    )
    assert "Drafts" in ui.get("/status").text


# --- preview helper for the bookmark form -----------------------------------------------------------------
def test_preview_fetches_a_title(ui, no_network):
    no_network.get("https://example.com/page").respond(
        200,
        headers={"content-type": "text/html"},
        text='<html><head><title>Fallback</title><meta property="og:title" content="The Real Title">'
        '<meta property="og:site_name" content="Example"><meta name="description" content="About it"></head></html>',
    )
    r = ui.get("/api/preview", params={"url": "https://example.com/page"})
    assert r.status_code == 200
    assert r.json() == {"name": "The Real Title", "site": "Example", "summary": "About it"}


def test_preview_refuses_private_addresses(ui):
    assert ui.get("/api/preview", params={"url": "http://127.0.0.1/"}).json() == {}
    assert ui.get("/api/preview", params={"url": "http://192.168.1.1/admin"}).json() == {}


def test_preview_needs_a_session(client):
    assert client.get("/api/preview", params={"url": "https://example.com/"}).status_code == 401


def test_preview_is_cached(ui, no_network):
    route = no_network.get("https://example.com/c").respond(
        200, headers={"content-type": "text/html"}, text="<title>C</title>"
    )
    ui.get("/api/preview", params={"url": "https://example.com/c"})
    ui.get("/api/preview", params={"url": "https://example.com/c"})
    assert route.call_count == 1


def test_app_js_is_served_without_inline_script(ui):
    assert ui.get("/static/app.js").status_code == 200
    assert "<script>" not in ui.get("/posts/new/note").text.replace(
        '<script src="/static/app.js" defer></script>', ""
    )
