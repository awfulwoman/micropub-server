import asyncio
import re

import pytest
from ruamel.yaml import YAML

from micropub_server.card_image import CardImage
from micropub_server.publish import update_post
from micropub_server.syndication.mastodon import MastodonTarget
from micropub_server.target_meta import TargetMeta

from .conftest import PASSWORD, jpeg_bytes

NOTE = "content/notes/2026/10/14/1910.md"
API = "https://indieweb.social"


@pytest.fixture
def ui(client):
    assert client.post("/login", data={"password": PASSWORD}).status_code == 303
    return client


def csrf(client, path="/posts"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1]), text.split("---\n", 2)[2]


def make(ui, type_="note", **data):
    r = ui.post(
        f"/posts/new/{type_}",
        data={"csrf": csrf(ui, f"/posts/new/{type_}"), "status": "publish", **data},
    )
    assert r.status_code == 303, r.text
    return r.headers["location"].split("?")[0]  # /posts/<uid>


def edit(ui, post, data=None, button="save"):
    page = ui.get(f"{post}/edit")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    return ui.post(f"{post}/edit", data={"csrf": token, "action": button, **(data or {})})


# --- the edit form -----------------------------------------------------------------------
def test_edit_form_is_filled_in(ui):
    post = make(ui, content="Original text", category="a, b")
    html = ui.get(f"{post}/edit").text
    assert "Original text" in html and 'value="a, b"' in html and "Edit" in html


def test_edit_form_needs_a_session(client):
    assert client.get("/posts/whatever/edit").status_code == 303


def test_edit_unknown_post_is_404(ui):
    assert ui.get("/posts/nope/edit").status_code == 404


def test_edit_changes_the_file(ui, site):
    post = make(ui, content="Original text")
    r = edit(ui, post, {"content": "Edited text"})
    assert r.status_code == 303
    front, body = fm(site, NOTE)
    assert body.strip() == "Edited text" and front["lastmod"] == "2026-10-14T19:10:47+02:00"


def test_saving_without_changes_says_so_and_commits_nothing(ui, state, monkeypatch):
    post = make(ui, content="Same", category="a")
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append(message)
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    r = edit(ui, post, {"content": "Same", "category": "a"})
    assert r.status_code == 303 and "Nothing+changed" in r.headers["location"]
    assert commits == []


def test_changing_tags(ui, site):
    post = make(ui, content="x", category="a, b")
    edit(ui, post, {"content": "x", "category": "b, c"})
    assert fm(site, NOTE)[0]["tags"] == ["b", "c"]


def test_emptying_a_field_removes_it(ui, site):
    post = make(ui, "article", name="Title", content="Body", summary="A summary")
    path = "content/posts/2026/title/index.md"
    assert fm(site, path)[0]["description"] == "A summary"
    edit(ui, post, {"name": "Title", "content": "Body", "summary": ""})
    assert "description" not in fm(site, path)[0]


def test_required_fields_are_enforced_when_editing_too(ui, site):
    post = make(ui, content="Keep me")
    r = edit(ui, post, {"content": ""})
    assert r.status_code == 422 and "Text is required" in r.text
    assert "Keep me" in (site / NOTE).read_text()


def test_editing_a_bookmark_updates_its_domain(ui, site):
    post = make(ui, "bookmark", **{"bookmark-of": "https://www.one.example/a"})
    edit(ui, post, {"bookmark-of": "https://two.example/b"})
    front, _ = fm(site, "content/bookmarks/2026/10/14/1910/index.md")
    assert front["bookmark_of"] == "https://two.example/b" and front["basedomain"] == "two.example"


def test_editing_a_checkin(ui, site):
    post = make(ui, "checkin", checkin_name="Old Place", checkin_lat="1", checkin_lng="2")
    html = ui.get(f"{post}/edit").text
    assert 'value="Old Place"' in html
    edit(ui, post, {"checkin_name": "New Place", "checkin_lat": "3", "checkin_lng": "4"})
    front, _ = fm(site, "content/checkins/2026/10/14/1910/index.md")
    assert front["place_name"] == "New Place" and front["place_geo"] == {"lat": 3.0, "lng": 4.0}


def test_location_is_prefilled_and_editable(ui, site):
    post = make(ui, content="here", location_lat="52.5", location_lng="13.4")
    assert 'value="52.5"' in ui.get(f"{post}/edit").text
    edit(ui, post, {"content": "here", "location_lat": "10", "location_lng": "20"})
    assert fm(site, NOTE)[0]["place_geo"] == {"lat": 10.0, "lng": 20.0}


