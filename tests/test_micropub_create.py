import json

from .conftest import sign_in

NOTE_PATH = "content/notes/2026/10/14/1910.md"


def read(site, path):
    return (site / path).read_text()


def test_create_note_form_encoded(client, bearer, site):
    r = client.post(
        "/micropub",
        data={"h": "entry", "content": "Hello *world*", "category[]": ["indieweb", "test"]},
        headers=bearer,
    )
    assert r.status_code == 202
    assert r.headers["location"] == "https://awfulwoman.com/notes/2026/10/14/1910/"
    assert r.json() == {
        "success": "create_pending",
        "success_description": "Post will be created at https://awfulwoman.com/notes/2026/10/14/1910/",
    }
    assert read(site, NOTE_PATH) == (
        '---\ndate: "2026-10-14T19:10:47+02:00"\ntags:\n  - indieweb\n  - test\n---\n\nHello *world*\n'
    )


def test_create_note_json_mf2(client, bearer, site):
    body = {"type": ["h-entry"], "properties": {"content": ["From JSON"], "category": ["a"]}}
    r = client.post("/micropub", json=body, headers=bearer)
    assert r.status_code == 202
    assert "From JSON" in read(site, NOTE_PATH)


def test_create_json_with_html_content(client, bearer, site):
    body = {"type": ["h-entry"], "properties": {"content": [{"html": "<p>Hi <b>there</b></p>"}]}}
    assert client.post("/micropub", json=body, headers=bearer).status_code == 202
    assert "Hi **there**" in read(site, NOTE_PATH)


def test_access_token_in_form_body(client, token, site):
    r = client.post("/micropub", data={"h": "entry", "content": "x", "access_token": token})
    assert r.status_code == 202
    assert "access_token" not in read(site, NOTE_PATH)


def test_token_in_both_header_and_body_is_rejected(client, token):
    r = client.post(
        "/micropub",
        data={"content": "x", "access_token": token},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_missing_token_is_401(client):
    r = client.post("/micropub", data={"content": "x"})
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_invalid_token_is_401(client):
    r = client.post(
        "/micropub", data={"content": "x"}, headers={"Authorization": "Bearer mps_nope"}
    )
    assert r.status_code == 401


def test_insufficient_scope_is_403(client):
    t = sign_in(client, scope="media")["access_token"]
    r = client.post("/micropub", data={"content": "x"}, headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 403
    assert r.json() == {
        "error": "insufficient_scope",
        "error_description": "The create scope is required",
        "scope": "create",
    }


def test_draft_scope_forces_draft(client, site):
    t = sign_in(client, scope="draft")["access_token"]
    r = client.post(
        "/micropub",
        data={"content": "secret", "post-status": "published"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 202
    assert "draft: true" in read(site, NOTE_PATH)


def test_client_post_status_draft(client, bearer, site):
    client.post("/micropub", data={"content": "x", "post-status": "draft"}, headers=bearer)
    assert "draft: true" in read(site, NOTE_PATH)


def test_same_minute_collision_gets_suffix(client, bearer, site):
    r1 = client.post("/micropub", data={"content": "one"}, headers=bearer)
    r2 = client.post("/micropub", data={"content": "two"}, headers=bearer)
    assert r1.status_code == r2.status_code == 202
    assert r2.headers["location"] == "https://awfulwoman.com/notes/2026/10/14/1910-2/"
    assert "one" in read(site, NOTE_PATH)
    assert "two" in read(site, "content/notes/2026/10/14/1910-2.md")


def test_article_with_slug_path_and_collision(client, bearer, site):
    data = {"name": "Hello there", "content": "Body text"}
    r1 = client.post("/micropub", data=data, headers=bearer)
    assert r1.headers["location"] == "https://awfulwoman.com/posts/hello-there/"
    assert "title: Hello there" in read(site, "content/posts/2026/hello-there/index.md")
    r2 = client.post("/micropub", data=data, headers=bearer)
    assert r2.headers["location"] == "https://awfulwoman.com/posts/hello-there-2/"
    assert (site / "content/posts/2026/hello-there-2/index.md").exists()


def test_mp_slug_is_honoured(client, bearer):
    r = client.post(
        "/micropub",
        data={"name": "Whatever", "content": "x", "mp-slug": "custom-slug"},
        headers=bearer,
    )
    assert r.headers["location"].endswith("/posts/custom-slug/")


def test_bookmark_uses_site_key_names(client, bearer, site):
    r = client.post(
        "/micropub",
        data={"bookmark-of": "https://www.hackaday.io/p/1", "name": "Clock", "content": "Fun"},
        headers=bearer,
    )
    assert r.status_code == 202
    text = read(site, "content/bookmarks/2026/10/14/1910.md")
    assert "bookmark_of: https://www.hackaday.io/p/1" in text and "basedomain: hackaday.io" in text
    assert "bookmarkOf" not in text and "uid:" not in text and "publishDate" not in text


def test_published_property_is_respected(client, bearer, site):
    client.post(
        "/micropub",
        data={"content": "old", "published": "2025-01-02T03:04:05+01:00"},
        headers=bearer,
    )
    assert "old" in read(site, "content/notes/2025/01/02/0304.md")


def test_unconfigured_post_type_is_501(client, bearer):
    r = client.post("/micropub", data={"rsvp": "yes", "in-reply-to-x": "1"}, headers=bearer)
    assert r.status_code == 501 and r.json()["error"] == "not_implemented"
    assert "rsvp" in r.json()["error_description"]


def test_mp_commands_never_reach_the_file(client, bearer, site):
    client.post(
        "/micropub",
        data={"content": "x", "mp-slug": "s", "mp-syndicate-to": "https://m.example/"},
        headers=bearer,
    )
    assert "mp-" not in read(site, NOTE_PATH) and "mp_" not in read(site, NOTE_PATH)


def test_post_is_recorded_in_database(client, bearer, state):
    client.post("/micropub", data={"content": "x"}, headers=bearer)
    rows = state.db.query("SELECT * FROM posts")
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == "https://awfulwoman.com/notes/2026/10/14/1910/"
    assert (
        row["path"] == NOTE_PATH
        and row["post_type"] == "note"
        and row["post_status"] == "published"
    )
    props = json.loads(row["properties"])
    assert props["uid"] == row["uid"] and props["content"]["text"] == "x"


def test_client_supplied_uid_is_ignored(client, bearer, state):
    client.post("/micropub", data={"content": "x", "uid": "attacker-chosen"}, headers=bearer)
    assert state.db.query("SELECT uid FROM posts")[0][0] != "attacker-chosen"


def test_store_failure_is_502_and_leaves_no_record(client, bearer, state, site, monkeypatch):
    from micropub_server.stores import StoreError

    async def boom(changes, message):
        raise StoreError("GitHub could not update the branch: HTTP 403: nope", 403)

    monkeypatch.setattr(state.store, "commit", boom)
    r = client.post("/micropub", data={"content": "x"}, headers=bearer)
    assert r.status_code == 502
    assert r.json()["error"] == "store_error" and "HTTP 403" in r.json()["error_description"]
    assert state.db.query("SELECT * FROM posts") == []


def test_commit_message(client, bearer, state, monkeypatch):
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    client.post("/micropub", data={"content": "x"}, headers=bearer)
    assert seen == ["create note post"]


def test_unknown_action_is_400(client, bearer):
    r = client.post("/micropub", data={"action": "explode", "url": "https://x/"}, headers=bearer)
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_update_requires_url(client, bearer):
    r = client.post(
        "/micropub", json={"action": "update", "replace": {"content": ["x"]}}, headers=bearer
    )
    assert r.status_code == 400
