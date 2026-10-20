"""USD-safe naming helpers.

USD prim and property name components must be identifiers
(``[A-Za-z_][A-Za-z0-9_]*``). IFC names are free text, so every name written to
USD goes through :func:`safe_identifier`. The original text is always kept in
``displayName`` metadata, so sanitizing never loses information.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_INVALID_IDENTIFIER_CHARS = re.compile(r"[^A-Za-z0-9_]+")
_COLLAPSED_UNDERSCORES = re.compile(r"_+")


def safe_identifier(value: Any, fallback: str = "Item") -> str:
    """Convert arbitrary text into a USD identifier.

    Accented characters are transliterated (``Tür`` -> ``Tur``); anything else
    outside ``[A-Za-z0-9_]`` becomes ``_``. Leading digits get a ``_`` prefix.
    """
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", errors="ignore").decode("ascii")
    text = _INVALID_IDENTIFIER_CHARS.sub("_", text.strip())
    text = _COLLAPSED_UNDERSCORES.sub("_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"_{text}"
    return text


def safe_attr_name(value: Any, fallback: str = "value") -> str:
    return safe_identifier(value, fallback=fallback)


def lower_first(name: str) -> str:
    """EXPRESS attribute name to USD property name (``GlobalId`` -> ``globalId``).

    The mapping is reversible because EXPRESS attribute names start with an
    upper-case letter.
    """
    if not name:
        return name
    return name[0].lower() + name[1:]


def entity_label(entity: Any) -> str:
    """Legacy ``<Name>_<GlobalId>`` label for an IFC entity."""
    entity_type = _safe_call(entity, "is_a", "IfcEntity")
    name = _safe_getattr(entity, "Name")
    global_id = _safe_getattr(entity, "GlobalId")
    step_id = _safe_call(entity, "id", None)

    label = safe_identifier(name or entity_type, fallback=entity_type)
    stable_id = safe_identifier(global_id or step_id, fallback="id")
    return f"{label}_{stable_id}"


def prim_base_name(
    *,
    ifc_class: str,
    name: str | None,
    global_id: str | None,
    step_id: int | None,
    mode: str = "name_guid",
) -> str:
    """Base prim name for an IFC entity according to the naming mode."""
    label = safe_identifier(name, fallback=ifc_class) if name else safe_identifier(ifc_class)
    stable = global_id if global_id else (str(step_id) if step_id is not None else None)
    if mode == "name":
        return label
    if mode == "guid":
        return safe_identifier(stable, fallback=label) if stable else label
    if stable is None:
        return label
    return safe_identifier(f"{label}_{stable}", fallback=label)


class UniqueNames:
    """Hands out sibling-unique names in a deterministic way."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def claim(self, base: str) -> str:
        name = base
        index = 2
        while name in self._used:
            name = f"{base}_{index}"
            index += 1
        self._used.add(name)
        return name

    def reserve(self, name: str) -> None:
        self._used.add(name)

    def __contains__(self, name: str) -> bool:
        return name in self._used


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
