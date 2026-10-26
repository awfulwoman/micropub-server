import json
import re

import pytest
from ruamel.yaml import YAML

from micropub_server.syndication.mastodon import MastodonTarget

from .conftest import PASSWORD, jpeg_bytes


@pytest.fixture
def ui(client):
    assert client.post("/login", data={"password": PASSWORD}).status_code == 303
    return client


def csrf(client, path="/posts/new"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1]), text.split("---\n", 2)[2]


def create(client, type_, data=None, files=None, token=None):
    data = {
        "csrf": token or csrf(client, f"/posts/new/{type_}"),
        "status": "publish",
        **(data or {}),
    }
    return client.post(f"/posts/new/{type_}", data=data, files=files)


# --- choosing a type --------------------------------------------------------------------
def test_type_chooser_lists_configured_types(ui):
    r = ui.get("/posts/new")
    assert r.status_code == 200
    for name in ("Note", "Post", "Bookmark", "Photo", "Checkin", "Quote", "Page"):
        assert name in r.text
    assert 'href="/posts/new/note"' in r.text


def test_unknown_type_is_404(ui):
    assert ui.get("/posts/new/nonsense").status_code == 404


def test_forms_need_a_session(client):
    assert client.get("/posts/new/note").status_code == 303


# --- the forms ------------------------------------------------------------------------------
def test_note_form_has_the_right_fields(ui):
    html = ui.get("/posts/new/note").text
    assert 'name="content"' in html and 'name="category"' in html and 'name="csrf"' in html
    assert 'name="name"' not in html  # a note has no title
    assert 'value="publish"' in html and 'value="draft"' in html
    assert 'enctype="multipart/form-data"' in html


def test_bookmark_form_has_a_link_field(ui):
    html = ui.get("/posts/new/bookmark").text
    assert 'name="bookmark-of"' in html and 'type="url"' in html


def test_photo_form_has_file_slots_with_alt_text(ui):
    html = ui.get("/posts/new/photo").text
    assert (
        html.count('type="file"') >= 4
        and html.count('name="photo_alt"') >= 4
        and 'accept="image/*"' in html
    )


def test_syndication_targets_appear_when_configured(ui, state):
    assert "Share to" not in ui.get("/posts/new/note").text
    state.targets = [
        MastodonTarget(url="https://indieweb.social", user="awfulwoman", token="t", checked=True)
    ]
    html = ui.get("/posts/new/note").text
    assert "Share to" in html and "@awfulwoman on indieweb.social" in html and "checked" in html


# --- creating ---------------------------------------------------------------------------------
def test_create_a_note(ui, site):
    r = create(
        ui, "note", {"content": "Hello from the browser", "category": "indieweb, ui , indieweb"}
    )
    assert r.status_code == 303
    front, body = fm(site, "content/notes/2026/10/14/1910.md")
    assert body.strip() == "Hello from the browser" and front["tags"] == ["indieweb", "ui"]
    assert "draft" not in front
    uid = re.search(r"/posts/([^/?]+)", r.headers["location"]).group(1)
    assert ui.get(r.headers["location"]).status_code == 200 and uid


def test_the_result_page_says_what_happened(ui):
    r = create(ui, "note", {"content": "Hi"})
    page = ui.get(r.headers["location"]).text
    assert "Committed" in page and "https://awfulwoman.com/notes/2026/10/14/1910/" in page


def test_save_as_draft(ui, site):
    r = create(ui, "note", {"content": "Not yet", "status": "draft"})
    assert r.status_code == 303
    assert fm(site, "content/notes/2026/10/14/1910.md")[0]["draft"] is True


def test_csrf_is_required(ui, site):
    r = ui.post("/posts/new/note", data={"content": "evil", "status": "publish", "csrf": "wrong"})
    assert r.status_code == 403
    assert not any(site.rglob("*.md"))


def test_a_post_without_a_session_is_refused(client, site):
    r = client.post("/posts/new/note", data={"content": "x", "csrf": "x"})
    assert r.status_code == 303 and not any(site.rglob("*.md"))


def test_missing_required_fields_show_errors_and_keep_what_was_typed(ui, site):
    r = create(ui, "article", {"name": "A title", "content": ""})
    assert r.status_code == 422
    assert "Text is required" in r.text and 'value="A title"' in r.text
    assert not any(site.rglob("*.md"))


def test_article(ui, site):
    r = create(ui, "article", {"name": "Hello there", "content": "Body text", "mp-slug": "custom"})
    assert r.status_code == 303
    assert fm(site, "content/posts/2026/custom/index.md")[0]["title"] == "Hello there"


def test_bookmark(ui, site):
    create(
        ui,
        "bookmark",
        {"bookmark-of": "https://www.example.com/page", "name": "A page", "content": "Nice"},
    )
    front, _ = fm(site, "content/bookmarks/2026/10/14/1910/index.md")
    assert front["bookmark_of"] == "https://www.example.com/page" and "basedomain" not in front


