import pytest

from micropub_server.update import UpdateError, apply_update


def test_replace_scalar_and_list():
    out = apply_update(
        {"name": "Old", "category": ["a"]}, replace={"name": ["New"], "category": ["x", "y"]}
    )
    assert out == {"name": "New", "category": ["x", "y"]}


def test_replace_with_empty_list_is_ignored():
    assert apply_update({"name": "Keep"}, replace={"name": []}) == {"name": "Keep"}


def test_replace_creates_missing_property():
    assert apply_update({}, replace={"summary": ["s"]}) == {"summary": "s"}


def test_replace_html_content():
    out = apply_update(
        {"content": {"text": "old", "html": "<p>old</p>"}},
        replace={"content": [{"html": "<p>new</p>"}]},
    )
    assert out["content"] == {"html": "<p>new</p>"}


def test_replace_with_nested_card():
    out = apply_update(
        {}, replace={"checkin": [{"type": ["h-card"], "properties": {"name": ["Cafe"]}}]}
    )
    assert out["checkin"] == {"type": "card", "name": "Cafe"}


def test_add_to_existing_scalar_and_list_and_missing():
    out = apply_update(
        {"category": ["a"], "name": "n"},
        add={"category": ["b"], "name": ["m"], "syndication": ["https://x"]},
    )
    assert out["category"] == ["a", "b"]
    assert out["name"] == ["n", "m"]
    assert out["syndication"] == "https://x"


def test_add_applies_every_key():
    # Indiekit returns inside its loop and only ever applies the first key
    out = apply_update({}, add={"category": ["a"], "syndication": ["https://x", "https://y"]})
    assert out["category"] == "a" and out["syndication"] == ["https://x", "https://y"]


def test_delete_whole_properties_with_a_list():
    out = apply_update(
        {"category": ["a"], "summary": "s", "name": "n"}, delete=["category", "summary"]
    )
    assert out == {"name": "n"}


def test_delete_specific_values_with_an_object():
    out = apply_update({"category": ["a", "b", "c"]}, delete={"category": ["b"]})
    assert out == {"category": ["a", "c"]}


def test_delete_last_value_removes_the_property():
    assert apply_update({"category": ["a"], "name": "n"}, delete={"category": ["a"]}) == {
        "name": "n"
    }


def test_delete_value_from_scalar():
    assert apply_update({"name": "n", "x": 1}, delete={"name": ["n"]}) == {"x": 1}


def test_delete_missing_value_or_property_is_a_noop():
    assert apply_update({"category": ["a"]}, delete={"category": ["zzz"], "nope": ["x"]}) == {
        "category": ["a"]
    }
    assert apply_update({"a": 1}, delete=["nope"]) == {"a": 1}


def test_operations_apply_in_order_add_replace_delete():
    out = apply_update(
        {"category": ["a"]},
        add={"category": ["b"]},
        replace={"name": ["N"]},
        delete={"category": ["a"]},
    )
    assert out == {"category": "b", "name": "N"} or out == {"category": ["b"], "name": "N"}


def test_does_not_mutate_the_input():
    original = {"category": ["a"]}
    apply_update(original, add={"category": ["b"]}, delete=["category"])
    assert original == {"category": ["a"]}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"replace": {"name": "not a list"}},
        {"add": {"name": "not a list"}},
        {"delete": {"name": "not a list"}},
        {"replace": ["not", "an", "object"]},
        {"delete": "string"},
    ],
)
def test_malformed_operations_are_rejected(kwargs):
    with pytest.raises(UpdateError):
        apply_update({"name": "n"}, **kwargs)


def test_protected_properties_cannot_be_changed():
    for key in ("uid", "url", "post-type", "type"):
        out = apply_update({key: "orig"}, replace={key: ["hacked"]}, delete=[key])
        assert out[key] == "orig"
