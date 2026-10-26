"""backfill-cards --step live and --step archive on a temp checkout, with the network mocked."""

import io
from datetime import date

import httpx
import pytest
import respx
from PIL import Image

from micropub_server.backfill_cards import STEPS, backfill_archive, backfill_live
from micropub_server.cli import main
from micropub_server.config import MediaCard

CFG = MediaCard()
TODAY = date.today().isoformat()
PAGE = (
    '<html><head><title>Hello</title><meta property="og:site_name" content="Example">'
    '<meta property="og:description" content="About hello.">'
    '<meta property="og:image" content="/pic.jpg"></head></html>'
)
TEXT_ONLY = (
    '<html><head><title>T</title><meta name="description" content="Only text."></head></html>'
)
BARE = "<html><head><title>Just a title</title></head><body>hi</body></html>"
EMPTY = "<html><head></head><body>parked</body></html>"


def resolver(host):
    return ["93.184.216.34"]


def jpeg(size=(1000, 500)) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 40).convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


def post(root, name, url="https://a.example/x", kind="bookmark", extra="", date_="2021-07-07",
         bundle=True):  # fmt: skip
    day = root / f"content/{kind}s/{date_[:4]}/{date_[5:7]}/{date_[8:10]}"
    text = f"---\n{kind}_of: '{url}'\ndate: '{date_}T09:38:46Z'\n{extra}---\nBody\n"
    if bundle:
        (day / name).mkdir(parents=True)
        path = day / name / "index.md"
    else:
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"{name}.md"
    path.write_text(text)
    return path


async def live(root, kind="bookmark", **kw):
    sleeps = kw.pop("sleeps", [])

    async def sleep(seconds):
        sleeps.append(seconds)

    kw.setdefault("delay", 1.0)
    return await backfill_live(root, CFG, kind=kind, resolver=resolver, sleep=sleep, **kw)


def front(path):
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(path.read_text().split("---\n")[1])


def page_route(router, url="https://a.example/x", **kw):
    return router.get(url).mock(**kw)


def test_steps_include_live_and_archive():
    assert STEPS == ("scraped", "live", "archive")


async def test_found_writes_keys_and_image_and_no_marker(tmp_path):
    path = post(tmp_path, "a")
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=PAGE))
        router.get("https://a.example/pic.jpg").respond(200, content=jpeg())
        report = await live(tmp_path)
    assert report.found == 1
    meta = front(path)
    assert meta["bookmark_site"] == "Example"
    assert meta["bookmark_description"] == "About hello."
    assert meta["bookmark_image"] == {"file": "card.webp", "alt": "", "width": 800, "height": 400}
    assert "bookmark_card" not in meta
    assert (path.parent / "card.webp").exists()
    assert "title" not in meta


async def test_rest_of_the_file_is_byte_for_byte(tmp_path):
    path = post(tmp_path, "a", extra="tags:\n  - x\n")
    before = path.read_text()
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=TEXT_ONLY))
        await live(tmp_path)
    after = path.read_text()
    added = "bookmark_site: a.example\nbookmark_description: Only text.\n"
    assert after == before.replace("date:", added + "date:")
    for line in before.splitlines(keepends=True):
        assert line in after


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(410),
        httpx.Response(200, html=EMPTY),
        httpx.ConnectError("refused"),
    ],
)
async def test_gone_writes_the_marker(tmp_path, response):
    path = post(tmp_path, "a")
    with respx.mock as router:
        if isinstance(response, Exception):
            page_route(router, side_effect=response)
        else:
            page_route(router, return_value=response)
        report = await live(tmp_path)
    assert (report.gone, report.found) == (1, 0)
    assert front(path)["bookmark_card"] == {"result": "gone", "checked": date.today()}
    assert f"  checked: {TODAY}\n" in path.read_text()


async def test_dns_failure_is_gone(tmp_path):
    from micropub_server.auth.client import FetchError

    def nxdomain(host):
        raise FetchError(f"cannot resolve {host}")

    path = post(tmp_path, "a")
    report = await backfill_live(tmp_path, CFG, resolver=nxdomain, delay=0)
    assert report.gone == 1
    assert front(path)["bookmark_card"]["result"] == "gone"


async def test_none_when_answered_without_card_tags(tmp_path):
    path = post(tmp_path, "a")
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=BARE))
        report = await live(tmp_path)
    assert report.none == 1
    assert front(path)["bookmark_card"]["result"] == "none"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(429),
        httpx.Response(503),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_retry_writes_nothing_and_lists_the_url(tmp_path, response):
    path = post(tmp_path, "a")
    before = path.read_text()
    with respx.mock as router:
        if isinstance(response, Exception):
            page_route(router, side_effect=response)
        else:
            page_route(router, return_value=response)
        report = await live(tmp_path)
    assert report.retry == 1
    assert report.retry_urls == ["https://a.example/x"]
    assert path.read_text() == before