@pytest.mark.parametrize("bad", ["not a url", "javascript:alert(1)", "ftp://x.example/", "http://"])
def test_links_must_be_web_addresses(ui, bad, site):
    r = create(ui, "bookmark", {"bookmark-of": bad})
    assert r.status_code == 422 and "web address" in r.text
    assert not any(site.rglob("*.md"))


def test_published_time_is_read_in_the_site_time_zone(ui, site):
    create(ui, "note", {"content": "back-dated", "published": "2025-01-02T03:04"})
    assert "back-dated" in (site / "content/notes/2025/01/02/0304.md").read_text()


def test_location(ui, site):
    create(ui, "note", {"content": "here", "location_lat": "52.5", "location_lng": "13.4"})
    assert fm(site, "content/notes/2026/10/14/1910.md")[0]["place_geo"] == {
        "lat": 52.5,
        "lng": 13.4,
    }


@pytest.mark.parametrize(("lat", "lng"), [("91", "0"), ("0", "181"), ("abc", "1"), ("1", "")])
def test_bad_coordinates_are_rejected(ui, lat, lng):
    r = create(ui, "note", {"content": "x", "location_lat": lat, "location_lng": lng})
    assert r.status_code == 422 and "coordinates" in r.text.lower()


def test_checkin(ui, site):
    create(
        ui,
        "checkin",
        {
            "checkin_name": "Cafe Jule",
            "checkin_lat": "52.5",
            "checkin_lng": "13.4",
            "content": "Coffee",
        },
    )
    front, body = fm(site, "content/checkins/2026/10/14/1910/index.md")
    assert front["title"] == "Checked in at Cafe Jule" and front["place_geo"] == {
        "lat": 52.5,
        "lng": 13.4,
    }
    assert "Coffee" in body


def test_page_goes_to_the_site_root(ui, site):
    create(ui, "page", {"name": "Uses", "content": "My kit."})
    assert (site / "content/uses.md").exists()


def test_quote(ui, site):
    create(ui, "quote", {"quotation-of": "https://example.com/post", "content": "A fine sentence."})
    front, body = fm(site, "content/quotes/2026/10/14/1910.md")
    assert front["quote_of"] == "https://example.com/post" and "A fine sentence." in body


def test_photos_with_alt_text_go_into_the_bundle(ui, site):
    r = create(
        ui,
        "photo",
        {"content": "Boats!", "photo_alt": ["A boat on a canal", "", "", ""]},
        files=[
            ("photo", ("boat.jpg", jpeg_bytes(), "image/jpeg")),
            ("photo", ("", b"", "application/octet-stream")),
        ],
    )
    assert r.status_code == 303, r.text
    assert (site / "content/photos/2026/10/14/1910/boat.jpg").exists()
    assert fm(site, "content/photos/2026/10/14/1910/index.md")[0]["images"] == [
        {"file": "boat.jpg", "alt": "A boat on a canal"}
    ]


def test_a_photo_post_needs_a_photo(ui):
    r = create(ui, "photo", {"content": "no picture"})
    assert r.status_code == 422 and "Photo is required" in r.text


def test_an_unsupported_file_is_an_error_on_the_photo_field(ui, site):
    r = create(
        ui, "photo", {"content": "x"}, files=[("photo", ("x.pdf", b"%PDF-1.4", "application/pdf"))]
    )
    assert r.status_code == 422 and "not supported" in r.text
    assert not any(site.rglob("*.md"))


def test_syndication_choice_is_recorded(ui, state):
    state.targets = [MastodonTarget(url="https://indieweb.social", user="awfulwoman", token="t")]
    create(ui, "note", {"content": "Hi", "syndicate": "https://indieweb.social/@awfulwoman"})
    props = json.loads(state.db.query("SELECT properties FROM posts")[0][0])
    assert props["mp-syndicate-to"] == ["https://indieweb.social/@awfulwoman"]


def test_store_failures_keep_the_form_and_say_why(ui, state, monkeypatch):
    from micropub_server.stores import StoreError

    async def boom(changes, message):
        raise StoreError(
            "GitHub could not update the branch: HTTP 403: nope. Check the token.", 403
        )

    monkeypatch.setattr(state.store, "commit", boom)
    r = create(ui, "note", {"content": "precious words"})
    assert r.status_code == 502 and "HTTP 403" in r.text and "precious words" in r.text


# --- the post page --------------------------------------------------------------------------------
def make(ui, **data):
    r = create(ui, "note", {"content": "Hello **world**", **data})
    return r.headers["location"].split("?")[0]


def test_post_page_shows_the_post(ui):
    page = ui.get(make(ui)).text
    assert "<strong>world</strong>" in page and "2026-10-14" in page
    assert "https://awfulwoman.com/notes/2026/10/14/1910/" in page


def test_preview_is_sanitised(ui):
    from html.parser import HTMLParser

    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.attrs = [], []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            self.attrs += [name for name, _ in attrs]

    page = ui.get(
        make(ui, content='<script>alert(1)</script><img src=x onerror="alert(2)"> text')
    ).text
    found = Elements()
    found.feed(page)
    assert "alert(1)" not in page.split("<main")[1].split("</main>")[0].replace(
        "&lt;script&gt;alert(1)&lt;/script&gt;", ""
    )
    assert not [a for a in found.attrs if a.startswith("on")]
    assert (
        "script" not in found.tags
    )  # the post page loads no scripts at all, so none came from the post


