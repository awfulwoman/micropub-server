from micropub_server.target_meta import (
    TargetMeta,
    apply_oembed,
    clean_oembed_html,
    extract_page,
    oembed_endpoint,
)

ENTRY = """<html><head><title>Ignored | Adactio</title></head><body>
<article class="h-entry"><h1 class="p-name">The real title</h1>
<a class="p-author h-card" href="https://adactio.com/"><span class="p-name">Jeremy Keith</span></a>
<div class="e-content"><p>Hello there.</p></div></article></body></html>"""

OG_ONLY = """<html><head><title>A page | Some Site</title>
<meta property="og:title" content="A page | Some Site">
<meta property="og:site_name" content="Some Site">
<meta name="author" content="Pat Example"></head><body><p>x</p></body></html>"""

NO_AUTHOR = """<html><head><title>Just a title</title>
<meta property="og:site_name" content="Some Site"></head><body></body></html>"""

JSON_LD = """<html><head><title>T</title>
<script type="application/ld+json">{"@type":"Article","author":{"@type":"Person","name":"Ada L"}}</script>
</head><body></body></html>"""

TWEET = (
    '<blockquote class="twitter-tweet"><p lang="en" dir="ltr">I cofounded a thing.<br><br>'
    'It is <a href="https://t.co/x">here</a></p>&mdash; jasmine (@jasminericegirl) '
    '<a href="https://twitter.com/jasminericegirl/status/1">June 9, 2021</a></blockquote>\n'
    '<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>\n'
)


def test_h_entry_gives_title_author_and_no_embed():
    meta = extract_page(ENTRY, "https://adactio.com/journal/1")
    assert meta.title == "The real title"
    assert meta.author == "Jeremy Keith"
    assert meta.oembed == "" and meta.text == ""  # text is only kept to back up an embed


def test_falls_back_to_og_title_without_the_site_suffix_and_meta_author():
    meta = extract_page(OG_ONLY, "https://x.example/p")
    assert meta.title == "A page"
    assert meta.author == "Pat Example"


def test_plain_title_when_nothing_else():
    assert extract_page(NO_AUTHOR, "https://x.example/").title == "Just a title"


def test_never_uses_the_site_name_as_the_author():
    assert extract_page(NO_AUTHOR, "https://x.example/").author == ""


def test_json_ld_author():
    assert extract_page(JSON_LD, "https://x.example/").author == "Ada L"


def test_garbage_html_gives_an_empty_result():
    assert extract_page("", "https://x.example/") == TargetMeta()


def test_discovers_the_oembed_endpoint_from_the_page():
    html = (
        '<head><link rel="alternate" type="application/json+oembed" '
        'href="https://masto.example/api/oembed?url=https%3A%2F%2Fmasto.example%2Fs%2F1"></head>'
    )
    assert oembed_endpoint(html, "https://masto.example/s/1") == (
        "https://masto.example/api/oembed?url=https%3A%2F%2Fmasto.example%2Fs%2F1"
    )


def test_twitter_and_x_use_the_publish_endpoint_without_discovery():
    for host in ("twitter.com", "x.com", "www.twitter.com"):
        endpoint = oembed_endpoint("", f"https://{host}/a/status/1")
        assert endpoint.startswith("https://publish.twitter.com/oembed?")
        assert "omit_script=1" in endpoint


def test_no_endpoint_when_the_page_offers_none():
    assert oembed_endpoint(ENTRY, "https://adactio.com/journal/1") is None


def test_tweet_oembed_gives_author_text_and_clean_html():
    meta = apply_oembed(TargetMeta(), {"author_name": "jasmine", "html": TWEET, "type": "rich"})
    assert meta.author == "jasmine"
    assert meta.text == "I cofounded a thing.\n\nIt is here"
    assert "<script" not in meta.oembed
    assert meta.oembed.startswith('<blockquote class="twitter-tweet">')
    assert 'href="https://twitter.com/jasminericegirl/status/1"' in meta.oembed


def test_oembed_does_not_override_what_the_page_already_gave():
    page = TargetMeta(title="T", author="Page Author", text="from page")
    meta = apply_oembed(page, {"author_name": "Embed Author", "html": TWEET})
    assert meta.author == "Page Author"
    assert meta.text == "from page"


def test_iframe_oembed_keeps_the_iframe_only_over_https():
    html = '<iframe src="https://masto.example/@a/1/embed" width="400" class="mastodon-embed" onload="x()"></iframe><script src="https://masto.example/embed.js"></script>'
    cleaned = clean_oembed_html(html)
    assert (
        cleaned
        == '<iframe class="mastodon-embed" src="https://masto.example/@a/1/embed" width="400"></iframe>'
    )


def test_oembed_html_drops_unsafe_content():
    assert clean_oembed_html('<iframe src="javascript:alert(1)"></iframe>') == ""
    assert clean_oembed_html('<iframe src="http://insecure.example/e"></iframe>') == ""
    assert clean_oembed_html('<p onclick="x()">hi</p>') == "<p>hi</p>"