async def test_next_run_skips_found_gone_and_none_and_retries_retry(tmp_path):
    found = post(tmp_path, "f", "https://a.example/f")
    gone = post(tmp_path, "g", "https://a.example/g")
    none = post(tmp_path, "n", "https://a.example/n")
    retry = post(tmp_path, "r", "https://a.example/r")
    with respx.mock as router:
        router.get("https://a.example/f").respond(200, html=TEXT_ONLY)
        router.get("https://a.example/g").respond(404)
        router.get("https://a.example/n").respond(200, html=BARE)
        router.get("https://a.example/r").respond(503)
        await live(tmp_path)
    snapshot = {p: p.read_text() for p in (found, gone, none, retry)}
    with respx.mock as router:
        f = router.get("https://a.example/f").respond(200, html=TEXT_ONLY)
        g = router.get("https://a.example/g").respond(404)
        n = router.get("https://a.example/n").respond(200, html=BARE)
        r = router.get("https://a.example/r").respond(200, html=TEXT_ONLY)
        report = await live(tmp_path)
    assert (f.called, g.called, n.called, r.called) == (False, False, False, True)
    assert report.skipped == 3 and report.found == 1
    assert all(p.read_text() == snapshot[p] for p in (found, gone, none))
    assert "bookmark_description" in retry.read_text()


async def test_recheck_ignores_the_marker(tmp_path):
    path = post(tmp_path, "a", extra="bookmark_card:\n  result: none\n  checked: 2020-01-01\n")
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=TEXT_ONLY))
        report = await live(tmp_path, recheck=True)
    assert report.found == 1
    meta = front(path)
    assert "bookmark_card" not in meta and meta["bookmark_description"] == "Only text."


async def test_post_with_card_keys_is_never_touched(tmp_path):
    path = post(tmp_path, "a", extra="bookmark_site: Mine\n")
    before = path.read_text()
    with respx.mock as router:
        route = page_route(router, return_value=httpx.Response(200, html=PAGE))
        report = await live(tmp_path, recheck=True)
    assert not route.called and report.skipped == 1
    assert path.read_text() == before


async def test_flat_file_with_an_image_moves_to_a_bundle(tmp_path):
    path = post(tmp_path, "x", bundle=False)
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=PAGE))
        router.get("https://a.example/pic.jpg").respond(200, content=jpeg())
        await live(tmp_path)
    moved = path.with_suffix("") / "index.md"
    assert not path.exists() and moved.exists()
    assert (moved.parent / "card.webp").exists()
    assert front(moved)["bookmark_image"]["file"] == "card.webp"


async def test_flat_file_without_an_image_stays_flat(tmp_path):
    path = post(tmp_path, "x", bundle=False)
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=TEXT_ONLY))
        await live(tmp_path)
    assert path.exists() and "bookmark_description" in path.read_text()


async def test_no_image_option_saves_text_only(tmp_path):
    path = post(tmp_path, "a")
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=PAGE))
        pic = router.get("https://a.example/pic.jpg").respond(200, content=jpeg())
        await live(tmp_path, image=False)
    assert not pic.called and not (path.parent / "card.webp").exists()
    assert "bookmark_image" not in front(path)


async def test_dry_run_writes_nothing_but_counts(tmp_path):
    post(tmp_path, "a", "https://a.example/a")
    post(tmp_path, "b", "https://a.example/b")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with respx.mock as router:
        router.get("https://a.example/a").respond(200, html=TEXT_ONLY)
        router.get("https://a.example/b").respond(404)
        report = await live(tmp_path, dry_run=True)
    assert (report.found, report.gone) == (1, 1)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


async def test_limit_since_before(tmp_path):
    post(tmp_path, "old", "https://a.example/old", date_="2014-03-01")
    post(tmp_path, "mid", "https://a.example/mid", date_="2021-07-07")
    post(tmp_path, "new", "https://a.example/new", date_="2026-10-20")
    with respx.mock as router:
        old = router.get("https://a.example/old").respond(200, html=TEXT_ONLY)
        mid = router.get("https://a.example/mid").respond(200, html=TEXT_ONLY)
        new = router.get("https://a.example/new").respond(200, html=TEXT_ONLY)
        await live(tmp_path, before="2026-10-14", since=2020)
    assert (old.called, mid.called, new.called) == (False, True, False)
    with respx.mock as router:
        old = router.get("https://a.example/old").respond(200, html=TEXT_ONLY)
        router.get("https://a.example/new").respond(200, html=TEXT_ONLY)
        report = await live(tmp_path, limit=1)
    assert report.found == 1 and old.called  # the oldest first; stops after one