def test_changing_the_url_name_moves_the_post(ui, site):
    post = make(ui, "article", name="Hello there", content="Body")
    r = edit(ui, post, {"name": "Hello there", "content": "Body", "mp-slug": "greetings"})
    assert r.status_code == 303 and "moved" in r.headers["location"].lower()
    assert (site / "content/posts/2026/greetings/index.md").exists()
    assert not (site / "content/posts/2026/hello-there").exists()


def test_the_publication_time_prefill_and_change(ui, site):
    post = make(ui, content="x")
    assert 'value="2026-10-14T19:10"' in ui.get(f"{post}/edit").text
    edit(ui, post, {"content": "x", "published": "2026-10-14T20:30"})  # changes the path (HHmm)
    assert (site / "content/notes/2026/10/14/2030.md").exists() and not (site / NOTE).exists()


def test_publish_a_draft_and_revert_it(ui, site):
    post = make(ui, content="x", status="draft")
    assert fm(site, NOTE)[0]["draft"] is True
    edit(ui, post, {"content": "x"}, button="publish")
    assert "draft" not in fm(site, NOTE)[0]
    edit(ui, post, {"content": "x"}, button="draft")
    assert fm(site, NOTE)[0]["draft"] is True


def test_buttons_match_the_state(ui):
    published = make(ui, content="x")
    assert "Revert to draft" in ui.get(f"{published}/edit").text
    draft = make(ui, content="y", status="draft", published="2026-10-13T10:00")
    html = ui.get(f"{draft}/edit").text
    assert "Publish" in html and "Revert to draft" not in html


def test_edit_needs_csrf(ui, site):
    post = make(ui, content="x")
    r = ui.post(f"{post}/edit", data={"csrf": "wrong", "content": "evil"})
    assert r.status_code == 403 and "evil" not in (site / NOTE).read_text()


def test_deleted_posts_cannot_be_edited(ui):
    post = make(ui, content="x")
    ui.post(f"{post}/delete", data={"csrf": csrf(ui, f"{post}/delete")})
    assert ui.get(f"{post}/edit").status_code in (303, 404, 409)


def test_photos_are_shown_but_not_changed_by_editing(ui, site):
    r = ui.post(
        "/posts/new/photo",
        data={
            "csrf": csrf(ui, "/posts/new/photo"),
            "status": "publish",
            "content": "Boats",
            "photo_alt": ["A boat", "", "", ""],
        },
        files=[("photo", ("boat.jpg", jpeg_bytes(), "image/jpeg"))],
    )
    post = r.headers["location"].split("?")[0]
    html = ui.get(f"{post}/edit").text
    assert "boat.jpg" in html
    edit(ui, post, {"content": "More boats"})
    path = "content/photos/2026/10/14/1910/"
    assert (site / path / "boat.jpg").exists()
    assert fm(site, path + "index.md")[0]["images"] == [{"file": "boat.jpg", "alt": "A boat"}]


# --- delete and undelete ----------------------------------------------------------------------
def test_delete_asks_first(ui, site):
    post = make(ui, content="Precious")
    page = ui.get(f"{post}/delete")
    assert page.status_code == 200 and "Delete" in page.text and "Precious" in page.text
    assert (site / NOTE).exists()


def test_delete_removes_the_file_and_lists_it_as_deleted(ui, site):
    post = make(ui, content="Precious")
    r = ui.post(f"{post}/delete", data={"csrf": csrf(ui, f"{post}/delete")})
    assert r.status_code == 303 and not (site / NOTE).exists()
    assert "Precious" in ui.get("/posts?status=deleted").text
    assert "Precious" not in ui.get("/posts").text


def test_delete_needs_csrf(ui, site):
    post = make(ui, content="x")
    assert ui.post(f"{post}/delete", data={"csrf": "wrong"}).status_code == 403
    assert (site / NOTE).exists()


def test_restore(ui, site):
    post = make(ui, content="Precious", category="a")
    before = (site / NOTE).read_text()
    ui.post(f"{post}/delete", data={"csrf": csrf(ui, f"{post}/delete")})
    page = ui.get(f"{post}/undelete")
    assert page.status_code == 200 and "Restore" in page.text
    r = ui.post(f"{post}/undelete", data={"csrf": csrf(ui, f"{post}/undelete")})
    assert r.status_code == 303 and (site / NOTE).read_text() == before


