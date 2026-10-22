"""Setting EXPRESS attributes of IFC entities from USD values."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ifcusd.reverse.values import ValueFactory, cast_candidates


def express_name(usd_name: str) -> str:
    """``ifc:globalId`` -> ``GlobalId`` (inverse of :func:`ifcusd.naming.lower_first`)."""
    name = usd_name.split(":", 1)[1] if usd_name.startswith("ifc:") else usd_name
    return name[:1].upper() + name[1:]


def attribute_names(entity: Any) -> list[str]:
    try:
        return [entity.attribute_name(index) for index in range(len(entity))]
    except Exception:
        return []


def create(model: Any, ifc_class: str, warn: Callable[[str], None] | None = None, **attributes: Any) -> Any:
    """Create an entity, setting only the attributes its class has in this schema.

    ``None`` values are left unset. Values the schema rejects are reported
    through ``warn`` instead of aborting the export.
    """
    entity = model.create_entity(ifc_class)
    names = set(attribute_names(entity))
    for key, value in attributes.items():
        if value is None or key not in names:
            continue
        try:
            setattr(entity, key, value)
        except Exception as exc:
            if warn is not None:
                warn(f"{ifc_class}.{key} could not be set: {exc}")
    return entity


class AttributeWriter:
    """Sets explicit attributes with schema-correct casting."""

    def __init__(self, model: Any, factory: ValueFactory, warn: Callable[[str], None]) -> None:
        self.model = model
        self.factory = factory
        self.warn = warn

    def set(self, entity: Any, name: str, value: Any, *, value_type: str | None = None) -> bool:
        """Set ``entity.<name>``; tries plain casts first, then a wrapped ``value_type``."""
        if value is None:
            return False
        for candidate in cast_candidates(value):
            try:
                setattr(entity, name, candidate)
            except Exception:
                continue
            if getattr(entity, name, None) is not None:
                return True
        if value_type:
            wrapped = self.factory.wrap(value_type, value)
            if wrapped is not None:
                try:
                    setattr(entity, name, wrapped)
                    return True
                except Exception:
                    pass
        self.warn(f"{entity.is_a()} #{entity.id()}: attribute {name} = {value!r} could not be written")
        return False

    def get(self, entity: Any, name: str) -> Any:
        try:
            return getattr(entity, name)
        except Exception:
            return None