async def test_delay_sleeps_between_fetches_only(tmp_path):
    for n in "abc":
        post(tmp_path, n, f"https://a.example/{n}")
    sleeps = []
    with respx.mock as router:
        router.get(host="a.example").respond(200, html=TEXT_ONLY)
        await live(tmp_path, delay=2.5, sleeps=sleeps)
    assert sleeps == [2.5, 2.5]


async def test_private_address_is_never_fetched(tmp_path):
    def private(host):
        return ["10.0.0.1"]

    path = post(tmp_path, "a")
    with respx.mock as router:
        route = page_route(router, return_value=httpx.Response(200, html=PAGE))
        report = await backfill_live(tmp_path, CFG, resolver=private, delay=0)
    assert not route.called and report.none == 1
    assert front(path)["bookmark_card"]["result"] == "none"


# --- likes ---


async def test_like_gets_like_keys(tmp_path):
    path = post(tmp_path, "a", kind="like", extra="like_title: T\nlike_author: A\n")
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=TEXT_ONLY))
        await live(tmp_path, kind="like")
    assert list(front(path))[:3] == ["like_of", "like_site", "like_description"]
    assert front(path)["like_description"] == "Only text."


async def test_like_with_oembed_is_skipped_without_a_fetch(tmp_path):
    path = post(tmp_path, "a", kind="like", extra="like_oembed: '<blockquote></blockquote>'\n")
    before = path.read_text()
    with respx.mock as router:
        route = page_route(router, return_value=httpx.Response(200, html=PAGE))
        report = await live(tmp_path, kind="like")
    assert not route.called and report.skipped == 1 and path.read_text() == before


async def test_like_whose_fetch_finds_an_oembed_gets_nothing(tmp_path):
    url = "https://a.example/x"
    html = (
        '<html><head><title>T</title><meta property="og:description" content="D">'
        '<link rel="alternate" type="application/json+oembed" href="https://a.example/oembed">'
        "</head></html>"
    )
    path = post(tmp_path, "a", url, kind="like")
    before = path.read_text()
    with respx.mock as router:
        page_route(router, return_value=httpx.Response(200, html=html))
        router.get("https://a.example/oembed").respond(
            200, json={"html": "<blockquote><p>hi</p></blockquote>"}
        )
        report = await live(tmp_path, kind="like")
    assert report.embed == 1 and path.read_text() == before


async def test_tweet_with_no_embed_is_none_not_gone(tmp_path):
    url = "https://twitter.com/a/status/1"
    path = post(tmp_path, "a", url, kind="like")
    with respx.mock as router:
        router.get(host="publish.twitter.com").respond(404)
        report = await live(tmp_path, kind="like")
    assert (report.none, report.gone) == (1, 0)
    assert front(path)["like_card"]["result"] == "none"


# --- archive ---

WAYBACK = "https://archive.org/wayback/available"


def snapshot(ts="20210707120000", url="https://a.example/x", status="200"):
    return {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "status": status,
                "timestamp": ts,
                "url": f"http://web.archive.org/web/{ts}/{url}",
            }
        }
    }


async def archive(root, kind="bookmark", **kw):
    sleeps = kw.pop("sleeps", [])

    async def sleep(seconds):
        sleeps.append(seconds)

    kw.setdefault("delay", 4.0)
    return await backfill_archive(root, CFG, kind=kind, resolver=resolver, sleep=sleep, **kw)


GONE = "bookmark_card:\n  result: gone\n  checked: 2026-10-01\n"


async def test_archive_success_writes_keys_url_and_removes_the_marker(tmp_path):
    path = post(tmp_path, "a", extra=GONE)
    with respx.mock as router:
        lookup = router.get(WAYBACK).respond(200, json=snapshot())
        router.get("https://web.archive.org/web/20210707120000id_/https://a.example/x").respond(
            200, html=PAGE
        )
        router.get(
            "https://web.archive.org/web/20210707120000id_/https://a.example/pic.jpg"
        ).respond(200, content=jpeg())
        report = await archive(tmp_path)
    assert report.found == 1
    request = lookup.calls[0].request
    assert dict(request.url.params) == {"url": "https://a.example/x", "timestamp": "20210707"}
    meta = front(path)
    assert "bookmark_card" not in meta
    assert meta["bookmark_site"] == "Example"
    assert (
        meta["bookmark_card_archived"]
        == "https://web.archive.org/web/20210707120000/https://a.example/x"
    )
    assert meta["bookmark_image"]["file"] == "card.webp"
    assert (path.parent / "card.webp").exists()