def test_the_post_page_offers_the_right_actions(ui):
    post = make(ui, content="x")
    html = ui.get(post).text
    assert (
        f'href="{post}/edit"' in html and f'href="{post}/delete"' in html and "undelete" not in html
    )
    ui.post(f"{post}/delete", data={"csrf": csrf(ui, f"{post}/delete")})
    html = ui.get(post).text
    assert f'href="{post}/undelete"' in html and f'href="{post}/edit"' not in html


# --- share now --------------------------------------------------------------------------------------
def test_share_now(ui, state, site, no_network):
    state.targets = [MastodonTarget(url=API, user="awfulwoman", token="t", checked=True)]
    r = ui.post(
        "/posts/new/note",
        data={
            "csrf": csrf(ui, "/posts/new/note"),
            "status": "publish",
            "content": "Hello",
            "syndicate": f"{API}/@awfulwoman",
        },
    )
    post = r.headers["location"].split("?")[0]
    assert "Share now" in ui.get(post).text
    no_network.get("https://awfulwoman.com/notes/2026/10/14/1910/").respond(200, text="ok")
    no_network.post(f"{API}/api/v1/statuses").respond(200, json={"url": f"{API}/@awfulwoman/5"})
    r = ui.post(f"{post}/syndicate", data={"csrf": csrf(ui, post)})
    assert r.status_code == 303
    assert fm(site, NOTE)[0]["syndication"] == [f"{API}/@awfulwoman/5"]
    assert f"{API}/@awfulwoman/5" in ui.get(post).text
    assert "Share now" not in ui.get(post).text


def _with_copies(ui, state, *copies):
    post = make(ui, content="x")
    url = state.db.query("SELECT url FROM posts WHERE uid = ?", (post.split("/")[-1],))[0]["url"]
    asyncio.run(update_post(state, url, add={"syndication": list(copies)}))
    return post


def test_removing_a_copy(ui, state, site):
    post = _with_copies(ui, state, f"{API}/@awfulwoman/5", "https://bsky.app/x")
    html = ui.get(post).text
    confirm = f"{post}/syndication/remove?url=https%3A//indieweb.social/%40awfulwoman/5"
    assert f'href="{confirm}"' in html and 'method="post"' not in html.split("Also published at")[1]
    page = ui.get(confirm)
    assert page.status_code == 200 and "Remove this link?" in page.text
    assert f'name="url" value="{API}/@awfulwoman/5"' in page.text
    assert fm(site, NOTE)[0]["syndication"] == [f"{API}/@awfulwoman/5", "https://bsky.app/x"]
    r = ui.post(
        f"{post}/syndication/remove", data={"csrf": csrf(ui, post), "url": f"{API}/@awfulwoman/5"}
    )
    assert r.status_code == 303 and "Removed" in r.headers["location"]
    assert fm(site, NOTE)[0]["syndication"] == ["https://bsky.app/x"]
    assert f"{API}/@awfulwoman/5" not in ui.get(post).text


def test_removing_the_last_copy_drops_the_property(ui, state, site):
    post = _with_copies(ui, state, f"{API}/@awfulwoman/5")
    ui.post(
        f"{post}/syndication/remove", data={"csrf": csrf(ui, post), "url": f"{API}/@awfulwoman/5"}
    )
    assert "syndication" not in fm(site, NOTE)[0]
    assert "Also published at" not in ui.get(post).text


def test_confirming_an_unknown_copy_goes_back(ui, state):
    post = _with_copies(ui, state, f"{API}/@awfulwoman/5")
    r = ui.get(f"{post}/syndication/remove?url=https://elsewhere.example/1")
    assert r.status_code == 303 and r.headers["location"].startswith(post)


def test_removing_a_copy_needs_csrf(ui, state, site):
    post = _with_copies(ui, state, f"{API}/@awfulwoman/5")
    r = ui.post(f"{post}/syndication/remove", data={"csrf": "wrong", "url": f"{API}/@awfulwoman/5"})
    assert r.status_code == 403 and fm(site, NOTE)[0]["syndication"] == [f"{API}/@awfulwoman/5"]


def test_share_now_needs_csrf(ui, state):
    state.targets = [MastodonTarget(url=API, user="awfulwoman", token="t")]
    post = make(ui, content="x")
    assert ui.post(f"{post}/syndicate", data={"csrf": "wrong"}).status_code == 403


# --- the link card -----------------------------------------------------------------------
IMG = "https://adactio.com/i/card.jpg"
LINK = "https://adactio.com/journal/22802"
BOOKMARK = "content/bookmarks/2026/10/14/1910/index.md"
LIKE = "content/likes/2026/10/14/1910/index.md"
CARD_META = TargetMeta(
    site="Adactio", description="A journal entry.", image_url=IMG, image_alt="A photo"
)
WEBP = CardImage(b"RIFF-webp-bytes", 800, 419)
WEBP_2 = CardImage(b"RIFF-other-bytes", 640, 360)


