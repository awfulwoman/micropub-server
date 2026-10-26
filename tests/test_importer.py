import json

import pytest
from ruamel.yaml import YAML

from micropub_server.importer import import_posts
from micropub_server.stores import FileSystemStore

from .conftest import sign_in

FILES = {
    # modern note: +0100 date without a colon, a title, tags
    "content/notes/2025/02/11/1855.md": '---\ntitle: An ode to ZFS\ndate: "2025-02-11T18:55:20+0100"\ntags:\n  - zfs\nsyndication:\n  - https://m.example/1\n---\n\nA disk is dying.\n',
    # old layout: bundle with an image next to it
    "content/notes/2018/3289/index.md": "---\ndate: '2018-05-01T10:00:00.000Z'\nimages:\n  - file: a.jpg\n    alt: A boat\n---\nBoats!\n",
    "content/notes/2018/3289/a.jpg": "JPEGDATA",
    "content/bookmarks/2025/01/23/1059.md": '---\ntitle: "Internet-Week Clock"\ndate: "2025-01-23T10:59:12+0100"\nbookmark_of: https://hackaday.io/project/202182\nbasedomain: hackaday.io\n---\nRidiculous.\n',
    # extras that layouts or Hugo rely on must survive
    "content/notes/2020/10/20201030t0952/index.md": "---\ndate: '2020-10-30'\nimages:\n  - width: 4608\n    height: 2592\n    alt: A plate\n    file: plate.jpg\n---\r\nLine one.\r\n\r\nLine two.\r\n",
    "content/notes/2020/10/20201030t0952/plate.jpg": "JPG",
    "content/bookmarks/2012/1125/index.md": "---\ntitle: Old bookmark\ndate: '2012-11-25T10:00:00+00:00'\nbookmark_of: https://lab.example.com/x\n---\nOld.\n",
    "content/likes/2021/20210223t114722/index.md": "---\nlike_of: 'https://twitter.com/x/status/1'\ndate: '2021-02-23T11:47:22.655Z'\nmodified: '2021-02-24T08:00:00.000Z'\nlike_of_oembed_twitter: \"<blockquote>hi</blockquote>\"\nadd_like: true\n---\n",
    "content/posts/2013/old-post/index.md": "---\ntitle: Old Post\ndate: '2013-06-20T13:10:00+01:00'\nslug: a-custom-slug\nstrapline: A strapline\naliases:\n  - /old/url/\n---\nBody text here.\n",
    # no slug in the front matter: Hugo's /posts/:slug/ permalink uses the title, not the folder
    "content/posts/2025/new-apartment/index.md": "---\ntitle: New Apartment, New Smarts\ndate: '2025-04-09T19:44:05+02:00'\n---\nWe bought a flat.\n",
    "content/checkins/2019/3384/index.md": "---\ntitle: 'Checked in at Gesundbrunnen, Berlin'\ndate: '2019-10-05T15:14:35+00:00'\nplace_geo:\n  lat: 52.54\n  lng: 13.38\nplace_name: 'Gesundbrunnen, Berlin'\n---\nBack in Berlin!\n",
    "content/colophon.md": "---\ntitle: Colophon\ndate: 2024-11-22\nmenus:\n  footer:\n    weight: 5\n---\nHow this site is made.\n",
    "content/notes/2025/03/01/0900.md": "---\ntitle: A draft\ndate: '2025-03-01T09:00:00+01:00'\ndraft: true\n---\nNot yet.\n",
    # things that are not posts, or are broken
    "content/guineapigs/ginger/index.md": "---\ntitle: Ginger\n---\n",
    "content/about.md": "---\ntitle: About\n---\n",
    "content/notes/2025/04/01/nodate.md": "---\ntitle: No date\n---\nx\n",
    "content/notes/2025/04/02/broken.md": "---\ntitle: [unclosed\n---\nx\n",
}


@pytest.fixture
def populated(state, site):
    for path, text in FILES.items():
        f = site / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return state


def posts(state):
    return {r["path"]: r for r in state.db.query("SELECT * FROM posts")}


