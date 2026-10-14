from micropub_server.jf2 import form_to_jf2, jf2_to_mf2, mf2_to_jf2


def test_form_to_jf2_basic():
    jf2 = form_to_jf2({"h": "entry", "content": "Hello", "access_token": "x", "action": "create"})
    assert jf2 == {"type": "entry", "content": "Hello"}


def test_form_to_jf2_lists_and_brackets():
    jf2 = form_to_jf2({"category[]": ["a", "b"], "content": "x"})
    assert jf2["category"] == ["a", "b"]


def test_form_to_jf2_single_bracket_value_is_still_list():
    assert form_to_jf2({"category[]": ["a"]})["category"] == ["a"]


def test_form_to_jf2_defaults_type_entry():
    assert form_to_jf2({"content": "x"})["type"] == "entry"


def test_mf2_to_jf2_simple():
    jf2 = mf2_to_jf2(
        {"type": ["h-entry"], "properties": {"content": ["Hi"], "category": ["a", "b"]}}
    )
    assert jf2 == {"type": "entry", "content": "Hi", "category": ["a", "b"]}


def test_mf2_to_jf2_html_content():
    jf2 = mf2_to_jf2(
        {"type": ["h-entry"], "properties": {"content": [{"html": "<b>x</b>", "value": "x"}]}}
    )
    assert jf2["content"] == {"html": "<b>x</b>", "text": "x"}


def test_mf2_to_jf2_photo_with_alt():
    jf2 = mf2_to_jf2(
        {
            "type": ["h-entry"],
            "properties": {"photo": [{"value": "https://x/a.jpg", "alt": "A"}, "https://x/b.jpg"]},
        }
    )
    assert jf2["photo"] == [{"url": "https://x/a.jpg", "alt": "A"}, "https://x/b.jpg"]


def test_mf2_to_jf2_nested_card():
    jf2 = mf2_to_jf2(
        {
            "type": ["h-entry"],
            "properties": {
                "checkin": [
                    {
                        "type": ["h-card"],
                        "properties": {
                            "name": ["Cafe"],
                            "latitude": ["52.5"],
                            "longitude": ["13.4"],
                        },
                    }
                ]
            },
        }
    )
    assert jf2["checkin"] == {
        "type": "card",
        "name": "Cafe",
        "latitude": "52.5",
        "longitude": "13.4",
    }


def test_mf2_to_jf2_defaults_to_entry():
    assert mf2_to_jf2({"properties": {"content": ["x"]}})["type"] == "entry"


def test_jf2_to_mf2_roundtrip_content():
    mf2 = jf2_to_mf2(
        {"type": "entry", "content": {"html": "<b>x</b>", "text": "x"}, "category": ["a"]}
    )
    assert mf2["type"] == ["h-entry"]
    assert mf2["properties"]["category"] == ["a"]
    assert mf2["properties"]["content"] == [{"html": "<b>x</b>", "value": "x"}]


def test_jf2_to_mf2_nested():
    mf2 = jf2_to_mf2({"type": "entry", "checkin": {"type": "card", "name": "Cafe"}})
    assert mf2["properties"]["checkin"] == [{"type": ["h-card"], "properties": {"name": ["Cafe"]}}]
