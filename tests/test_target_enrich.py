import pytest
from ruamel.yaml import YAML

from micropub_server.target_enrich import backfill_targets
from micropub_server.target_meta import TargetMeta

from .conftest import sign_in

LIKE = "content/likes/2026/10/14/1910.md"
QUOTE = "content/quotes/2026/10/14/1910.md"
BOOKMARK = "content/bookmarks/2026/10/14/1910.md"
ADACTIO = "https://adactio.com/journal/22802"
TOOT = "https://masto.example/@ada/1"
META = TargetMeta(title="The real title", author="Jeremy Keith")
TOOT_META = TargetMeta(
    author="Ada", text="hello world", oembed='<iframe src="https://masto.example/e"></iframe>'
)


def fm(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1])


@pytest.fixture
def full(client):
    token = sign_in(client, scope="create update delete media")["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make(client, headers, **data):
    r = client.post("/micropub", data=data, headers=headers)
    assert r.status_code == 202, r.text
    return r.headers["location"]


def update(client, headers, url, **ops):
    return client.post("/micropub", json={"action": "update", "url": url, **ops}, headers=headers)


def test_a_like_gets_the_title_and_author_of_its_target(client, full, site, targets):
    targets.metas[ADACTIO] = META
    make(client, full, **{"like-of": ADACTIO})
    front = fm(site, LIKE)
    assert (front["like_title"], front["like_author"]) == ("The real title", "Jeremy Keith")
    assert "like_text" not in front and "like_oembed" not in front


def test_a_quote_gets_the_embed_and_the_original_text(client, full, site, targets):
    targets.metas[TOOT] = TOOT_META
    make(client, full, **{"quotation-of": TOOT, "content": "hello world"})
    front = fm(site, QUOTE)
    assert front["quote_author"] == "Ada"
    assert front["quote_text"] == "hello world"
    assert front["quote_oembed"].startswith("<iframe")


def test_a_bookmark_gets_an_author_but_keeps_its_own_title(client, full, site, targets):
    targets.metas[ADACTIO] = META
    make(client, full, **{"bookmark-of": ADACTIO, "name": "My title"})
    front = fm(site, BOOKMARK)
    assert front["title"] == "My title"
    assert front["bookmark_author"] == "Jeremy Keith"
    assert "bookmark_title" not in front


def test_a_failed_lookup_still_makes_the_post(client, full, site, targets):
    make(client, full, **{"like-of": ADACTIO})
    front = fm(site, LIKE)
    assert front["like_of"] == ADACTIO
    assert not [k for k in front if k.startswith("like_") and k != "like_of"]


def test_a_crashing_fetcher_never_blocks_posting(client, full, site, state):
    async def boom(url, resolver):
        raise RuntimeError("bug")

    state.target_fetcher = boom
    make(client, full, **{"like-of": ADACTIO})
    assert fm(site, LIKE)["like_of"] == ADACTIO


def test_what_the_client_sent_wins(client, full, site, targets):
    targets.metas[ADACTIO] = META
    make(client, full, **{"like-of": ADACTIO, "like-title": "Mine"})
    front = fm(site, LIKE)
    assert front["like_title"] == "Mine"
    assert front["like_author"] == "Jeremy Keith"  # only the gaps are filled


def test_can_be_switched_off(client, full, site, targets, state):
    state.config.publication.fetch_target_meta = False
    make(client, full, **{"like-of": ADACTIO})
    assert targets.calls == []


def test_unrelated_edits_do_not_fetch_again(client, full, site, targets):
    targets.metas[ADACTIO] = META
    url = make(client, full, **{"quotation-of": ADACTIO, "content": "q"})
    assert update(client, full, url, replace={"content": ["changed"]}).status_code == 200
    assert targets.calls == [ADACTIO]


def test_changing_the_target_replaces_what_was_fetched(client, full, site, targets):
    targets.metas[ADACTIO] = META
    targets.metas["https://two.example/b"] = TargetMeta(title="Two")
    url = make(client, full, **{"like-of": ADACTIO})
    update(client, full, url, replace={"like-of": ["https://two.example/b"]})
    front = fm(site, LIKE)
    assert front["like_title"] == "Two"
    assert "like_author" not in front  # the old author belonged to the old page


# --- backfill ------------------------------------------------------------------------------------
async def test_backfill_fills_posts_that_lack_metadata(client, full, site, targets, state):
    make(client, full, **{"like-of": ADACTIO})
    make(client, full, **{"bookmark-of": TOOT, "name": "b"})
    targets.metas.update({ADACTIO: META, TOOT: TOOT_META})
    report = await backfill_targets(state, delay=0)
    assert report.updated == 2
    assert fm(site, LIKE)["like_title"] == "The real title"
    assert fm(site, BOOKMARK)["bookmark_author"] == "Ada"


async def test_backfill_leaves_complete_posts_alone(client, full, site, targets, state):
    targets.metas[ADACTIO] = META
    make(client, full, **{"like-of": ADACTIO})
    targets.calls.clear()
    report = await backfill_targets(state, delay=0)
    assert (report.updated, report.skipped) == (0, 1)
    assert targets.calls == []


async def test_backfill_dry_run_changes_nothing(client, full, site, targets, state):
    make(client, full, **{"like-of": ADACTIO})
    targets.metas[ADACTIO] = META
    before = (site / LIKE).read_text()
    report = await backfill_targets(state, dry_run=True, delay=0)
    assert report.updated == 1
    assert report.changes == [
        (
            "https://awfulwoman.com/likes/2026/10/14/1910/",
            {"like-title": "The real title", "like-author": "Jeremy Keith"},
        )
    ]
    assert (site / LIKE).read_text() == before


async def test_backfill_reports_pages_that_gave_nothing(client, full, site, targets, state):
    make(client, full, **{"like-of": ADACTIO})
    report = await backfill_targets(state, delay=0)
    assert (report.updated, report.nothing_found) == (0, 1)