async def run(state, **kw):
    return await import_posts(state, FileSystemStore(state.store.root), **kw)


async def test_report_counts(populated):
    report = await run(populated)
    assert report.imported == 11
    assert report.failed == 2 and {p for p, _ in report.problems} == {
        "content/notes/2025/04/01/nodate.md",
        "content/notes/2025/04/02/broken.md",
    }
    assert report.skipped == 2  # guineapigs and about.md are not posts
    assert "no date" in dict(report.problems)["content/notes/2025/04/01/nodate.md"].lower()


async def test_type_comes_from_the_section_not_the_content(populated):
    await run(populated)
    rows = posts(populated)
    # a note with a title is a note here, although discovery on its content would say article
    assert rows["content/notes/2025/02/11/1855.md"]["post_type"] == "note"
    assert rows["content/posts/2013/old-post/index.md"]["post_type"] == "article"
    assert rows["content/colophon.md"]["post_type"] == "page"
    assert rows["content/likes/2021/20210223t114722/index.md"]["post_type"] == "like"


async def test_urls_follow_the_templates_or_hugo_defaults(populated):
    await run(populated)
    rows = posts(populated)
    assert (
        rows["content/notes/2025/02/11/1855.md"]["url"]
        == "https://awfulwoman.com/notes/2025/02/11/1855/"
    )
    assert (
        rows["content/notes/2018/3289/index.md"]["url"] == "https://awfulwoman.com/notes/2018/3289/"
    )
    # front matter slug decides the article URL, not the directory name
    assert (
        rows["content/posts/2013/old-post/index.md"]["url"]
        == "https://awfulwoman.com/posts/a-custom-slug/"
    )


async def test_article_url_slug_follows_hugo_not_the_folder(populated):
    await run(populated)
    row = posts(populated)["content/posts/2025/new-apartment/index.md"]
    assert row["url"] == "https://awfulwoman.com/posts/new-apartment-new-smarts/"
    assert json.loads(row["properties"])["slug"] == "new-apartment-new-smarts"


async def test_pages_sit_at_the_site_root_and_keep_their_menus(populated):
    await run(populated)
    row = posts(populated)["content/colophon.md"]
    assert row["url"] == "https://awfulwoman.com/colophon/"
    assert json.loads(row["properties"])["front-matter"]["menus"] == {"footer": {"weight": 5}}


async def test_undated_pages_and_unknown_sections_are_left_alone(populated):
    report = await run(populated)
    paths = set(posts(populated))
    # about.md has no date, and guineapigs is not a post type: neither is a post, neither is a problem
    assert "content/about.md" not in paths and "content/guineapigs/ginger/index.md" not in paths
    assert "content/about.md" not in dict(report.problems)


async def test_properties_are_reversed_from_front_matter(populated):
    await run(populated)
    p = json.loads(posts(populated)["content/notes/2025/02/11/1855.md"]["properties"])
    assert p["name"] == "An ode to ZFS" and p["category"] == ["zfs"]
    assert p["syndication"] == ["https://m.example/1"] and "front-matter" not in p
    assert p["published"] == "2025-02-11T18:55:20+01:00"
    assert p["content"]["text"] == "A disk is dying." and "<p>" in p["content"]["html"]
    assert p["post-type"] == "note" and p["post-status"] == "published" and p["type"] == "entry"


async def test_all_the_date_formats_in_the_wild(populated):
    await run(populated)
    rows = posts(populated)
    published = {path: r["published"] for path, r in rows.items()}
    assert published["content/notes/2018/3289/index.md"] == "2018-05-01T12:00:00+02:00"  # ...Z
    assert published["content/colophon.md"] == "2024-11-22T00:00:00+01:00"  # date only
    assert published["content/checkins/2019/3384/index.md"] == "2019-10-05T17:14:35+02:00"


async def test_links_and_basedomain(populated):
    await run(populated)
    p = json.loads(posts(populated)["content/bookmarks/2025/01/23/1059.md"]["properties"])
    assert p["bookmark-of"] == "https://hackaday.io/project/202182"
    assert p["front-matter"] == {"basedomain": "hackaday.io"}  # as found, not recomputed


