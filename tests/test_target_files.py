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
