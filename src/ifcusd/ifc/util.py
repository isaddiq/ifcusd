"""Schema-agnostic helpers for reading IfcOpenShell entities.

Attribute and inverse names differ between IFC2X3, IFC4 and IFC4X3, and
IfcOpenShell raises when an attribute does not exist in the file's schema. The
helpers here return ``None``/empty values instead, so callers can probe for
schema-specific data without branching on the schema version.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache, lru_cache
from typing import Any

import ifcopenshell
import ifcopenshell.util.attribute


def attr(entity: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(entity, name)
    except Exception:
        return default
    return default if value is None else value


def inverse(entity: Any, name: str) -> tuple[Any, ...]:
    """Inverse attribute as a tuple (empty when absent in this schema)."""
    try:
        value = getattr(entity, name)
    except Exception:
        return ()
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def is_a(entity: Any, ifc_class: str) -> bool:
    try:
        return bool(entity.is_a(ifc_class))
    except Exception:
        return False


def is_any(entity: Any, classes: tuple[str, ...]) -> bool:
    return any(is_a(entity, name) for name in classes)


def step_id(entity: Any) -> int | None:
    try:
        value = int(entity.id())
    except Exception:
        return None
    return value if value > 0 else None


def ifc_class(entity: Any) -> str:
    try:
        return str(entity.is_a())
    except Exception:
        return type(entity).__name__


def is_entity(value: Any) -> bool:
    """True for entity instances (not defined-type wrappers such as ``IfcLabel``)."""
    if not isinstance(value, ifcopenshell.entity_instance):
        return False
    try:
        return bool(value.is_entity())
    except Exception:
        try:
            return int(value.id()) > 0
        except Exception:
            return False


def by_type(model: Any, ifc_class_name: str, include_subtypes: bool = True) -> list[Any]:
    """``model.by_type`` that returns ``[]`` for classes absent from the schema."""
    try:
        return list(model.by_type(ifc_class_name, include_subtypes))
    except Exception:
        return []


def schema_identifier(model: Any) -> str:
    return str(getattr(model, "schema_identifier", None) or getattr(model, "schema", "") or "")


def entity_schema(entity: Any) -> str:
    try:
        return str(entity.is_a(True)).split(".")[0]
    except Exception:
        return ""


@cache
def _schema(schema_name: str) -> Any:
    return ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema_name)


@lru_cache(maxsize=8192)
def attribute_layout(schema_name: str, entity_class: str) -> tuple[tuple[str, str, bool], ...]:
    """``(name, primitive_kind, is_derived)`` for every attribute of a class."""
    declaration = _schema(schema_name).declaration_by_name(entity_class).as_entity()
    derived = declaration.derived()
    layout = []
    for index, attribute in enumerate(declaration.all_attributes()):
        try:
            primitive = ifcopenshell.util.attribute.get_primitive_type(attribute)
        except Exception:
            primitive = "unknown"
        kind = primitive if isinstance(primitive, str) else str(primitive[0])
        layout.append((attribute.name(), kind, bool(derived[index])))
    return tuple(layout)


@lru_cache(maxsize=16384)
def declared_type(schema_name: str, entity_class: str, attribute_name: str) -> str | None:
    """Name of the defined type of an attribute (e.g. ``IfcLengthMeasure``), if any."""
    try:
        declaration = _schema(schema_name).declaration_by_name(entity_class).as_entity()
        for attribute in declaration.all_attributes():
            if attribute.name() != attribute_name:
                continue
            kind = attribute.type_of_attribute()
            for getter in (
                lambda k: k.declared_type().name(),
                lambda k: k.type_of_element().declared_type().name(),
            ):
                try:
                    name = getter(kind)
                except Exception:
                    continue
                if isinstance(name, str) and name.startswith("Ifc"):
                    return name
            return None
    except Exception:
        return None
    return None


@lru_cache(maxsize=8192)
def enum_items(schema_name: str, entity_class: str, attribute_name: str) -> tuple[str, ...]:
    try:
        declaration = _schema(schema_name).declaration_by_name(entity_class).as_entity()
        for attribute in declaration.all_attributes():
            if attribute.name() == attribute_name:
                return tuple(ifcopenshell.util.attribute.get_enum_items(attribute))
    except Exception:
        pass
    return ()


def explicit_attributes(entity: Any) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(name, primitive_kind, value)`` for each explicit attribute.

    ``primitive_kind`` is IfcOpenShell's primitive type name (``"string"``,
    ``"enum"``, ``"entity"``, ``"float"``, ``"list"``...). Derived attributes
    are skipped.
    """
    try:
        layout = attribute_layout(entity_schema(entity), entity.is_a())
    except Exception:
        layout = ()
    if not layout:
        try:
            info = entity.get_info(recursive=False)
        except Exception:
            info = {}
        for name, value in info.items():
            if name not in ("id", "type"):
                yield name, "unknown", value
        return
    for index, (name, kind, derived) in enumerate(layout):
        if derived:
            continue
        try:
            value = entity[index]
        except Exception:
            value = None
        yield name, kind, value


def is_subtype_name(schema_name: str, entity_class: str, ancestor: str) -> bool:
    """Schema-level subtype test on class names (no instance needed)."""
    try:
        declaration = _schema(schema_name).declaration_by_name(entity_class)
    except Exception:
        return False
    while declaration is not None:
        if declaration.name().lower() == ancestor.lower():
            return True
        try:
            declaration = declaration.as_entity().supertype()
        except Exception:
            return False
    return False