async def test_photo_dimensions_and_crlf_line_endings(populated):
    await run(populated)
    p = json.loads(posts(populated)["content/notes/2020/10/20201030t0952/index.md"]["properties"])
    assert p["photo"] == [{"url": "plate.jpg", "alt": "A plate", "width": 4608, "height": 2592}]
    assert p["content"]["text"] == "Line one.\n\nLine two."


async def test_checkin_becomes_a_card(populated):
    await run(populated)
    p = json.loads(posts(populated)["content/checkins/2019/3384/index.md"]["properties"])
    assert p["checkin"] == {
        "type": "card",
        "name": "Gesundbrunnen, Berlin",
        "latitude": "52.54",
        "longitude": "13.38",
    }


async def test_unknown_keys_are_kept_verbatim(populated):
    await run(populated)
    like = json.loads(posts(populated)["content/likes/2021/20210223t114722/index.md"]["properties"])
    assert like["front-matter"]["like_of_oembed_twitter"] == "<blockquote>hi</blockquote>"
    assert like["front-matter"]["add_like"] is True
    assert (
        like["updated"] == "2021-02-24T09:00:00+01:00"
    )  # modified -> updated, not kept as an extra
    assert "modified" not in like["front-matter"]
    post = json.loads(posts(populated)["content/posts/2013/old-post/index.md"]["properties"])
    assert post["front-matter"] == {
        "slug": "a-custom-slug",
        "strapline": "A strapline",
        "aliases": ["/old/url/"],
    }
    assert post["slug"] == "a-custom-slug"


async def test_draft_flag(populated):
    await run(populated)
    row = posts(populated)["content/notes/2025/03/01/0900.md"]
    assert row["post_status"] == "draft"


async def test_bundle_attachments_are_recorded_as_media(populated):
    await run(populated)
    m = populated.db.query("SELECT * FROM media WHERE path LIKE '%/3289/a.jpg'")[0]
    assert m["path"] == "content/notes/2018/3289/a.jpg" and m["media_type"] == "photo"
    assert m["url"] == "https://awfulwoman.com/notes/2018/3289/a.jpg" and m["post_uid"]
    assert m["md5"] == __import__("hashlib").md5(b"JPEGDATA").hexdigest()


async def test_rows_are_marked_as_imported_with_stable_uids(populated):
    await run(populated)
    first = {p: r["uid"] for p, r in posts(populated).items()}
    assert {r["source"] for r in posts(populated).values()} == {"import"}
    await run(populated)
    assert {p: r["uid"] for p, r in posts(populated).items()} == first
    assert len(posts(populated)) == 11


async def test_rerun_reports_nothing_new(populated):
    await run(populated)
    again = await run(populated)
    assert again.imported == 0 and again.unchanged == 11


async def test_dry_run_writes_nothing(populated):
    report = await run(populated, dry_run=True)
    assert report.imported == 11
    assert populated.db.query("SELECT * FROM posts") == []


async def test_section_filter(populated):
    report = await run(populated, only_type="bookmark")
    assert report.imported == 2


async def test_never_overwrites_posts_made_by_the_server(populated, client):
    token = sign_in(client)["access_token"]
    r = client.post(
        "/micropub", data={"content": "mine"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 202
    # an imported file that collides with that post's path
    (populated.store.root / "content/notes/2026/10/14").mkdir(parents=True, exist_ok=True)
    (populated.store.root / "content/notes/2026/10/14/1910.md").write_text(
        "---\ntitle: Imported\ndate: '2026-10-14T19:10:47+02:00'\n---\nx\n"
    )
    report = await run(populated)
    assert any("already exists" in reason for _, reason in report.problems)
    row = populated.db.query(
        "SELECT source, properties FROM posts WHERE path = 'content/notes/2026/10/14/1910.md'"
    )[0]
    assert row["source"] == "micropub"


# --- imported posts work like any other ------------------------------------------------
@pytest.fixture
def imported(populated, client):
    import asyncio

    asyncio.run(run(populated))
    token = sign_in(client, scope="create update delete")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1]), text.split("---\n", 2)[2]


