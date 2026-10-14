import json

import pytest
from ruamel.yaml import YAML

from .conftest import jpeg_bytes, sign_in

NOTE = "content/notes/2026/10/14/1910.md"


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1]), text.split("---\n", 2)[2]


@pytest.fixture
def full(client):
    """A token that can do everything."""
    return {
        "Authorization": f"Bearer {sign_in(client, scope='create update delete media')['access_token']}"
    }


def make(client, headers, **data):
    r = client.post("/micropub", data=data, headers=headers)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def update(client, headers, url, **ops):
    return client.post("/micropub", json={"action": "update", "url": url, **ops}, headers=headers)


# --- update ---------------------------------------------------------------------
def test_replace_content_rewrites_the_file(client, full, site):
    url = make(client, full, content="Old text")
    r = update(client, full, url, replace={"content": ["New text"]})
    assert r.status_code == 200
    assert r.json() == {"success": "update", "success_description": f"Post updated at {url}"}
    front, body = fm(site, NOTE)
    assert body.strip() == "New text"
    assert front["lastmod"] == "2026-10-14T19:10:47+02:00"


def test_add_and_delete_categories(client, full, site):
    url = make(client, full, content="x", **{"category[]": ["a", "b"]})
    update(client, full, url, add={"category": ["c"]})
    assert fm(site, NOTE)[0]["tags"] == ["a", "b", "c"]
    update(client, full, url, delete={"category": ["a"]})
    assert fm(site, NOTE)[0]["tags"] == ["b", "c"]


def test_delete_only_update_is_accepted(client, full, site):
    # Indiekit rejects any update that lacks replace/add/"remove"; the spec's operation is "delete"
    url = make(client, full, content="x", **{"category[]": ["a"]})
    r = update(client, full, url, delete=["category"])
    assert r.status_code == 200
    assert "tags" not in fm(site, NOTE)[0]


def test_add_with_two_keys_applies_both(client, full, site):
    url = make(client, full, content="x")
    update(client, full, url, add={"category": ["a"], "syndication": ["https://m.example/1"]})
    front, _ = fm(site, NOTE)
    assert front["tags"] == ["a"] and front["syndication"] == ["https://m.example/1"]


def test_update_without_operations_is_400(client, full):
    url = make(client, full, content="x")
    r = client.post("/micropub", json={"action": "update", "url": url}, headers=full)
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_malformed_operation_is_400(client, full):
    url = make(client, full, content="x")
    assert update(client, full, url, replace={"content": "not a list"}).status_code == 400


def test_form_encoded_update_is_rejected(client, full):
    url = make(client, full, content="x")
    r = client.post(
        "/micropub", data={"action": "update", "url": url, "replace": "x"}, headers=full
    )
    assert r.status_code == 400


def test_no_change_means_no_commit(client, full, state, monkeypatch):
    url = make(client, full, content="Same", **{"category[]": ["a"]})
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    r = update(client, full, url, replace={"content": ["Same"]})
    assert r.status_code == 200 and "no properties changed" in r.json()["success_description"]
    assert commits == []


def test_update_commit_message(client, full, state, monkeypatch):
    url = make(client, full, content="x")
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    update(client, full, url, replace={"content": ["y"]})
    assert seen == ["update note post"]


def test_unknown_url_is_400(client, full):
    r = update(client, full, "https://awfulwoman.com/notes/nope/", replace={"content": ["x"]})
    assert r.status_code == 400 and "No post found" in r.json()["error_description"]


def test_url_without_trailing_slash_is_found(client, full, site):
    url = make(client, full, content="x")
    assert update(client, full, url.rstrip("/"), replace={"content": ["y"]}).status_code == 200


def test_needs_update_scope(client, bearer):
    # the default test token has create, update, media; make one without update
    t = sign_in(client, scope="create")["access_token"]
    h = {"Authorization": f"Bearer {t}"}
    url = make(client, h, content="x")
    r = update(client, h, url, replace={"content": ["y"]})
    assert r.status_code == 403 and r.json()["scope"] == "update"


def test_draft_scope_cannot_touch_published_posts(client, full):
    url = make(client, full, content="x")
    t = sign_in(client, scope="draft")["access_token"]
    r = update(client, {"Authorization": f"Bearer {t}"}, url, replace={"content": ["y"]})
    assert r.status_code == 403 and r.json()["error"] == "insufficient_scope"


def test_draft_scope_can_edit_drafts_and_they_stay_drafts(client, site):
    t = sign_in(client, scope="draft")["access_token"]
    h = {"Authorization": f"Bearer {t}"}
    url = make(client, h, content="draft text")
    assert (
        update(
            client, h, url, replace={"content": ["edited"], "post-status": ["published"]}
        ).status_code
        == 200
    )
    front, body = fm(site, NOTE)
    assert body.strip() == "edited" and front["draft"] is True


