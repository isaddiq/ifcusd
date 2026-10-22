"""IFC values from USD values: the inverse of :mod:`ifcusd.ifc.values`.

When a USD attribute carries ``customData.ifcValueType`` (every attribute
IfcUSD writes does), that defined type is used. Otherwise the type is inferred:

==============================  ==========================================
USD type                        IFC type
==============================  ==========================================
``bool``                        ``IfcBoolean``
``int``, ``int64``, ``uint``    ``IfcInteger``
``float``, ``double``, ``half`` ``IfcReal``
``string``, ``token``           ``IfcLabel`` (``IfcText`` above 255 chars)
``asset``                       ``IfcURIReference`` (``IfcLabel`` in 2X3)
vectors, quaternions, matrices  a list of the component type
arrays                          a list of the element type
==============================  ==========================================

Values are cast to whatever the target schema declares for the defined type
(``IfcCountMeasure`` is a NUMBER in IFC4 but an INTEGER in IFC4X3), and every
cast is verified, so a value is never silently written as ``$``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import ifcopenshell.util.unit

from ifcusd.ifc import util

LABEL_MAX_LENGTH = 255

_SCALAR_TYPES = {
    "bool": "IfcBoolean",
    "int": "IfcInteger",
    "int64": "IfcInteger",
    "uint": "IfcInteger",
    "uint64": "IfcInteger",
    "uchar": "IfcInteger",
    "half": "IfcReal",
    "float": "IfcReal",
    "double": "IfcReal",
    "timecode": "IfcReal",
    "string": "IfcLabel",
    "token": "IfcLabel",
    "asset": "IfcURIReference",
}
_COMPONENT_BASES = {"f": "float", "d": "double", "h": "half", "i": "int"}
_ROLE_PREFIXES = ("point", "vector", "normal", "color", "texCoord", "frame")


def usd_element_type(type_name: str) -> tuple[str, int, str]:
    """``(scalar base, component count, role)`` of a USD type name.

    ``"point3f[]"`` gives ``("float", 3, "point")``, ``"double"`` gives
    ``("double", 1, "")`` and ``"matrix4d"`` gives ``("double", 16, "matrix")``.
    """
    name = type_name[:-2] if type_name.endswith("[]") else type_name
    if name.startswith("matrix") and len(name) >= 8 and name[6].isdigit():
        size = int(name[6])
        return "double", size * size, "matrix"
    if name.startswith("quat") and len(name) == 5:
        return _COMPONENT_BASES.get(name[-1], "double"), 4, "quaternion"
    role = next((prefix for prefix in _ROLE_PREFIXES if name.startswith(prefix)), "")
    rest = name[len(role) :] if role else name
    if len(rest) >= 2 and rest[-2].isdigit() and rest[-1] in _COMPONENT_BASES:
        return _COMPONENT_BASES[rest[-1]], int(rest[-2]), role
    if rest and rest[-1].isdigit() and rest[:-1] in _SCALAR_TYPES:  # e.g. "int2", "double3" written without suffix
        return rest[:-1], int(rest[-1]), role
    return name, 1, role


def python_value(value: Any) -> Any:
    """Plain Python data from a USD value (``Gf``/``Vt`` types become tuples and lists)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    path = getattr(value, "path", None)  # Sdf.AssetPath
    if isinstance(path, str) and hasattr(value, "resolvedPath"):
        return path
    if hasattr(value, "GetReal") and hasattr(value, "GetImaginary"):  # Gf.Quat*
        imaginary = value.GetImaginary()
        return (float(value.GetReal()), *(float(c) for c in imaginary))
    if hasattr(value, "GetRow") and hasattr(value, "dimension"):  # Gf.Matrix*
        rows, columns = value.dimension
        return tuple(float(value[r][c]) for r in range(rows) for c in range(columns))
    if hasattr(value, "GetValue") and type(value).__name__ == "TimeCode":  # Sdf.TimeCode
        return float(value.GetValue())
    if hasattr(value, "item") and not isinstance(value, (list, tuple)):  # numpy scalar
        try:
            return value.item()
        except Exception:
            pass
    try:
        items = list(value)
    except TypeError:
        text = str(value)
        try:
            return float(text)
        except ValueError:
            return text
    converted = [python_value(item) for item in items]
    type_name = type(value).__name__
    if type_name.startswith("Vec") and not type_name.endswith("Array"):  # Gf.Vec*, not Vt.Vec*Array
        return tuple(converted)
    return converted


def is_vector(value: Any) -> bool:
    return isinstance(value, tuple)


def flatten(value: Any) -> list[Any]:
    """Flat list of scalars from a scalar, vector, list or list of vectors."""
    if isinstance(value, (list, tuple)):
        out: list[Any] = []
        for item in value:
            out.extend(flatten(item))
        return out
    return [value]