def test_imported_posts_appear_in_queries(client, imported):
    items = client.get(
        "/micropub", params={"q": "source", "limit": "100"}, headers=imported
    ).json()["items"]
    assert len(items) == 11
    one = client.get(
        "/micropub",
        params={"q": "source", "url": "https://awfulwoman.com/notes/2025/02/11/1855/"},
        headers=imported,
    ).json()
    assert one["properties"]["name"] == ["An ode to ZFS"]


def test_editing_an_imported_note_does_not_turn_it_into_an_article(client, imported, site):
    url = "https://awfulwoman.com/notes/2025/02/11/1855/"
    r = client.post(
        "/micropub",
        json={"action": "update", "url": url, "add": {"category": ["openzfs"]}},
        headers=imported,
    )
    assert r.status_code == 200, r.text  # 200, not 201: it stayed where it was
    front, body = fm(site, "content/notes/2025/02/11/1855.md")
    assert front["tags"] == ["zfs", "openzfs"] and front["title"] == "An ode to ZFS"
    assert "A disk is dying." in body


def test_editing_keeps_front_matter_that_hugo_needs(client, imported, site):
    url = "https://awfulwoman.com/posts/a-custom-slug/"
    r = client.post(
        "/micropub",
        json={"action": "update", "url": url, "replace": {"content": ["New body."]}},
        headers=imported,
    )
    assert r.status_code == 200, r.text
    front, body = fm(site, "content/posts/2013/old-post/index.md")
    assert (
        front["slug"] == "a-custom-slug"
        and front["strapline"] == "A strapline"
        and front["aliases"] == ["/old/url/"]
    )
    assert front["title"] == "Old Post" and body.strip() == "New body."


def test_editing_a_bundle_keeps_its_photos_with_it(client, imported, site):
    url = "https://awfulwoman.com/notes/2018/3289/"
    r = client.post(
        "/micropub",
        json={"action": "update", "url": url, "replace": {"mp-slug": ["boats"]}},
        headers=imported,
    )
    assert r.status_code in (200, 201), r.text
    assert (site / "content/notes/2018/3289/a.jpg").exists()


def test_imported_posts_can_be_deleted_and_restored(client, imported, site):
    url = "https://awfulwoman.com/bookmarks/2025/01/23/1059/"
    before = (site / "content/bookmarks/2025/01/23/1059.md").read_text()
    assert (
        client.post(
            "/micropub", data={"action": "delete", "url": url}, headers=imported
        ).status_code
        == 200
    )
    assert not (site / "content/bookmarks/2025/01/23/1059.md").exists()
    assert (
        client.post(
            "/micropub", data={"action": "undelete", "url": url}, headers=imported
        ).status_code
        == 200
    )
    front, _ = fm(site, "content/bookmarks/2025/01/23/1059.md")
    assert (
        front["bookmark_of"] == "https://hackaday.io/project/202182"
        and front["title"] == "Internet-Week Clock"
    )
    assert before  # the original existed


# --- --check-urls ------------------------------------------------------------------
async def test_check_urls_reports_what_the_live_site_does_not_serve(populated, no_network):
    import httpx

    from micropub_server.importer import check_urls

    await run(populated)
    # respx tries routes in the order they were added, so the specific one goes first
    no_network.get("https://awfulwoman.com/notes/2025/02/11/1855/").mock(
        return_value=httpx.Response(404)
    )
    no_network.get(url__regex=r"https://awfulwoman\.com/.*").mock(return_value=httpx.Response(200))
    bad = await check_urls(populated, concurrency=4)
    assert bad == [("https://awfulwoman.com/notes/2025/02/11/1855/", 404)]


def test_editing_an_old_bookmark_does_not_add_a_basedomain(client, imported, site):
    url = "https://awfulwoman.com/bookmarks/2012/1125/"
    r = client.post(
        "/micropub",
        json={"action": "update", "url": url, "add": {"category": ["old"]}},
        headers=imported,
    )
    assert r.status_code == 200, r.text
    front, _ = fm(site, "content/bookmarks/2012/1125/index.md")
    assert front["tags"] == ["old"] and "basedomain" not in front


