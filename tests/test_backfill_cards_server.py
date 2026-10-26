"""backfill-cards without --checkout: posts in the database, each saved as an ordinary edit."""

import json
from datetime import date

import pytest
import respx

from micropub_server.backfill_cards import backfill_cards_server
from micropub_server.card_image import CardImage
from micropub_server.cli import main

from .conftest import sign_in
from .test_target_enrich import fm, make, update

TODAY = date.today().isoformat()
PAGE_URL = "https://a.example/x"
PIC = "https://a.example/pic.jpg"
PAGE = (
    '<html><head><title>Hello</title><meta property="og:site_name" content="Example">'
    '<meta property="og:description" content="About hello.">'
    f'<meta property="og:image" content="{PIC}"></head></html>'
)
BARE = "<html><head><title>Just a title</title></head><body>hi</body></html>"
WAYBACK = "https://archive.org/wayback/available"
SNAP = "https://web.archive.org/web/20261014120000id_"
WEBP = CardImage(b"RIFF-webp-bytes", 800, 419)
BOOKMARK = "content/bookmarks/2026/10/14/1910/index.md"
CARD = "content/bookmarks/2026/10/14/1910/card.webp"
LIKE = "content/likes/2026/10/14/1910/index.md"


@pytest.fixture
def full(client):
    token = sign_in(client, scope="create update delete media")["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def public(state):
    state.resolver = lambda host: ["93.184.216.34"]


@pytest.fixture
def commits(state, monkeypatch):
    seen = []
    original = state.store.commit

    async def spy(changes, message):
        seen.append(([c.path for c in changes], message))
        return await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    return seen


def bookmark(client, full, url=PAGE_URL):
    return make(client, full, **{"bookmark-of": url, "name": "b"})


def run(state, step="live", **kw):
    async def sleep(seconds):
        kw.get("sleeps", []).append(seconds)

    kw.pop("sleeps", None)
    return backfill_cards_server(state, step, sleep=sleep, delay=0, **kw)


async def test_live_saves_a_found_card_as_one_edit_with_its_image(
    client, full, site, images, state, commits
):
    bookmark(client, full)
    images.cards[PIC] = WEBP
    commits.clear()
    with respx.mock as router:
        router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state)
    assert (report.found, report.failed) == (1, 0)
    front = fm(site, BOOKMARK)
    assert (front["bookmark_site"], front["bookmark_description"]) == ("Example", "About hello.")
    assert front["bookmark_image"]["file"] == "card.webp"
    assert "bookmark_card" not in front
    assert (site / CARD).read_bytes() == WEBP.content
    assert len(commits) == 1 and set(commits[0][0]) == {BOOKMARK, CARD}
    row = state.db.query("SELECT * FROM media")[0]
    assert row["path"] == CARD
    assert (
        json.loads(state.db.query("SELECT properties FROM posts")[0]["properties"])[
            "bookmark-image"
        ]["file"]
        == "card.webp"
    )


async def test_imported_posts_are_left_to_checkout_mode(client, full, site, images, state, commits):
    bookmark(client, full)
    state.db.execute("UPDATE posts SET source = 'import'")
    images.cards[PIC] = WEBP
    commits.clear()
    with respx.mock as router:
        router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state)
    assert (report.found, report.skipped) == (0, 1)
    assert commits == []


async def test_dry_run_reports_and_writes_nothing(client, full, site, images, state, commits):
    bookmark(client, full)
    images.cards[PIC] = WEBP
    commits.clear()
    before = (site / BOOKMARK).read_text()
    props = state.db.query("SELECT properties FROM posts")[0]["properties"]
    with respx.mock as router:
        router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state, dry_run=True)
    assert report.found == 1 and len(report.changes) == 1
    assert (site / BOOKMARK).read_text() == before
    assert commits == []
    assert state.db.query("SELECT properties FROM posts")[0]["properties"] == props
    assert not state.db.query("SELECT * FROM media")


@pytest.mark.parametrize("status, result", [(404, "gone"), (200, "none")])
async def test_gone_and_none_are_remembered_in_the_marker(
    client, full, site, state, commits, status, result
):
    bookmark(client, full)
    commits.clear()
    with respx.mock as router:
        router.get(PAGE_URL).respond(status, html=BARE if status == 200 else "")
        report = await run(state)
    assert getattr(report, result) == 1
    assert fm(site, BOOKMARK)["bookmark_card"] == {"result": result, "checked": TODAY}
    assert len(commits) == 1


