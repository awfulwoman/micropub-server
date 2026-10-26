import pytest
from ruamel.yaml import YAML

from micropub_server.card_image import CardImage
from micropub_server.target_enrich import backfill_targets
from micropub_server.target_meta import TargetMeta

from .conftest import sign_in

LIKE = "content/likes/2026/10/14/1910/index.md"
QUOTE = "content/quotes/2026/10/14/1910.md"
BOOKMARK = "content/bookmarks/2026/10/14/1910/index.md"
CARD = "content/bookmarks/2026/10/14/1910/card.webp"
BOOKMARK_URL = "https://awfulwoman.com/bookmarks/2026/10/14/1910/"
IMG = "https://adactio.com/i/card.jpg"
ADACTIO = "https://adactio.com/journal/22802"
TOOT = "https://masto.example/@ada/1"
META = TargetMeta(title="The real title", author="Jeremy Keith")
TOOT_META = TargetMeta(
    author="Ada", text="hello world", oembed='<iframe src="https://masto.example/e"></iframe>'
)
CARD_META = TargetMeta(
    author="Jeremy Keith",
    site="Adactio",
    description="A journal entry.",
    image_url=IMG,
    image_alt="A photo",
)
WEBP = CardImage(b"RIFF-webp-bytes", 800, 419)
WEBP_2 = CardImage(b"RIFF-other-bytes", 640, 360)


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


# --- link cards (spec 16) ------------------------------------------------------------------------
def bookmark(client, full, **extra):
    return make(client, full, **{"bookmark-of": ADACTIO, "name": "b", **extra})


def media_rows(state):
    return state.db.query("SELECT * FROM media ORDER BY path")


def test_a_bookmark_gets_its_card_in_one_commit(
    client, full, site, targets, images, state, monkeypatch
):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append(([c.path for c in changes], message))
        return await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    bookmark(client, full)
    front = fm(site, BOOKMARK)
    assert (front["bookmark_site"], front["bookmark_description"]) == (
        "Adactio",
        "A journal entry.",
    )
    assert front["bookmark_image"] == {
        "file": "card.webp",
        "alt": "A photo",
        "width": 800,
        "height": 419,
    }
    assert (site / CARD).read_bytes() == WEBP.content
    assert commits == [([BOOKMARK, CARD], "create bookmark post")]
    assert images.calls == [IMG]


def test_the_card_image_has_a_media_row_linked_to_the_post(client, full, targets, images, state):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    bookmark(client, full)
    (row,) = media_rows(state)
    post = state.db.query("SELECT uid FROM posts")[0]["uid"]
    assert (row["path"], row["url"], row["post_uid"]) == (CARD, BOOKMARK_URL + "card.webp", post)
    assert (row["media_type"], row["content_type"]) == ("photo", "image/webp")


def test_the_image_fetch_gets_what_is_left_of_the_budget(client, full, targets, images):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    bookmark(client, full)
    assert 0 < images.timeouts[0] <= 12


def test_a_page_without_an_image_gives_a_text_card(client, full, site, targets, images, state):
    targets.metas[ADACTIO] = TargetMeta(site="Adactio", description="d")
    bookmark(client, full)
    front = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Adactio" and "bookmark_image" not in front
    assert images.calls == [] and media_rows(state) == []