# --- link cards on bookmarks (spec 16) ---------------------------------------------------
CARD_PATH = "content/bookmarks/2026/10/26/snopes/index.md"
CARD_FM = """---
title: Jack Black's mom
date: "2026-10-26T09:00:00+01:00"
bookmark_of: 'https://www.snopes.com/fact-check/jack-black-mom/'
bookmark_author: Alex Kasprak
bookmark_site: Snopes.com
bookmark_description: A tribute.
bookmark_image:
  file: card.webp
  alt: ''
  width: 800
  height: 419
bookmark_card_archived: https://web.archive.org/web/2020id_/https://www.snopes.com/x/
---
Nice.
"""
GONE_PATH = "content/bookmarks/2026/10/26/dead/index.md"
GONE_FM = """---
title: Dead link
date: "2026-10-26T10:00:00+01:00"
bookmark_of: https://dead.example/
bookmark_card:
  result: gone
  checked: 2026-10-26
---
"""


@pytest.fixture
def cards(state, site):
    for path, text in {
        CARD_PATH: CARD_FM,
        GONE_PATH: GONE_FM,
        CARD_PATH.replace("index.md", "card.webp"): "WEBP",
    }.items():
        f = site / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return state


async def test_card_keys_become_properties_not_extras(cards):
    await run(cards)
    p = json.loads(posts(cards)[CARD_PATH]["properties"])
    assert p["bookmark-site"] == "Snopes.com" and p["bookmark-description"] == "A tribute."
    assert p["bookmark-image"] == {"file": "card.webp", "alt": "", "width": 800, "height": 419}
    assert (
        p["bookmark-card-archived"]
        == "https://web.archive.org/web/2020id_/https://www.snopes.com/x/"
    )
    assert p["front-matter"] == {"bookmark_author": "Alex Kasprak"}  # kept as before
    gone = json.loads(posts(cards)[GONE_PATH]["properties"])
    assert gone["bookmark-card"] == {"result": "gone", "checked": "2026-10-26"}
    assert "front-matter" not in gone


async def test_card_webp_is_recorded_as_media(cards):
    await run(cards)
    m = cards.db.query("SELECT * FROM media WHERE path LIKE '%/snopes/card.webp'")[0]
    assert m["media_type"] == "photo" and m["content_type"] == "image/webp" and m["post_uid"]
    assert m["url"] == "https://awfulwoman.com/bookmarks/2026/10/26/snopes/card.webp"


async def test_card_posts_rerun_unchanged(cards):
    first = await run(cards)
    assert first.imported == 2 and first.failed == 0
    again = await run(cards)
    assert again.imported == 0 and again.unchanged == 2


async def test_card_front_matter_round_trips(cards):
    from micropub_server.publish import render_file

    await run(cards)

    def load(text):
        y = YAML(typ="safe")
        y.constructor.add_constructor(  # a bare date is the same text, whichever way it is read
            "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
        )
        return y.load(text.split("---\n")[1])

    for path, original in ((CARD_PATH, CARD_FM), (GONE_PATH, GONE_FM)):
        props = json.loads(posts(cards)[path]["properties"])
        assert load(render_file(cards, props)) == load(original)


async def test_like_card_keys_become_properties_too(state, site):
    path = "content/likes/2026/10/26/read/index.md"
    f = site / path
    f.parent.mkdir(parents=True)
    f.write_text(
        "---\ndate: '2026-10-26T09:00:00+01:00'\nlike_of: https://a.example/x\n"
        "like_site: A\nlike_description: D\nlike_image: {file: card.webp, alt: '', width: 8, height: 4}\n"
        "like_card: {result: none, checked: 2026-10-26}\n---\n"
    )
    await run(state)
    p = json.loads(posts(state)[path]["properties"])
    assert (p["like-site"], p["like-description"]) == ("A", "D")
    assert p["like-image"]["file"] == "card.webp"
    assert p["like-card"] == {"result": "none", "checked": "2026-10-26"}
    assert "front-matter" not in p