@pytest.fixture
def carded(ui, targets, images):
    targets.metas[LINK] = CARD_META
    images.cards[IMG] = WEBP


@pytest.mark.parametrize(
    ("kind", "path"), [("bookmark", BOOKMARK), ("like", LIKE)], ids=["bookmark", "like"]
)
def test_the_edit_form_shows_the_card(ui, carded, kind, path):
    post = make(ui, kind, **{f"{kind}-of": LINK})
    html = ui.get(f"{post}/edit").text
    assert "Adactio" in html and "A journal entry." in html
    assert 'src="https://awfulwoman.com/' in html and "card.webp" in html
    assert "800" in html and "419" in html
    assert 'name="remove_card_image"' in html
    assert 'name="card" value="refetch"' in html


def test_a_bookmark_without_a_card_still_offers_to_fetch_one(ui, targets):
    post = make(ui, "bookmark", **{"bookmark-of": LINK})
    html = ui.get(f"{post}/edit").text
    assert 'name="card" value="refetch"' in html and "remove_card_image" not in html


def test_a_like_with_an_embed_shows_no_card_section(ui, targets):
    toot = "https://masto.example/@ada/1"
    targets.metas[toot] = TargetMeta(author="Ada", oembed="<iframe></iframe>")
    post = make(ui, "like", **{"like-of": toot})
    html = ui.get(f"{post}/edit").text
    assert 'name="card"' not in html and "remove_card_image" not in html


@pytest.mark.parametrize(
    ("kind", "path"), [("bookmark", BOOKMARK), ("like", LIKE)], ids=["bookmark", "like"]
)
def test_ticking_remove_image_deletes_the_image_in_the_same_commit(
    ui, carded, site, state, monkeypatch, kind, path
):
    post = make(ui, kind, **{f"{kind}-of": LINK})
    card = path.replace("index.md", "card.webp")
    assert (site / card).exists()
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append([c.path for c in changes])
        await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    r = edit(ui, post, {f"{kind}-of": LINK, "remove_card_image": "1"})
    assert r.status_code == 303
    front, _ = fm(site, path)
    assert f"{kind}_image" not in front and front[f"{kind}_site"] == "Adactio"
    assert not (site / card).exists()
    assert commits == [[path, card]]


def test_fetching_the_card_again_replaces_it(ui, carded, site, targets, images):
    post = make(ui, "bookmark", **{"bookmark-of": LINK})
    targets.metas[LINK] = TargetMeta(
        site="New site", description="New text", image_url=IMG, image_alt="New"
    )
    images.cards[IMG] = WEBP_2
    page = ui.get(f"{post}/edit")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = ui.post(f"{post}/edit", data={"csrf": token, "bookmark-of": LINK, "card": "refetch"})
    assert r.status_code == 303 and "Saved" in r.headers["location"]
    front, _ = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "New site" and front["bookmark_image"]["width"] == 640
    assert (site / BOOKMARK.replace("index.md", "card.webp")).read_bytes() == WEBP_2.content
    assert targets.calls.count(LINK) == 2


def test_fetching_again_with_no_image_removes_the_old_one(ui, carded, site, targets, images):
    post = make(ui, "bookmark", **{"bookmark-of": LINK})
    targets.metas[LINK] = TargetMeta(site="Plain")
    page = ui.get(f"{post}/edit")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    ui.post(f"{post}/edit", data={"csrf": token, "bookmark-of": LINK, "card": "refetch"})
    front, _ = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Plain" and "bookmark_description" not in front
    assert "bookmark_image" not in front
    assert not (site / BOOKMARK.replace("index.md", "card.webp")).exists()


def test_fetching_again_keeps_the_card_when_the_page_does_not_answer(ui, carded, site, targets):
    post = make(ui, "bookmark", **{"bookmark-of": LINK})
    del targets.metas[LINK]
    page = ui.get(f"{post}/edit")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = ui.post(f"{post}/edit", data={"csrf": token, "bookmark-of": LINK, "card": "refetch"})
    assert r.status_code == 303
    front, _ = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Adactio" and front["bookmark_image"]


def test_saving_without_touching_the_card_fetches_nothing(ui, carded, targets):
    post = make(ui, "bookmark", **{"bookmark-of": LINK, "name": "T"})
    edit(ui, post, {"bookmark-of": LINK, "name": "Changed"})
    assert targets.calls == [LINK]