def test_titles_are_escaped(ui):
    r = create(ui, "article", {"name": "<b>Bold</b> & co", "content": "x"})
    page = ui.get(r.headers["location"]).text
    assert "&lt;b&gt;Bold&lt;/b&gt;" in page


def test_unknown_post_is_404(ui):
    assert ui.get("/posts/does-not-exist").status_code == 404


# --- the list ---------------------------------------------------------------------------------------------
def test_list_shows_posts_newest_first_with_badges(ui):
    create(ui, "note", {"content": "older note", "published": "2026-10-01T10:00"})
    create(ui, "note", {"content": "a draft", "published": "2026-10-10T10:00", "status": "draft"})
    html = ui.get("/posts").text
    assert html.index("a draft") < html.index("older note")
    assert 'class="badge draft"' in html


def test_list_filters(ui):
    create(
        ui, "note", {"content": "about cats", "category": "pets", "published": "2026-10-01T10:00"}
    )
    create(
        ui,
        "bookmark",
        {
            "bookmark-of": "https://example.com/",
            "name": "Dogs page",
            "published": "2026-10-02T10:00",
        },
    )
    assert (
        "about cats" in ui.get("/posts?q=cats").text
        and "Dogs page" not in ui.get("/posts?q=cats").text
    )
    assert (
        "Dogs page" in ui.get("/posts?type=bookmark").text
        and "about cats" not in ui.get("/posts?type=bookmark").text
    )
    assert (
        "about cats" in ui.get("/posts?tag=pets").text
        and "Dogs page" not in ui.get("/posts?tag=pets").text
    )
    assert "No posts match" in ui.get("/posts?q=zzzz").text


def test_list_paging(ui, state, monkeypatch):
    import micropub_server.routes_ui as r

    monkeypatch.setattr(r, "PAGE_SIZE", 2)
    for i in range(1, 6):
        create(ui, "note", {"content": f"post {i}", "published": f"2026-10-0{i}T10:00"})
    first = ui.get("/posts").text
    assert "post 5" in first and "post 4" in first and "post 3" not in first and "Older" in first
    older = re.search(r'href="([^"]+)">Older', first).group(1).replace("&amp;", "&")
    second = ui.get(older).text
    assert "post 3" in second and "post 2" in second and "Newer" in second
    newer = re.search(r'href="([^"]+)">&larr; Newer', second).group(1).replace("&amp;", "&")
    assert "post 5" in ui.get(newer).text


def test_list_keeps_filters_when_paging(ui, monkeypatch):
    import micropub_server.routes_ui as r

    monkeypatch.setattr(r, "PAGE_SIZE", 1)
    for i in (1, 2):
        create(ui, "note", {"content": f"note {i}", "published": f"2026-10-0{i}T10:00"})
    link = re.search(r'href="([^"]+)">Older', ui.get("/posts?type=note").text).group(1)
    assert "type=note" in link


def test_bad_paging_cursor_is_400(ui):
    assert ui.get("/posts?after=garbage").status_code == 400


# --- progressive enhancement ---------------------------------------------------------------
def _uid(response):
    return re.search(r"/posts/([^/?]+)", response.headers["location"]).group(1)


def test_tag_suggestions_come_from_tags_already_used(ui):
    create(ui, "note", {"content": "one", "category": "zfs, indieweb"})
    create(ui, "note", {"content": "two", "category": "zfs"})
    html = ui.get("/posts/new/note").text
    assert html.index('value="zfs"') < html.index('value="indieweb"')
    assert "data-tags" in html


def test_build_endpoint_reports_state_as_json(ui, state):
    uid = _uid(create(ui, "note", {"content": "built"}))
    state.db.execute(
        "INSERT INTO deploys (sha, post_uid, action) VALUES ('abc', ?, 'create')", (uid,)
    )
    r = ui.get(f"/posts/{uid}/build")
    assert r.json()["state"] == "pending" and r.headers["cache-control"] == "no-store"
    page = ui.get(f"/posts/{uid}").text
    assert f'data-poll="/posts/{uid}/build"' in page and "/static/app.js" in page
    state.db.execute("UPDATE deploys SET state = 'live' WHERE sha = 'abc'")
    assert ui.get(f"/posts/{uid}/build").json()["state"] == "live"
    assert "data-poll" not in ui.get(f"/posts/{uid}").text


def test_build_endpoint_needs_a_session(client):
    assert client.get("/posts/x/build", follow_redirects=False).status_code in {303, 401}


def test_post_page_images_point_at_the_site_not_the_editor(ui):
    r = create(ui, "note", {"content": "![a cat](files/cat.jpg)"})
    html = ui.get(r.headers["location"]).text
    assert re.search(r'<img src="https?://[^"]+/files/cat\.jpg"', html)
