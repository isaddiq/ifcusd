"""Conversion of IFC attribute values into USD-typed values.

The mapping is fixed and documented (see docs/schema-mapping.md):

=====================================  =====================
IFC value                              USD attribute type
=====================================  =====================
BOOLEAN                                ``bool``
LOGICAL (TRUE/FALSE/UNKNOWN)           ``bool`` or ``token``
INTEGER                                ``int`` (``int64`` if it overflows)
REAL / NUMBER / measures               ``double``
STRING / labels / identifiers          ``string``
ENUMERATION                            ``token`` (+ ``allowedTokens``)
homogeneous LIST/SET of the above      the matching array type
nested or mixed aggregates, entities   ``string`` holding canonical JSON
=====================================  =====================

Defined types (``IfcLengthMeasure(3.0)``) are unwrapped; the defined type name
is reported separately so it can be stored as attribute metadata.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ifcusd.ifc import util

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


@dataclass(frozen=True)
class UsdValue:
    """A value ready to be authored on a USD attribute."""

    type_name: str
    value: Any
    value_type: str | None = None
    encoding: str | None = None  # "json" when ``value`` is a JSON document


def unwrap(value: Any) -> tuple[Any, str | None]:
    """Strip IFC defined-type wrappers, returning ``(python_value, defined_type)``."""
    defined_type = None
    depth = 0
    while (
        value is not None
        and not isinstance(value, (str, int, float, bool, list, tuple))
        and not util.is_entity(value)
        and depth < 8
    ):
        try:
            defined_type = defined_type or value.is_a()
            value = value.wrappedValue
        except Exception:
            break
        depth += 1
    return value, defined_type


def to_usd_value(value: Any, *, enum: bool = False) -> UsdValue | None:
    """Map a plain or wrapped IFC value to a :class:`UsdValue`.

    Returns ``None`` for ``None`` and for entity references (callers turn those
    into USD relationships or JSON).
    """
    if value is None or util.is_entity(value):
        return None
    raw, defined_type = unwrap(value)
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return _aggregate(raw, defined_type, enum=enum)
    scalar = _scalar(raw, enum=enum)
    if scalar is None:
        return None
    type_name, converted = scalar
    return UsdValue(type_name, converted, defined_type)


def _scalar(value: Any, *, enum: bool = False) -> tuple[str, Any] | None:
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, int):
        if INT32_MIN <= value <= INT32_MAX:
            return "int", value
        return "int64", value
    if isinstance(value, float):
        return "double", value
    if isinstance(value, str):
        if value == "UNKNOWN" and not enum:
            return "token", value
        return ("token" if enum else "string"), value
    return None


def _aggregate(values: Any, defined_type: str | None, *, enum: bool) -> UsdValue | None:
    items = []
    item_types = set()
    for item in values:
        if util.is_entity(item):
            return UsdValue("string", to_json(jsonable(values)), defined_type, "json")
        raw, item_type = unwrap(item)
        if isinstance(raw, (list, tuple)) or raw is None:
            return UsdValue("string", to_json(jsonable(values)), defined_type, "json")
        items.append(raw)
        if item_type:
            item_types.add(item_type)
    common_type = defined_type or (item_types.pop() if len(item_types) == 1 else None)
    if not items:
        return None
    if all(isinstance(item, bool) for item in items):
        return UsdValue("bool[]", list(items), common_type)
    if all(isinstance(item, int) and not isinstance(item, bool) for item in items):
        array_type = "int[]" if all(INT32_MIN <= item <= INT32_MAX for item in items) else "int64[]"
        return UsdValue(array_type, list(items), common_type)
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in items):
        return UsdValue("double[]", [float(item) for item in items], common_type)
    if all(isinstance(item, str) for item in items):
        return UsdValue("token[]" if enum else "string[]", list(items), common_type)
    return UsdValue("string", to_json(jsonable(values)), common_type, "json")


def jsonable(value: Any, depth: int = 3, _seen: frozenset[int] = frozenset()) -> Any:
    """Convert an IFC value (including entity graphs) into JSON-compatible data.

    Entities become ``{"type": ..., "id": ..., <attributes>}``; recursion stops
    at ``depth`` (deeper entities are reduced to ``{"type", "id"}``) and on
    cycles.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(item, depth, _seen) for item in value]
    if util.is_entity(value):
        identifier = util.step_id(value)
        reference: dict[str, Any] = {"type": util.ifc_class(value)}
        if identifier is not None:
            reference["id"] = identifier
        if depth <= 0 or (identifier is not None and identifier in _seen):
            return reference
        seen = _seen | ({identifier} if identifier is not None else frozenset())
        for name, _kind, attribute_value in util.explicit_attributes(value):
            if attribute_value is None:
                continue
            reference[name] = jsonable(attribute_value, depth - 1, seen)
        return reference
    raw, _defined = unwrap(value)
    if raw is not value:
        return jsonable(raw, depth, _seen)
    return str(value)


def to_json(data: Any) -> str:
    """Canonical (sorted, compact) JSON so output is byte-for-byte reproducible."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
