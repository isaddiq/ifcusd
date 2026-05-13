from __future__ import annotations

import re
from typing import Any

_INVALID_IDENTIFIER_CHARS = re.compile(r"[^A-Za-z0-9_]+")
_COLLAPSED_UNDERSCORES = re.compile(r"_+")


def safe_identifier(value: Any, fallback: str = "Item") -> str:
    text = "" if value is None else str(value)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    text = _INVALID_IDENTIFIER_CHARS.sub("_", text.strip())
    text = _COLLAPSED_UNDERSCORES.sub("_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"_{text}"
    return text


def safe_attr_name(value: Any, fallback: str = "value") -> str:
    return safe_identifier(value, fallback=fallback)


def entity_label(entity: Any) -> str:
    entity_type = _safe_call(entity, "is_a", "IfcEntity")
    name = _safe_getattr(entity, "Name")
    global_id = _safe_getattr(entity, "GlobalId")
    step_id = _safe_call(entity, "id", None)

    label = safe_identifier(name or entity_type, fallback=entity_type)
    stable_id = safe_identifier(global_id or step_id, fallback="id")
    return f"{label}_{stable_id}"


def _safe_getattr(entity: Any, name: str) -> Any:
    try:
        return getattr(entity, name)
    except Exception:
        return None


def _safe_call(entity: Any, name: str, default: Any) -> Any:
    try:
        method = getattr(entity, name)
    except Exception:
        return default
    try:
        return method()
    except Exception:
        return default