def values_equal(a: Any, b: Any) -> bool:
    """Equality that tolerates float round-off and list/tuple differences."""
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != len(b):
            return False
        return all(values_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


class ValueFactory:
    """Creates typed IFC values for one model, cast to its schema."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.schema_name = util.schema_identifier(model)
        self.is_ifc2x3 = self.schema_name.upper() == "IFC2X3"
        self._kinds: dict[str, str | None] = {}
        self._si_scales: dict[str, float] = {}

    # ------------------------------------------------------------------ schema queries

    def kind(self, name: str | None) -> str | None:
        """``"entity"``, ``"type"``, ``"select"``, ``"enumeration"`` or ``None`` when unknown."""
        if not name:
            return None
        if name not in self._kinds:
            declaration = util.declaration(self.schema_name, name)
            kind = None
            if declaration is not None:
                if declaration.as_entity() is not None:
                    kind = "entity"
                elif declaration.as_type_declaration() is not None:
                    kind = "type"
                elif declaration.as_select_type() is not None:
                    kind = "select"
                elif declaration.as_enumeration_type() is not None:
                    kind = "enumeration"
            self._kinds[name] = kind
        return self._kinds[name]

    def is_entity_class(self, name: str | None) -> bool:
        return self.kind(name) == "entity"

    def is_defined_type(self, name: str | None) -> bool:
        return self.kind(name) == "type"

    def is_a(self, name: str, ancestor: str) -> bool:
        return util.is_subtype_name(self.schema_name, name, ancestor)

    def enum_items(self, entity_class: str, attribute: str) -> tuple[str, ...]:
        return util.enum_items(self.schema_name, entity_class, attribute)

    # ------------------------------------------------------------------ values

    def wrap(self, type_name: str, value: Any) -> Any | None:
        """``model.create_entity(type_name, value)`` with a verified, schema-correct cast."""
        if value is None or not self.is_defined_type(type_name):
            return None
        for candidate in cast_candidates(value):
            try:
                wrapped = self.model.create_entity(type_name, candidate)
            except Exception:
                continue
            if wrapped.wrappedValue is not None:
                return wrapped
        return None

    def infer_type(self, usd_type: str, value: Any) -> str:
        """IFC defined type for a USD value without ``ifcValueType`` metadata."""
        base, _count, _role = usd_element_type(usd_type)
        type_name = _SCALAR_TYPES.get(base, "IfcLabel")
        if type_name == "IfcURIReference" and not self.is_defined_type(type_name):
            type_name = "IfcLabel"
        if type_name == "IfcLabel" and any(isinstance(v, str) and len(v) > LABEL_MAX_LENGTH for v in flatten(value)):
            type_name = "IfcText"
        return type_name

    def value(self, usd_type: str, value: Any, declared: str | None = None) -> Any | None:
        """A single wrapped IFC value; falls back to the inferred type when ``declared`` cannot hold it."""
        if value is None:
            return None
        for type_name in (declared, self.infer_type(usd_type, value)):
            if type_name and self.is_defined_type(type_name):
                wrapped = self.wrap(type_name, value)
                if wrapped is not None:
                    return wrapped
        return self.wrap("IfcLabel", str(value))

    def values(self, usd_type: str, values: Iterable[Any], declared: str | None = None) -> list[Any]:
        """Wrapped IFC values for every scalar in ``values`` (vectors are flattened)."""
        out = []
        for item in flatten(list(values) if not isinstance(values, (list, tuple)) else values):
            wrapped = self.value(usd_type, item, declared)
            if wrapped is not None:
                out.append(wrapped)
        return out

    # ------------------------------------------------------------------ units

    def si_scale(self, measure_type: str | None) -> float:
        """SI value of one project unit of ``measure_type`` (``1.0`` when SI or unitless)."""
        if not measure_type:
            return 1.0
        if measure_type not in self._si_scales:
            scale = 1.0
            try:
                unit_type = ifcopenshell.util.unit.get_measure_unit_type(measure_type)
            except Exception:
                unit_type = None
            if unit_type and unit_type not in ("USERDEFINED", "REALUNIT") and self._has_project_unit(unit_type):
                try:
                    scale = float(ifcopenshell.util.unit.calculate_unit_scale(self.model, unit_type))
                except Exception:
                    scale = 1.0
                if unit_type == "MASSUNIT":
                    scale /= 1000.0  # IfcOpenShell scales mass units relative to the gram
            self._si_scales[measure_type] = scale or 1.0
        return self._si_scales[measure_type]

    def from_si(self, value: Any, measure_type: str | None) -> Any:
        """Convert an SI value (or list of values) into project units."""
        scale = self.si_scale(measure_type)
        if scale == 1.0:
            return value
        if isinstance(value, (list, tuple)):
            return type(value)(self.from_si(v, measure_type) for v in value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) / scale
        return value

    def _has_project_unit(self, unit_type: str) -> bool:
        for assignment in util.by_type(self.model, "IfcUnitAssignment"):
            for unit in util.attr(assignment, "Units", ()) or ():
                if str(util.attr(unit, "UnitType") or "") == unit_type:
                    return True
        return False


def cast_candidates(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return [value]
    if isinstance(value, int):
        return [value, float(value)]
    if isinstance(value, float):
        out: list[Any] = [value]
        if value.is_integer() and abs(value) < 2**63:
            out.append(int(value))
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)
        out = [items]
        if items and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in items):
            if all(float(v).is_integer() for v in items):
                out.append([int(v) for v in items])
            out.append([float(v) for v in items])
        return out
    return [value]
