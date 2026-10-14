import pytest

from micropub_server.util import canonical_url, to_list


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://awfulwoman.com", "https://awfulwoman.com/"),
        ("HTTPS://AwfulWoman.com/", "https://awfulwoman.com/"),
        ("https://awfulwoman.com:443/", "https://awfulwoman.com/"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a/", "https://example.com:8443/a/"),
        ("https://example.com/a?x=1", "https://example.com/a?x=1"),
    ],
)
def test_canonical_url(given, expected):
    assert canonical_url(given) == expected


def test_to_list():
    assert to_list(None) == []
    assert to_list("a") == ["a"]
    assert to_list(["a", "b"]) == ["a", "b"]
