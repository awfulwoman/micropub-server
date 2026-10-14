"""Convert between form-encoded, mf2 JSON and JF2 (spec 05)."""

from __future__ import annotations

from typing import Any

RESERVED = frozenset({"access_token", "h", "action", "url"})


def form_to_jf2(body: dict[str, Any]) -> dict[str, Any]:
    """Build JF2 from a form-encoded body (values already decoded)."""
    jf2: dict[str, Any] = {"type": body.get("h") or "entry"}
    for key, value in body.items():
        if key in RESERVED:
            continue
        if key.endswith("[]"):
            jf2[key[:-2]] = value if isinstance(value, list) else [value]
        else:
            jf2[key] = value
    return jf2


def mf2_to_jf2(item: dict[str, Any]) -> dict[str, Any]:
    types = item.get("type") or ["h-entry"]
    jf2: dict[str, Any] = {"type": types[0].removeprefix("h-")}
    for key, values in (item.get("properties") or {}).items():
        converted = [convert_value(key, value) for value in values]
        jf2[key] = converted[0] if len(converted) == 1 else converted
    return jf2


def convert_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        if "type" in value:
            return mf2_to_jf2(value)
        if key == "content" and ("html" in value or "value" in value):
            out = {}
            if "html" in value:
                out["html"] = value["html"]
            if "value" in value:
                out["text"] = value["value"]
            return out
        if "value" in value:
            out = {"url": value["value"]}
            if "alt" in value:
                out["alt"] = value["alt"]
            return out
    return value


def jf2_to_mf2(jf2: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in jf2.items():
        if key == "type":
            continue
        values = value if isinstance(value, list) else [value]
        props[key] = [_to_mf2_value(key, item) for item in values]
    return {"type": [f"h-{jf2.get('type', 'entry')}"], "properties": props}


def _to_mf2_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        if "type" in value:
            return jf2_to_mf2(value)
        if key == "content":
            out = {}
            if "html" in value:
                out["html"] = value["html"]
            if "text" in value:
                out["value"] = value["text"]
            return out
    return value
