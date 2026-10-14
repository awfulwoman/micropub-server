import pytest

from micropub_server.scopes import check_scope


@pytest.mark.parametrize(
    ("scope", "action", "expected"),
    [
        ("create", "create", True),
        ("post", "create", True),  # deprecated alias
        ("update", "create", False),
        ("create update", "update", True),
        ("update", "delete", False),
        ("delete", "delete", True),
        ("create", "undelete", True),  # undelete needs create
        ("draft", "create", "draft"),
        ("draft", "update", "draft"),
        ("draft", "delete", False),
        ("draft", "undelete", "draft"),
        ("create draft", "delete", False),
        ("", "create", False),
        (None, "create", False),
        ("media", "create", False),
    ],
)
def test_check_scope(scope, action, expected):
    assert check_scope(scope, action) == expected