def test_publishing_a_draft(client, full, site):
    url = make(client, full, content="x", **{"post-status": "draft"})
    assert fm(site, NOTE)[0]["draft"] is True
    update(client, full, url, replace={"post-status": ["published"]})
    assert "draft" not in fm(site, NOTE)[0]


def test_editing_a_title_does_not_change_the_url(client, full, site):
    url = make(client, full, name="Hello there", content="Body")
    assert url.endswith("/posts/hello-there/")
    r = update(client, full, url, replace={"name": ["A completely different title"]})
    assert r.status_code == 200
    assert (
        fm(site, "content/posts/2026/hello-there/index.md")[0]["title"]
        == "A completely different title"
    )
    assert not (site / "content/posts/2026/a-completely-different-title").exists()


def test_mp_slug_moves_the_post(client, full, site, state):
    url = make(client, full, name="Hello there", content="Body")
    r = update(client, full, url, replace={"mp-slug": ["greetings"]})
    assert r.status_code == 201
    assert r.headers["location"] == "https://awfulwoman.com/posts/greetings/"
    assert not (site / "content/posts/2026/hello-there").exists()
    assert (site / "content/posts/2026/greetings/index.md").exists()
    [row] = state.db.query("SELECT url, path FROM posts")
    assert row["url"] == "https://awfulwoman.com/posts/greetings/" and row["path"].endswith(
        "greetings/index.md"
    )


def test_moving_a_bundle_moves_its_photos(client, full, site, state):
    r = client.post(
        "/micropub",
        data={"name": "Boats", "content": "x"},
        files={"photo": ("boat.jpg", jpeg_bytes(), "image/jpeg")},
        headers=full,
    )
    url = r.headers["location"]
    bundle = site / "content/photos/2026/10/14/1910"
    assert (bundle / "boat.jpg").exists()
    # photo posts use a HHmm path, so move it by changing the publication time
    r = update(client, full, url, replace={"published": ["2026-10-14T20:20:00+02:00"]})
    assert r.status_code == 201
    new = site / "content/photos/2026/10/14/2020"
    assert (new / "index.md").exists() and (new / "boat.jpg").exists()
    assert not bundle.exists()
    [m] = state.db.query("SELECT path, url FROM media")
    assert m["path"] == "content/photos/2026/10/14/2020/boat.jpg" and m["url"].endswith(
        "/2020/boat.jpg"
    )


def test_type_change_moves_the_post(client, full, site):
    url = make(client, full, content="A note")
    r = update(client, full, url, add={"photo": ["https://cdn.example/a.jpg"]})
    assert r.status_code == 201
    assert r.headers["location"] == "https://awfulwoman.com/photos/2026/10/14/1910/"
    assert not (site / NOTE).exists()
    assert fm(site, "content/photos/2026/10/14/1910/index.md")[0]["images"] == [{"file": "a.jpg"}]


def test_channel_survives_an_update(client, full, site, state):
    make(client, full, name="Wife Biscuits", **{"mp-channel": "bandnames"})
    url = state.db.query("SELECT url FROM posts")[0][0]
    update(client, full, url, add={"category": ["funny"]})
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert props["channel"] == ["bandnames"] and props["post-type"] == "bandname"


def test_uid_cannot_be_changed(client, full, state):
    url = make(client, full, content="x")
    before = state.db.query("SELECT uid FROM posts")[0][0]
    update(client, full, url, replace={"uid": ["hacked"], "content": ["y"]})
    assert state.db.query("SELECT uid FROM posts")[0][0] == before
    assert json.loads(state.db.query("SELECT properties FROM posts")[0][0])["uid"] == before


def test_store_failure_leaves_the_database_alone(client, full, state, monkeypatch):
    from micropub_server.stores import StoreError

    url = make(client, full, content="Original")

    async def boom(changes, message):
        raise StoreError("GitHub could not update the branch: HTTP 403", 403)

    monkeypatch.setattr(state.store, "commit", boom)
    r = update(client, full, url, replace={"content": ["Changed"]})
    assert r.status_code == 502 and r.json()["error"] == "store_error"
    assert (
        json.loads(state.db.query("SELECT properties FROM posts")[0][0])["content"]["text"]
        == "Original"
    )


def test_update_recorded_in_database(client, full, state):
    url = make(client, full, content="x")
    update(client, full, url, replace={"content": ["y"]})
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert props["updated"] == "2026-10-14T19:10:47+02:00" and props["content"]["text"] == "y"
    assert state.db.query("SELECT updated FROM posts")[0][0] == props["updated"]


def test_q_source_reflects_updates(client, full):
    url = make(client, full, content="x")
    update(client, full, url, replace={"content": ["updated text"]})
    body = client.get("/micropub", params={"q": "source", "url": url}, headers=full).json()
    assert body["properties"]["content"][0]["value"] == "updated text"


