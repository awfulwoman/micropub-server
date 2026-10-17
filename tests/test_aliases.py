"""A post type can list extra URLs (Hugo aliases) that redirect to the post."""

from ruamel.yaml import YAML


def front(site, path):
    text = (site / path).read_text()
    return YAML(typ="safe").load(text.split("---\n")[1])


def test_article_gets_a_dated_alias(client, bearer, site, state):
    state.config.post_types["article"].post.aliases = ["/posts/{yyyy}/{MM}/{dd}/{slug}/"]
    r = client.post(
        "/micropub",
        json={"type": ["h-entry"], "properties": {"name": ["Hello there"], "content": ["x"]}},
        headers=bearer,
    )
    assert r.status_code == 202, r.text
    assert front(site, "content/posts/2026/hello-there/index.md")["aliases"] == [
        "/posts/2026/10/14/hello-there/"
    ]


def test_alias_follows_a_title_slug_change(client, bearer, site, state):
    state.config.post_types["article"].post.aliases = ["/posts/{yyyy}/{MM}/{dd}/{slug}/"]
    r = client.post(
        "/micropub",
        json={"type": ["h-entry"], "properties": {"name": ["Hello there"], "content": ["x"]}},
        headers=bearer,
    )
    url = r.headers["location"]
    r = client.post(
        "/micropub",
        json={"action": "update", "url": url, "replace": {"mp-slug": ["renamed"]}},
        headers=bearer,
    )
    assert r.status_code in (200, 201, 204), r.text
    assert front(site, "content/posts/2026/renamed/index.md")["aliases"] == [
        "/posts/2026/10/14/renamed/"
    ]
