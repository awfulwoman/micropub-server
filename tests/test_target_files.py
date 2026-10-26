from ruamel.yaml import YAML

from micropub_server.target_files import add_to_front_matter, kind_of_path

LIKE = """---
date: "2026-10-18T18:24:49+02:00"
like_of: https://adactio.com/journal/22802
tags:
  - x
---
"""
QUOTE = """---
quote_of: 'https://twitter.com/j/status/1'
date: '2021-06-10T13:05:35.020Z'
aliases:
  - /quotes/20210610t130535/
---
The words.

More words.
"""


def load(text):
    return YAML(typ="safe").load(text.split("---\n")[1])


def test_inserts_after_the_target_line_and_changes_nothing_else():
    out = add_to_front_matter(
        LIKE, "like", {"like-title": "The Tusks: a book", "like-author": "Jeremy Keith"}
    )
    assert out == LIKE.replace(
        "like_of: https://adactio.com/journal/22802\n",
        "like_of: https://adactio.com/journal/22802\n"
        "like_title: 'The Tusks: a book'\nlike_author: Jeremy Keith\n",
    )
    assert load(out)["like_title"] == "The Tusks: a book"


def test_body_and_other_keys_are_untouched():
    out = add_to_front_matter(QUOTE, "quote", {"quote-author": "jasmine", "quote-text": "a\n\nb"})
    assert out.endswith("---\nThe words.\n\nMore words.\n")
    front = load(out)
    assert front["quote_text"] == "a\n\nb"
    assert front["aliases"] == ["/quotes/20210610t130535/"]


def test_embed_markup_round_trips():
    html = '<blockquote class="twitter-tweet"><p>Hi &amp; bye</p></blockquote>'
    assert load(add_to_front_matter(QUOTE, "quote", {"quote-oembed": html}))["quote_oembed"] == html


def test_posts_that_already_have_a_value_are_left_alone():
    done = LIKE.replace("tags:", "like_title: Mine\ntags:")
    assert add_to_front_matter(done, "like", {"like-author": "A"}) is None


def test_nothing_to_add_or_no_front_matter_or_no_target_key():
    assert add_to_front_matter(LIKE, "like", {}) is None
    assert add_to_front_matter("just text", "like", {"like-title": "T"}) is None
    assert add_to_front_matter("---\ndate: x\n---\n", "like", {"like-title": "T"}) is None


def test_kind_comes_from_the_section():
    assert kind_of_path("content/likes/2026/10/18/x.md") == "like"
    assert kind_of_path("content/quotes/2021/06/10/x/index.md") == "quote"
    assert kind_of_path("content/bookmarks/2025/01/04/x.md") == "bookmark"
    assert kind_of_path("content/notes/2025/01/04/x.md") is None


def test_card_keys_go_after_bookmark_author_with_a_nested_image():
    from micropub_server.target_files import add_card_to_front_matter

    text = "---\nbookmark_of: https://x.org/\nbookmark_author: A\ntags:\n  - x\n---\nBody\n"
    card = {
        "bookmark_site": "X",
        "bookmark_image": {"file": "card.webp", "alt": "", "width": 800, "height": 419},
    }
    out = add_card_to_front_matter(text, card)
    assert out == (
        "---\nbookmark_of: https://x.org/\nbookmark_author: A\nbookmark_site: X\n"
        "bookmark_image:\n  file: card.webp\n  alt: ''\n  width: 800\n  height: 419\n"
        "tags:\n  - x\n---\nBody\n"
    )
    assert add_card_to_front_matter(out, card) is None


def test_like_card_keys_go_after_like_author():
    from micropub_server.target_files import add_card_to_front_matter

    text = "---\nlike_of: https://a.example/\nlike_title: T\nlike_author: A\nlike_text: x\n---\n"
    out = add_card_to_front_matter(text, {"like_site": "S", "like_description": "D"}, "like")
    assert out == (
        "---\nlike_of: https://a.example/\nlike_title: T\nlike_author: A\n"
        "like_site: S\nlike_description: D\nlike_text: x\n---\n"
    )
    assert add_card_to_front_matter(out, {"like_site": "S"}, "like") is None


MARKED = (
    "---\nbookmark_of: https://x.org/\nbookmark_author: A\n"
    "bookmark_card:\n  result: gone\n  checked: 2026-10-01\ntags:\n  - x\n---\nBody\n"
)
PLAIN = "---\nbookmark_of: https://x.org/\nbookmark_author: A\ntags:\n  - x\n---\nBody\n"


def test_marker_goes_after_the_target_keys_and_nothing_else_changes():
    from micropub_server.target_files import set_card_marker

    out = set_card_marker(PLAIN, "bookmark", "gone", "2026-10-01")
    assert out == MARKED


def test_marker_is_replaced_in_place_and_removed_cleanly():
    from micropub_server.target_files import set_card_marker

    assert set_card_marker(MARKED, "bookmark", "none", "2026-10-26") == MARKED.replace(
        "gone", "none"
    ).replace("2026-10-01", "2026-10-26")
    assert set_card_marker(MARKED, "bookmark", None) == PLAIN


def test_marker_needs_a_front_matter_and_a_target_line():
    from micropub_server.target_files import set_card_marker

    assert set_card_marker("no front matter", "bookmark", "gone", "2026-10-01") is None
    assert set_card_marker("---\ntags: x\n---\n", "bookmark", "gone", "2026-10-01") is None


def test_archived_url_follows_the_image_and_like_marker_uses_like_keys():
    from micropub_server.target_files import add_card_to_front_matter, set_card_marker

    card = {
        "bookmark_site": "X",
        "bookmark_card_archived": "https://web.archive.org/web/2014/https://x.org/",
    }
    out = add_card_to_front_matter(PLAIN, card)
    assert (
        "bookmark_site: X\nbookmark_card_archived: https://web.archive.org/web/2014/https://x.org/\ntags:"
        in out
    )
    like = "---\nlike_of: https://a.example/\ndate: 'x'\n---\n"
    assert set_card_marker(like, "like", "none", "2026-10-26") == (
        "---\nlike_of: https://a.example/\nlike_card:\n  result: none\n  checked: 2026-10-26\n"
        "date: 'x'\n---\n"
    )