# --- delete -----------------------------------------------------------------------
def delete(client, headers, url):
    return client.post("/micropub", data={"action": "delete", "url": url}, headers=headers)


def test_delete_removes_the_file(client, full, site, state):
    url = make(client, full, content="x")
    r = delete(client, full, url)
    assert r.status_code == 200
    assert r.json() == {"success": "delete", "success_description": f"Post deleted from {url}"}
    assert not (site / NOTE).exists()
    row = state.db.query("SELECT * FROM posts")[0]
    assert row["deleted"] is not None and row["deleted_properties"] is not None
    assert set(json.loads(row["properties"])) <= {
        "post-type",
        "published",
        "slug",
        "type",
        "uid",
        "url",
        "deleted",
    }


def test_deleted_posts_disappear_from_queries(client, full):
    url = make(client, full, content="x")
    delete(client, full, url)
    assert client.get("/micropub", params={"q": "source"}, headers=full).json()["items"] == []
    assert (
        client.get("/micropub", params={"q": "source", "url": url}, headers=full).status_code == 400
    )


def test_delete_needs_delete_scope(client, bearer):
    url = make(client, bearer, content="x")
    r = delete(client, bearer, url)
    assert r.status_code == 403 and r.json()["scope"] == "delete"


def test_delete_unknown_is_400(client, full):
    assert delete(client, full, "https://awfulwoman.com/notes/nope/").status_code == 400


def test_deleting_twice_is_harmless(client, full):
    url = make(client, full, content="x")
    assert delete(client, full, url).status_code == 200
    assert delete(client, full, url).status_code == 200


def test_delete_commit_message(client, full, state, monkeypatch):
    url = make(client, full, content="x")
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    delete(client, full, url)
    assert seen == ["delete note post"]


def test_cannot_update_a_deleted_post(client, full):
    url = make(client, full, content="x")
    delete(client, full, url)
    assert update(client, full, url, replace={"content": ["y"]}).status_code == 400


def test_deleting_a_bundle_keeps_its_photos(client, full, site):
    r = client.post(
        "/micropub",
        data={"content": "x"},
        files={"photo": ("boat.jpg", jpeg_bytes(), "image/jpeg")},
        headers=full,
    )
    delete(client, full, r.headers["location"])
    bundle = site / "content/photos/2026/10/14/1910"
    assert not (bundle / "index.md").exists() and (bundle / "boat.jpg").exists()


# --- undelete ----------------------------------------------------------------------
def undelete(client, headers, url):
    return client.post("/micropub", data={"action": "undelete", "url": url}, headers=headers)


def test_undelete_restores_the_file_exactly(client, full, site):
    url = make(client, full, content="Precious", **{"category[]": ["a"]})
    before = (site / NOTE).read_text()
    delete(client, full, url)
    r = undelete(client, full, url)
    assert r.status_code == 200 and r.headers["location"] == url
    assert r.json()["success"] == "delete_undelete"
    assert (site / NOTE).read_text() == before


def test_undelete_brings_the_post_back_in_queries(client, full):
    url = make(client, full, content="x")
    delete(client, full, url)
    undelete(client, full, url)
    assert len(client.get("/micropub", params={"q": "source"}, headers=full).json()["items"]) == 1


def test_undelete_needs_create_scope(client, full):
    url = make(client, full, content="x")
    delete(client, full, url)
    t = sign_in(client, scope="delete")["access_token"]
    r = undelete(client, {"Authorization": f"Bearer {t}"}, url)
    assert r.status_code == 403 and r.json()["scope"] == "create"


def test_undelete_of_a_live_post_is_400(client, full):
    url = make(client, full, content="x")
    assert undelete(client, full, url).status_code == 400


def test_undelete_with_draft_scope_restores_as_draft(client, full, site):
    url = make(client, full, content="x")
    delete(client, full, url)
    t = sign_in(client, scope="draft")["access_token"]
    assert undelete(client, {"Authorization": f"Bearer {t}"}, url).status_code == 200
    assert fm(site, NOTE)[0]["draft"] is True


def test_undelete_commit_message(client, full, state, monkeypatch):
    url = make(client, full, content="x")
    delete(client, full, url)
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    undelete(client, full, url)
    assert seen == ["undelete note post"]


def test_changing_a_bookmarked_url_updates_basedomain(client, full, site):
    url = make(client, full, **{"bookmark-of": "https://www.one.example/a"})
    path = "content/bookmarks/2026/10/14/1910.md"
    assert fm(site, path)[0]["basedomain"] == "one.example"
    update(client, full, url, replace={"bookmark-of": ["https://two.example/b"]})
    assert fm(site, path)[0]["basedomain"] == "two.example"
