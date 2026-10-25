from ruamel.yaml import YAML

from micropub_server.topics import (
    add_topics,
    load_topic_map,
    parse_suggestion,
    tags_of,
    topics_for_tags,
)

MAP = load_topic_map(
    {
        "web": {"title": "Web development", "tags": ["web", "html", "CSS"]},
        "tech": {"title": "Technology", "tags": ["tech", "apple"]},
        "lgbtq": {"title": "LGBTQ", "tags": ["trans", "pride"]},
        "life": {"title": "Life", "tags": ["berlin", "home"]},
    }
)

POST = """---
title: A post
tags:
  - Trans
  - html
date: '2021-02-10T09:00:00Z'
---
Body text.
"""


def load(text):
    return YAML(typ="safe").load(text.split("---\n")[1])


def test_map_keys_keep_their_order_and_tags_are_lowercased():
    assert list(MAP) == ["web", "tech", "lgbtq", "life"]
    assert MAP["web"] == ["web", "html", "css"]


def test_map_skips_broken_entries():
    assert load_topic_map({"ok": {"tags": ["a"]}, "bad": "nope", "none": {"title": "x"}}) == {
        "ok": ["a"],
        "none": [],
    }
    assert load_topic_map(None) == {}


def test_topics_for_tags_ignores_case_and_unknown_tags():
    assert topics_for_tags(["CSS", "nothing"], MAP) == ["web"]
    assert topics_for_tags([], MAP) == []


def test_at_most_three_topics_most_matching_tags_first_then_map_order():
    tags = ["home", "trans", "pride", "apple", "web"]
    assert topics_for_tags(tags, MAP) == ["lgbtq", "web", "tech"]


def test_tags_of_reads_the_front_matter():
    assert tags_of(POST) == ["Trans", "html"]
    assert tags_of("---\ntags: solo\n---\n") == ["solo"]
    assert tags_of("no front matter") == []


def test_add_topics_goes_after_the_tags_and_changes_nothing_else():
    out = add_topics(POST, ["lgbtq", "web"])
    assert out == POST.replace("  - html\n", "  - html\ntopics:\n  - lgbtq\n  - web\n")
    assert load(out)["topics"] == ["lgbtq", "web"]


def test_add_topics_without_tags_goes_at_the_end_of_the_front_matter():
    text = "---\ntitle: x\n---\nBody\n"
    assert add_topics(text, ["life"]) == "---\ntitle: x\ntopics:\n  - life\n---\nBody\n"


def test_add_topics_never_overwrites_and_needs_front_matter():
    assert add_topics("---\ntopics: [web]\n---\n", ["life"]) is None
    assert add_topics(POST, []) is None
    assert add_topics("no front matter", ["web"]) is None


def test_parse_suggestion_reads_a_json_list_and_keeps_known_topics_only():
    assert parse_suggestion('["lgbtq", "web"]', MAP) == ["lgbtq", "web"]
    assert parse_suggestion('Sure! ["Life", "nope", "life"]', MAP) == ["life"]


def test_parse_suggestion_caps_at_three_and_accepts_none():
    assert parse_suggestion('["web", "tech", "lgbtq", "life"]', MAP) == ["web", "tech", "lgbtq"]
    assert parse_suggestion("[]", MAP) == []
    assert parse_suggestion("no idea", MAP) == []