async def test_retry_writes_nothing_and_lists_the_url(client, full, site, state, commits):
    bookmark(client, full)
    commits.clear()
    with respx.mock as router:
        router.get(PAGE_URL).respond(429)
        report = await run(state)
    assert report.retry == 1 and report.retry_urls == [PAGE_URL]
    assert commits == [] and "bookmark_card" not in fm(site, BOOKMARK)


async def test_posts_with_a_card_or_a_marker_are_skipped_unless_rechecked(
    client, full, site, images, state, targets
):
    url = bookmark(client, full)
    update(client, full, url, replace={"bookmark-card": [{"result": "none"}]})
    with respx.mock as router:
        route = router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state)
        assert report.skipped == 1 and not route.called
        images.cards[PIC] = WEBP
        report = await run(state, recheck=True)
    assert report.found == 1 and "bookmark_card" not in fm(site, BOOKMARK)
    with respx.mock as router:
        route = router.get(PAGE_URL).respond(200, html=PAGE)
        assert (await run(state)).skipped == 1 and not route.called  # it has a card now


async def test_since_and_limit(client, full, state):
    bookmark(client, full)
    bookmark(client, full, "https://b.example/y")
    with respx.mock as router:
        route = router.get(PAGE_URL).respond(404)
        router.get("https://b.example/y").respond(404)
        assert (await run(state, since=2027)).skipped == 2
        assert not route.called
        report = await run(state, limit=1)
    assert report.gone == 1


async def test_a_like_gets_its_card(client, full, site, images, state):
    make(client, full, **{"like-of": PAGE_URL})
    images.cards[PIC] = WEBP
    with respx.mock as router:
        router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state, kind="like")
    assert report.found == 1
    assert fm(site, LIKE)["like_site"] == "Example"
    assert (site / "content/likes/2026/10/14/1910/card.webp").exists()


async def test_a_like_with_an_oembed_is_skipped(client, full, site, state):
    url = make(client, full, **{"like-of": PAGE_URL})
    update(client, full, url, replace={"like-oembed": ["<iframe></iframe>"]})
    with respx.mock as router:
        route = router.get(PAGE_URL).respond(200, html=PAGE)
        report = await run(state, kind="like")
    assert report.skipped == 1 and not route.called


async def test_archive_replaces_gone_with_the_card_and_the_archived_url(
    client, full, site, images, state, commits
):
    url = bookmark(client, full)
    update(client, full, url, replace={"bookmark-card": [{"result": "gone"}]})
    images.cards[f"{SNAP}/{PIC}"] = WEBP
    commits.clear()
    snap = {
        "archived_snapshots": {
            "closest": {"available": True, "status": "200", "timestamp": "20261014120000"}
        }
    }
    with respx.mock as router:
        lookup = router.get(WAYBACK).respond(200, json=snap)
        router.get(f"{SNAP}/{PAGE_URL}").respond(200, html=PAGE)
        report = await run(state, "archive")
    assert dict(lookup.calls[0].request.url.params)["timestamp"] == "20261014"
    assert report.found == 1
    front = fm(site, BOOKMARK)
    assert "bookmark_card" not in front
    assert (
        front["bookmark_card_archived"] == f"https://web.archive.org/web/20261014120000/{PAGE_URL}"
    )
    assert front["bookmark_site"] == "Example" and (site / CARD).exists()
    assert len(commits) == 1


async def test_archive_without_a_snapshot_becomes_none_and_skips_the_rest(
    client, full, site, state
):
    gone = bookmark(client, full)
    update(client, full, gone, replace={"bookmark-card": [{"result": "gone"}]})
    bookmark(client, full, "https://b.example/y")
    with respx.mock as router:
        router.get(WAYBACK).respond(200, json={"archived_snapshots": {}})
        report = await run(state, "archive")
    assert (report.none, report.skipped) == (1, 1)
    assert fm(site, BOOKMARK)["bookmark_card"]["result"] == "none"


async def test_scraped_is_refused_in_server_mode(state):
    with pytest.raises(ValueError, match="scraped"):
        await run(state, "scraped")


def test_cli_refuses_scraped_without_a_checkout(capsys):
    assert main(["backfill-cards", "--step", "scraped"]) == 1
    assert "--checkout" in capsys.readouterr().err