async def test_archive_only_looks_at_gone(tmp_path):
    none = post(tmp_path, "n", "https://a.example/n", extra=GONE.replace("gone", "none"))
    plain = post(tmp_path, "p", "https://a.example/p")
    before = (none.read_text(), plain.read_text())
    with respx.mock as router:
        lookup = router.get(WAYBACK).respond(200, json=snapshot())
        report = await archive(tmp_path)
    assert not lookup.called and report.skipped == 2
    assert (none.read_text(), plain.read_text()) == before


async def test_archive_without_a_snapshot_becomes_none(tmp_path):
    path = post(tmp_path, "a", extra=GONE)
    with respx.mock as router:
        router.get(WAYBACK).respond(200, json={"archived_snapshots": {}})
        report = await archive(tmp_path)
    assert report.none == 1
    assert front(path)["bookmark_card"]["result"] == "none"
    assert front(path)["bookmark_card"]["checked"] == date.today()


async def test_archive_snapshot_without_card_tags_becomes_none(tmp_path):
    path = post(tmp_path, "a", extra=GONE)
    with respx.mock as router:
        router.get(WAYBACK).respond(200, json=snapshot())
        router.get("https://web.archive.org/web/20210707120000id_/https://a.example/x").respond(
            200, html=BARE
        )
        report = await archive(tmp_path)
    assert report.none == 1 and "bookmark_card_archived" not in path.read_text()


async def test_archive_errors_leave_the_marker_and_list_the_url(tmp_path):
    path = post(tmp_path, "a", extra=GONE)
    before = path.read_text()
    with respx.mock as router:
        router.get(WAYBACK).respond(429)
        report = await archive(tmp_path)
    assert report.retry == 1 and report.retry_urls == ["https://a.example/x"]
    assert path.read_text() == before


async def test_archive_delay_is_between_every_wayback_request(tmp_path):
    post(tmp_path, "a", extra=GONE)
    sleeps = []
    with respx.mock as router:
        router.get(WAYBACK).respond(200, json=snapshot())
        router.get("https://web.archive.org/web/20210707120000id_/https://a.example/x").respond(
            200, html=PAGE
        )
        router.get(
            "https://web.archive.org/web/20210707120000id_/https://a.example/pic.jpg"
        ).respond(200, content=jpeg())
        await archive(tmp_path, sleeps=sleeps)
    assert sleeps == [4.0, 4.0]


async def test_archive_like(tmp_path):
    path = post(tmp_path, "a", kind="like", extra=GONE.replace("bookmark", "like"))
    with respx.mock as router:
        router.get(WAYBACK).respond(200, json=snapshot())
        router.get("https://web.archive.org/web/20210707120000id_/https://a.example/x").respond(
            200, html=TEXT_ONLY
        )
        await archive(tmp_path, kind="like", image=False)
    meta = front(path)
    assert "like_card" not in meta and meta["like_description"] == "Only text."
    assert meta["like_card_archived"].startswith("https://web.archive.org/web/2021")


# --- cli ---


def test_cli_prints_counts_and_retry_urls(tmp_path, capsys, monkeypatch):
    post(tmp_path, "a", "https://a.example/a")
    post(tmp_path, "b", "https://a.example/b")
    monkeypatch.setattr("micropub_server.auth.client.system_resolver", resolver)
    monkeypatch.setattr("micropub_server.backfill_cards.system_resolver", resolver)
    with respx.mock as router:
        router.get("https://a.example/a").respond(404)
        router.get("https://a.example/b").respond(503)
        code = main(
            ["backfill-cards", "--checkout", str(tmp_path), "--step", "live", "--delay", "0"]
        )
    out = capsys.readouterr().out
    assert code == 0
    assert "gone 1" in out and "retry 1" in out and "https://a.example/b" in out


def test_cli_defaults_delay_by_step(monkeypatch, tmp_path):
    seen = {}

    async def fake(root, card, **kw):
        seen.update(kw)
        from micropub_server.backfill_cards import CardReport

        return CardReport()

    monkeypatch.setattr("micropub_server.backfill_cards.backfill_live", fake)
    monkeypatch.setattr("micropub_server.backfill_cards.backfill_archive", fake)
    (tmp_path / "content/bookmarks").mkdir(parents=True)
    base = ["backfill-cards", "--checkout", str(tmp_path)]
    main([*base, "--step", "live"])
    assert seen["delay"] == 1.0
    main([*base, "--step", "archive"])
    assert seen["delay"] == 4.0
    main([*base, "--step", "live", "--delay", "0.5", "--limit", "3", "--since", "2014",
          "--before", "2026-10-14", "--recheck", "--no-image"])  # fmt: skip
    assert (seen["delay"], seen["limit"], seen["since"], seen["before"]) == (
        0.5,
        3,
        2014,
        "2026-10-14",
    )
    assert seen["recheck"] is True and seen["image"] is False