def test_props_are_named_for_the_kind():
    meta = TargetMeta(title="T", author="A", text="body", oembed="<p>e</p>")
    assert meta.as_props("like") == {
        "like-title": "T",
        "like-author": "A",
        "like-text": "body",
        "like-oembed": "<p>e</p>",
    }
    assert TargetMeta(author="A").as_props("quote") == {"quote-author": "A"}
    assert TargetMeta(title="T", author="A").as_props("bookmark") == {"bookmark-author": "A"}


# --- fetching ------------------------------------------------------------------------------------
import pytest  # noqa: E402

from micropub_server.target_meta import fetch_target_meta  # noqa: E402

from .conftest import public_resolver  # noqa: E402

MASTO_PAGE = """<html><head><title>Ada: "hello world" - Masto</title>
<link rel="alternate" type="application/json+oembed" href="https://masto.example/api/oembed?url=https%3A%2F%2Fmasto.example%2F%40ada%2F1">
</head><body><div class="h-entry"><a class="p-author h-card" href="https://masto.example/@ada"><span class="p-name">Ada</span></a>
<div class="e-content"><p>hello world</p></div></div></body></html>"""


async def test_mastodon_post_gets_author_embed_and_original_text(no_network):
    no_network.get("https://masto.example/@ada/1").respond(
        200, headers={"content-type": "text/html"}, text=MASTO_PAGE
    )
    no_network.get("https://masto.example/api/oembed").respond(
        200,
        json={
            "type": "rich",
            "author_name": "Ada",
            "html": '<iframe src="https://masto.example/@ada/1/embed" class="mastodon-embed" width="400"></iframe><script src="https://masto.example/embed.js"></script>',
        },
    )
    meta = await fetch_target_meta("https://masto.example/@ada/1", public_resolver)
    assert meta.author == "Ada"
    assert meta.title == ""  # a post has no title of its own
    assert meta.text == "hello world"
    assert "<iframe" in meta.oembed and "<script" not in meta.oembed


async def test_tweet_uses_only_the_oembed_endpoint(no_network):
    route = no_network.get(url__startswith="https://publish.twitter.com/oembed").respond(
        200, json={"author_name": "jasmine", "html": TWEET, "type": "rich"}
    )
    meta = await fetch_target_meta("https://twitter.com/j/status/1", public_resolver)
    assert route.call_count == 1  # twitter.com itself is never fetched
    assert meta.author == "jasmine"
    assert meta.text.startswith("I cofounded")


async def test_a_failing_embed_keeps_what_the_page_gave(no_network):
    no_network.get("https://adactio.com/journal/1").respond(
        200,
        headers={"content-type": "text/html"},
        text=ENTRY.replace(
            "<head>",
            '<head><link rel="alternate" type="application/json+oembed" href="https://adactio.com/oembed">',
        ),
    )
    no_network.get("https://adactio.com/oembed").respond(500)
    meta = await fetch_target_meta("https://adactio.com/journal/1", public_resolver)
    assert (meta.title, meta.author, meta.oembed) == ("The real title", "Jeremy Keith", "")


@pytest.mark.parametrize("status", [404, 500])
async def test_dead_pages_give_an_empty_result(no_network, status):
    no_network.get("https://gone.example/").respond(status)
    assert await fetch_target_meta("https://gone.example/", public_resolver) == TargetMeta()


async def test_private_addresses_are_never_fetched():
    assert await fetch_target_meta("http://127.0.0.1/admin") == TargetMeta()
    assert await fetch_target_meta("http://192.168.1.1/") == TargetMeta()


async def test_a_redirect_into_a_private_address_is_refused(no_network):
    no_network.get("https://redir.example/").respond(
        302, headers={"location": "http://127.0.0.1/secret"}
    )
    assert await fetch_target_meta("https://redir.example/", public_resolver) == TargetMeta()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("262588213843476", ""),
        ("https://coderwall.com/jaymabazza", ""),
        ("admin", ""),
        ("admin_riojaweb", ""),
        ("By: Editor", ""),
        ("By Jeremy Keith", "Jeremy Keith"),
        ("by: Ada L", "Ada L"),
        ("Guardian Staff", "Guardian Staff"),
        ("@mrmrs", "@mrmrs"),
    ],
)
def test_author_values_that_are_not_names_are_dropped(raw, expected):
    html = f'<html><head><title>T</title><meta name="author" content="{raw}"></head></html>'
    assert extract_page(html, "https://x.example/").author == expected


def test_a_placeholder_author_does_not_hide_a_real_one_further_down():
    html = JSON_LD.replace(
        "<title>T</title>", '<title>T</title><meta name="author" content="admin">'
    )
    assert extract_page(html, "https://x.example/").author == "Ada L"