def test_an_image_that_cannot_be_used_gives_a_text_card(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META  # the fake has no image for IMG
    bookmark(client, full)
    front = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Adactio" and "bookmark_image" not in front
    assert not (site / CARD).exists()


def test_a_crashing_image_fetcher_never_blocks_posting(client, full, site, targets, state):
    async def boom(*args, **kwargs):
        raise RuntimeError("bug")

    targets.metas[ADACTIO] = CARD_META
    state.image_fetcher = boom
    bookmark(client, full)
    front = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Adactio" and "bookmark_image" not in front


def test_images_can_be_switched_off_alone(client, full, site, targets, images, state):
    state.config.publication.fetch_target_image = False
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    bookmark(client, full)
    front = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Adactio" and "bookmark_image" not in front
    assert images.calls == [] and not (site / CARD).exists()


def test_no_target_meta_means_no_card_at_all(client, full, site, targets, images, state):
    state.config.publication.fetch_target_meta = False
    bookmark(client, full)
    assert targets.calls == [] and images.calls == []


def test_a_flat_bookmark_gets_text_only(client, full, site, targets, images, state):
    state.config.post_types["bookmark"].post.path = "content/bookmarks/2026/10/14/1910.md"
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    bookmark(client, full)
    front = fm(site, "content/bookmarks/2026/10/14/1910.md")
    assert front["bookmark_site"] == "Adactio" and "bookmark_image" not in front
    assert not list(site.rglob("card.webp"))


def test_quotes_get_no_card_or_image(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    make(client, full, **{"quotation-of": ADACTIO})
    assert "quote_site" not in fm(site, QUOTE)
    assert images.calls == []


def test_an_edit_that_leaves_the_link_keeps_the_card(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    url = bookmark(client, full)
    assert update(client, full, url, replace={"content": ["hi"]}).status_code == 200
    assert fm(site, BOOKMARK)["bookmark_image"]["width"] == 800
    assert (site / CARD).read_bytes() == WEBP.content
    assert targets.calls == [ADACTIO] and images.calls == [IMG]


def test_changing_the_link_replaces_the_card_in_one_commit(
    client, full, site, targets, images, state, monkeypatch
):
    targets.metas[ADACTIO] = CARD_META
    targets.metas["https://two.example/b"] = TargetMeta(
        site="Two", image_url="https://two.example/i.png"
    )
    images.cards[IMG] = WEBP
    images.cards["https://two.example/i.png"] = WEBP_2
    url = bookmark(client, full)
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append(len(changes))
        return await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    update(client, full, url, replace={"bookmark-of": ["https://two.example/b"]})
    front = fm(site, BOOKMARK)
    assert front["bookmark_site"] == "Two" and "bookmark_description" not in front
    assert front["bookmark_image"] == {"file": "card.webp", "alt": "", "width": 640, "height": 360}
    assert (site / CARD).read_bytes() == WEBP_2.content
    assert commits == [2]
    (row,) = media_rows(state)
    assert row["md5"] != ""


def test_changing_the_link_to_a_page_with_no_image_removes_the_old_one(
    client, full, site, targets, images, state
):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    url = bookmark(client, full)
    update(client, full, url, replace={"bookmark-of": ["https://two.example/b"]})
    front = fm(site, BOOKMARK)
    assert "bookmark_image" not in front and "bookmark_site" not in front
    assert not (site / CARD).exists()
    assert media_rows(state) == []


def test_changing_the_link_drops_the_card_marker_too(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META
    url = bookmark(client, full)
    update(client, full, url, replace={"bookmark-card": [{"result": "none"}], "content": ["x"]})
    assert fm(site, BOOKMARK)["bookmark_card"] == {"result": "none"}
    update(client, full, url, replace={"bookmark-of": ["https://two.example/b"]})
    assert "bookmark_card" not in fm(site, BOOKMARK)


def test_deleting_bookmark_image_removes_the_file_and_its_row(
    client, full, site, targets, images, state
):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    url = bookmark(client, full)
    assert update(client, full, url, delete=["bookmark-image"]).status_code == 200
    front = fm(site, BOOKMARK)
    assert "bookmark_image" not in front and front["bookmark_site"] == "Adactio"
    assert not (site / CARD).exists()
    assert (site / BOOKMARK).exists()
    assert media_rows(state) == []
    assert targets.calls == [ADACTIO]  # nothing fetched again


def test_moving_the_post_takes_the_card_along(client, full, site, targets, images, state):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    url = bookmark(client, full)
    update(client, full, url, replace={"published": ["2026-10-15T10:00:00+02:00"]})
    new = site / "content/bookmarks/2026/10/15/1000/card.webp"
    assert new.read_bytes() == WEBP.content
    assert not (site / CARD).exists()


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


async def test_backfill_can_be_limited_to_one_type(client, full, site, targets, state):
    make(client, full, **{"like-of": ADACTIO})
    make(client, full, **{"bookmark-of": TOOT, "name": "b"})
    targets.metas.update({ADACTIO: META, TOOT: TOOT_META})
    report = await backfill_targets(state, delay=0, only_type="like")
    assert report.updated == 1
    assert "bookmark_author" not in fm(site, BOOKMARK)


def test_card_keys_sit_next_to_bookmark_of(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    bookmark(client, full, content="words")
    keys = list(fm(site, BOOKMARK))
    wanted = [
        "bookmark_of",
        "bookmark_author",
        "bookmark_site",
        "bookmark_description",
        "bookmark_image",
    ]
    start = keys.index("bookmark_of")
    assert keys[start:] == wanted


# --- link cards for likes (spec 16, Likes) -----------------------------------------------------
LIKE_CARD = "content/likes/2026/10/14/1910/card.webp"


def like(client, full, **extra):
    return make(client, full, **{"like-of": ADACTIO, **extra})


def test_a_like_gets_its_card_in_one_commit(
    client, full, site, targets, images, state, monkeypatch
):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    commits = []
    original = state.store.commit

    async def spy(changes, message):
        commits.append(([c.path for c in changes], message))
        return await original(changes, message)

    monkeypatch.setattr(state.store, "commit", spy)
    like(client, full)
    front = fm(site, LIKE)
    assert (front["like_site"], front["like_description"]) == ("Adactio", "A journal entry.")
    assert front["like_image"] == {
        "file": "card.webp",
        "alt": "A photo",
        "width": 800,
        "height": 419,
    }
    assert (site / LIKE_CARD).read_bytes() == WEBP.content
    assert commits == [([LIKE, LIKE_CARD], "create like post")]
    (row,) = media_rows(state)
    assert row["path"] == LIKE_CARD and row["post_uid"]


def test_a_like_with_an_embed_gets_no_card(client, full, site, targets, images, state):
    targets.metas[TOOT] = TargetMeta(
        author="Ada",
        text="hello",
        oembed="<blockquote>hi</blockquote>",
        site="Mastodon",
        description="hello",
        image_url=IMG,
    )
    images.cards[IMG] = WEBP
    make(client, full, **{"like-of": TOOT})
    front = fm(site, LIKE)
    assert front["like_oembed"] and front["like_author"] == "Ada"
    assert not {"like_site", "like_description", "like_image"} & set(front)
    assert images.calls == [] and media_rows(state) == []


def test_like_card_keys_follow_like_author(client, full, site, targets, images):
    targets.metas[ADACTIO] = TargetMeta(
        title="T", author="Jeremy", site="S", description="D", image_url=IMG
    )
    images.cards[IMG] = WEBP
    like(client, full)
    keys = list(fm(site, LIKE))
    start = keys.index("like_of")
    assert keys[start : start + 6] == [
        "like_of", "like_title", "like_author", "like_site", "like_description", "like_image",
    ]  # fmt: skip


def test_deleting_like_image_removes_the_file(client, full, site, targets, images, state):
    targets.metas[ADACTIO] = CARD_META
    images.cards[IMG] = WEBP
    url = like(client, full)
    assert update(client, full, url, delete=["like-image"]).status_code == 200
    front = fm(site, LIKE)
    assert "like_image" not in front and front["like_site"] == "Adactio"
    assert not (site / LIKE_CARD).exists() and media_rows(state) == []


def test_changing_the_like_replaces_the_card(client, full, site, targets, images):
    targets.metas[ADACTIO] = CARD_META
    targets.metas["https://two.example/b"] = TargetMeta(site="Two", description="Second")
    images.cards[IMG] = WEBP
    url = like(client, full)
    update(client, full, url, replace={"like-of": ["https://two.example/b"]})
    front = fm(site, LIKE)
    assert front["like_site"] == "Two" and "like_image" not in front
    assert not (site / LIKE_CARD).exists()
