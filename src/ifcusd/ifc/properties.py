"""Property set and quantity extraction with full typing.

Every IFC property kind is handled, not only single values:

* ``IfcPropertySingleValue``      -> one attribute
* ``IfcPropertyEnumeratedValue``  -> ``token``/``token[]`` + ``allowedTokens``
* ``IfcPropertyBoundedValue``     -> ``<Prop>:upperBoundValue`` / ``lowerBoundValue`` / ``setPointValue``
* ``IfcPropertyListValue``        -> array attribute
* ``IfcPropertyTableValue``       -> ``<Prop>:definingValues`` / ``definedValues``
* ``IfcPropertyReferenceValue``   -> relationship (or JSON when the target has no prim)
* ``IfcComplexProperty``          -> nested namespace ``<Complex>:<Sub>``
* ``IfcElementQuantity``          -> ``IfcQuantity*`` values, nested for complex quantities
* predefined property sets        -> one attribute per explicit attribute

Values keep their original (project) units; the unit and the IFC value type
travel with each value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ifcusd.ifc import util
from ifcusd.ifc.units import UnitResolver
from ifcusd.ifc.values import UsdValue, jsonable, to_json, to_usd_value, unwrap
from ifcusd.naming import UniqueNames, lower_first, safe_identifier

_ROOT_ATTRIBUTES = {"GlobalId", "OwnerHistory", "Name", "Description"}


@dataclass
class PropertyValue:
    """One USD property to author inside a property/quantity set namespace."""

    path: list[str]  # sanitized namespace components below the set, e.g. ["FireRating"]
    display_name: str
    value: UsdValue | None = None  # None with no targets: declared but empty
    targets: list[Any] = field(default_factory=list)  # entities -> USD relationship
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tokens: tuple[str, ...] = ()

    @property
    def is_relationship(self) -> bool:
        return bool(self.targets)

    def key(self) -> tuple[str, ...]:
        return tuple(self.path)


@dataclass
class PropertySet:
    """A property set or quantity set, ready to be written as a USD namespace."""

    name: str
    ifc_class: str
    is_quantity: bool
    global_id: str | None = None
    step_id: int | None = None
    description: str | None = None
    source: str = "occurrence"
    values: list[PropertyValue] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class PropertyExtractor:
    """Turns IFC property definitions into :class:`PropertySet` records."""

    def __init__(self, units: UnitResolver, *, include_properties: bool, include_quantities: bool):
        self.units = units
        self.include_properties = include_properties
        self.include_quantities = include_quantities
        self._cache: dict[int, PropertySet | None] = {}

    # ------------------------------------------------------------------ public API

    def for_object(self, entity: Any, *, inherit_from_type: bool) -> list[PropertySet]:
        """Property sets of an object occurrence, merged with its type's sets."""
        occurrence = [self.convert(d) for d in occurrence_definitions(entity)]
        occurrence_sets = [s for s in occurrence if s is not None]
        if util.is_a(entity, "IfcTypeObject"):
            type_sets = [self.convert(d) for d in util.attr(entity, "HasPropertySets", ()) or ()]
            return _sorted([s for s in type_sets if s is not None] + occurrence_sets)
        if not inherit_from_type:
            return _sorted(occurrence_sets)
        type_object = object_type(entity)
        if type_object is None:
            return _sorted(occurrence_sets)
        inherited = [self.convert(d, source="type") for d in util.attr(type_object, "HasPropertySets", ()) or ()]
        return _sorted(_merge([s for s in inherited if s is not None], occurrence_sets))

    def for_material(self, material: Any) -> list[PropertySet]:
        """Material property sets (``IfcMaterialProperties`` in IFC4+, subtypes in IFC2X3)."""
        sets: list[PropertySet] = []
        for definition in util.inverse(material, "HasProperties"):
            converted = self._material_properties(definition)
            if converted is not None:
                sets.append(converted)
        if not sets:
            for definition in _material_properties_ifc2x3(material):
                converted = self._material_properties(definition)
                if converted is not None:
                    sets.append(converted)
        return _sorted(sets)

    def convert(self, definition: Any, *, source: str = "occurrence") -> PropertySet | None:
        """Convert one ``IfcPropertySetDefinition`` (cached per instance)."""
        identifier = util.step_id(definition)
        if identifier is not None and identifier in self._cache:
            cached = self._cache[identifier]
            return None if cached is None else _with_source(cached, source)
        result = self._convert(definition)
        if identifier is not None:
            self._cache[identifier] = result
        return None if result is None else _with_source(result, source)

    # ------------------------------------------------------------------ conversion

    def _convert(self, definition: Any) -> PropertySet | None:
        if util.is_a(definition, "IfcElementQuantity"):
            if not self.include_quantities:
                return None
            pset = _new_set(definition, is_quantity=True)
            method = util.attr(definition, "MethodOfMeasurement")
            if method:
                pset.extra["methodOfMeasurement"] = str(method)
            names = UniqueNames()
            for quantity in util.attr(definition, "Quantities", ()) or ():
                pset.values.extend(self._quantity(quantity, [], names))
            return pset
        if not self.include_properties:
            return None
        if util.is_a(definition, "IfcPropertySet"):
            pset = _new_set(definition, is_quantity=False)
            names = UniqueNames()
            for prop in util.attr(definition, "HasProperties", ()) or ():
                pset.values.extend(self._property(prop, [], names))
            return pset
        if util.is_a(definition, "IfcPropertySetDefinition"):
            return self._predefined(definition)
        return None

    def _predefined(self, definition: Any) -> PropertySet:
        """Predefined property sets (e.g. ``IfcDoorLiningProperties``)."""
        pset = _new_set(definition, is_quantity=False)
        if not util.attr(definition, "Name"):
            pset.name = util.ifc_class(definition)
        names = UniqueNames()
        for name, kind, value in util.explicit_attributes(definition):
            if name in _ROOT_ATTRIBUTES or value is None:
                continue
            pset.values.append(self._attribute_value(definition, name, kind, value, [], names, "attribute"))
        return pset

    def _material_properties(self, definition: Any) -> PropertySet | None:
        if not self.include_properties:
            return None
        name = util.attr(definition, "Name") or util.ifc_class(definition)
        pset = PropertySet(
            name=str(name),
            ifc_class=util.ifc_class(definition),
            is_quantity=False,
            step_id=util.step_id(definition),
            description=util.attr(definition, "Description"),
            source="material",
        )
        names = UniqueNames()
        properties = util.attr(definition, "Properties") or util.attr(definition, "ExtendedProperties")
        if properties:
            for prop in properties:
                pset.values.extend(self._property(prop, [], names))
        else:
            for attr_name, kind, value in util.explicit_attributes(definition):
                if attr_name in ("Material", "Name", "Description") or value is None:
                    continue
                pset.values.append(self._attribute_value(definition, attr_name, kind, value, [], names, "attribute"))
        return pset

    def _property(self, prop: Any, prefix: list[str], names: UniqueNames) -> list[PropertyValue]:
        name = str(util.attr(prop, "Name") or util.ifc_class(prop))
        ident = names.claim(safe_identifier(name, "Property"))
        path = [*prefix, ident]
        cls = util.ifc_class(prop)
        meta: dict[str, Any] = {"ifcType": cls}
        description = util.attr(prop, "Description")
        if description:
            meta["ifcDescription"] = str(description)
        explicit_unit = self.units.unit_label(util.attr(prop, "Unit")) if util.attr(prop, "Unit") else None

        if util.is_a(prop, "IfcPropertySingleValue"):
            nominal = util.attr(prop, "NominalValue")
            return [self._value(path, name, nominal, meta, explicit_unit)]

        if util.is_a(prop, "IfcPropertyEnumeratedValue"):
            values = [unwrap(v)[0] for v in util.attr(prop, "EnumerationValues", ()) or ()]
            enumeration = util.attr(prop, "EnumerationReference")
            allowed: tuple[str, ...] = ()
            if enumeration is not None:
                allowed = tuple(str(unwrap(v)[0]) for v in util.attr(enumeration, "EnumerationValues", ()) or ())
                meta["ifcEnumeration"] = str(util.attr(enumeration, "Name") or "")
                enum_unit = util.attr(enumeration, "Unit")
                if enum_unit is not None and explicit_unit is None:
                    explicit_unit = self.units.unit_label(enum_unit)
            value_type = None
            raw_values = util.attr(prop, "EnumerationValues", ()) or ()
            if raw_values:
                value_type = unwrap(raw_values[0])[1]
            if value_type:
                meta["ifcValueType"] = value_type
            if explicit_unit:
                meta["ifcUnit"] = explicit_unit
            if len(values) == 1:
                usd_value = UsdValue("token", str(values[0]), value_type)
            else:
                usd_value = UsdValue("token[]", [str(v) for v in values], value_type)
            return [PropertyValue(path, name, usd_value, metadata=meta, allowed_tokens=allowed)]

        if util.is_a(prop, "IfcPropertyBoundedValue"):
            out: list[PropertyValue] = []
            for attr_name in ("UpperBoundValue", "LowerBoundValue", "SetPointValue"):
                bound = util.attr(prop, attr_name)
                if bound is None:
                    continue
                out.append(
                    self._value(
                        [*path, lower_first(attr_name)], f"{name}.{attr_name}", bound, dict(meta), explicit_unit
                    )
                )
            if not out:
                out.append(PropertyValue(path, name, None, metadata=meta))
            return out

        if util.is_a(prop, "IfcPropertyListValue"):
            values = util.attr(prop, "ListValues", ()) or ()
            return [self._value(path, name, tuple(values), meta, explicit_unit)]

        if util.is_a(prop, "IfcPropertyTableValue"):
            out = []
            for attr_name, unit_attr in (("DefiningValues", "DefiningUnit"), ("DefinedValues", "DefinedUnit")):
                values = util.attr(prop, attr_name, ()) or ()
                unit = self.units.unit_label(util.attr(prop, unit_attr)) if util.attr(prop, unit_attr) else None
                out.append(
                    self._value([*path, lower_first(attr_name)], f"{name}.{attr_name}", tuple(values), dict(meta), unit)
                )
            for attr_name in ("Expression", "CurveInterpolation"):
                extra = util.attr(prop, attr_name)
                if extra is not None:
                    for item in out:
                        item.metadata[f"ifc{attr_name}"] = str(unwrap(extra)[0])
            return out

        if util.is_a(prop, "IfcPropertyReferenceValue"):
            usage = util.attr(prop, "UsageName")
            if usage:
                meta["ifcUsageName"] = str(usage)
            reference = util.attr(prop, "PropertyReference")
            if reference is None:
                return [PropertyValue(path, name, None, metadata=meta)]
            meta["ifcValueType"] = util.ifc_class(reference)
            return [
                PropertyValue(
                    path,
                    name,
                    UsdValue("string", to_json(jsonable(reference)), util.ifc_class(reference), "json"),
                    targets=[reference],
                    metadata=meta,
                )
            ]

        if util.is_a(prop, "IfcComplexProperty"):
            usage = util.attr(prop, "UsageName")
            nested_names = UniqueNames()
            out = []
            for child in util.attr(prop, "HasProperties", ()) or ():
                for item in self._property(child, path, nested_names):
                    item.metadata.setdefault("ifcComplexProperty", name)
                    if usage:
                        item.metadata.setdefault("ifcUsageName", str(usage))
                    out.append(item)
            return out or [PropertyValue(path, name, None, metadata=meta)]

        # Unknown IfcProperty subtype: keep everything as JSON.
        return [PropertyValue(path, name, UsdValue("string", to_json(jsonable(prop)), cls, "json"), metadata=meta)]

    def _quantity(self, quantity: Any, prefix: list[str], names: UniqueNames) -> list[PropertyValue]:
        name = str(util.attr(quantity, "Name") or util.ifc_class(quantity))
        ident = names.claim(safe_identifier(name, "Quantity"))
        path = [*prefix, ident]
        cls = util.ifc_class(quantity)
        meta: dict[str, Any] = {"ifcType": cls}
        description = util.attr(quantity, "Description")
        if description:
            meta["ifcDescription"] = str(description)
        formula = util.attr(quantity, "Formula")
        if formula:
            meta["ifcFormula"] = str(formula)
        if util.is_a(quantity, "IfcPhysicalComplexQuantity"):
            for attr_name in ("Discrimination", "Quality", "Usage"):
                extra = util.attr(quantity, attr_name)
                if extra:
                    meta[f"ifc{attr_name}"] = str(extra)
            nested = UniqueNames()
            out: list[PropertyValue] = []
            for child in util.attr(quantity, "HasQuantities", ()) or ():
                for item in self._quantity(child, path, nested):
                    item.metadata.setdefault("ifcComplexQuantity", name)
                    out.append(item)
            return out or [PropertyValue(path, name, None, metadata=meta)]

        value = None
        for name_, _kind, attribute_value in util.explicit_attributes(quantity):
            if name_.endswith("Value") and attribute_value is not None:
                value = attribute_value
                break
        measure = UnitResolver.measure_for_quantity(cls)
        unit = self.units.unit_label(util.attr(quantity, "Unit")) if util.attr(quantity, "Unit") else None
        if unit is None:
            unit = self.units.label_for_measure(measure)
        if measure:
            meta["ifcValueType"] = measure
        if unit:
            meta["ifcUnit"] = unit
        usd_value = to_usd_value(value)
        if usd_value is not None and usd_value.type_name in ("int", "int64") and cls != "IfcQuantityCount":
            usd_value = UsdValue("double", float(usd_value.value), usd_value.value_type)
        return [PropertyValue(path, name, usd_value, metadata=meta)]

    def _value(
        self,
        path: list[str],
        display_name: str,
        raw: Any,
        meta: dict[str, Any],
        explicit_unit: str | None,
    ) -> PropertyValue:
        if util.is_entity(raw):
            meta["ifcValueType"] = util.ifc_class(raw)
            return PropertyValue(
                path,
                display_name,
                UsdValue("string", to_json(jsonable(raw)), util.ifc_class(raw), "json"),
                targets=[raw],
                metadata=meta,
            )
        usd_value = to_usd_value(raw)
        if usd_value is not None:
            if usd_value.value_type:
                meta["ifcValueType"] = usd_value.value_type
            if usd_value.encoding:
                meta["ifcEncoding"] = usd_value.encoding
            unit = explicit_unit or self.units.label_for_measure(usd_value.value_type)
            if unit:
                meta["ifcUnit"] = unit
        return PropertyValue(path, display_name, usd_value, metadata=meta)

    def _attribute_value(
        self,
        owner: Any,
        name: str,
        kind: str,
        value: Any,
        prefix: list[str],
        names: UniqueNames,
        ifc_type: str,
    ) -> PropertyValue:
        ident = names.claim(safe_identifier(name, "Attribute"))
        meta: dict[str, Any] = {"ifcType": ifc_type}
        if util.is_entity(value) or (isinstance(value, (list, tuple)) and any(util.is_entity(v) for v in value)):
            return PropertyValue(
                [*prefix, ident],
                name,
                UsdValue("string", to_json(jsonable(value)), None, "json"),
                metadata={**meta, "ifcEncoding": "json"},
            )
        usd_value = to_usd_value(value, enum=(kind == "enum"))
        allowed: tuple[str, ...] = ()
        if usd_value is not None:
            if usd_value.value_type:
                meta["ifcValueType"] = usd_value.value_type
                unit = self.units.label_for_measure(usd_value.value_type)
                if unit:
                    meta["ifcUnit"] = unit
            if kind == "enum":
                allowed = util.enum_items(util.entity_schema(owner), owner.is_a(), name)
        return PropertyValue([*prefix, ident], name, usd_value, metadata=meta, allowed_tokens=allowed)


# ---------------------------------------------------------------------- helpers


def occurrence_definitions(entity: Any) -> list[Any]:
    """Property set definitions attached directly to an object."""
    definitions: list[Any] = []
    for rel in util.inverse(entity, "IsDefinedBy"):
        if not util.is_a(rel, "IfcRelDefinesByProperties"):
            continue
        relating = util.attr(rel, "RelatingPropertyDefinition")
        if isinstance(relating, (list, tuple)):  # IfcPropertySetDefinitionSet
            definitions.extend(relating)
        elif relating is not None:
            definitions.append(relating)
    return definitions


def object_type(entity: Any) -> Any | None:
    """The type object of an occurrence (IFC2X3 and IFC4+)."""
    for rel in util.inverse(entity, "IsTypedBy"):
        relating = util.attr(rel, "RelatingType")
        if relating is not None:
            return relating
    for rel in util.inverse(entity, "IsDefinedBy"):
        if util.is_a(rel, "IfcRelDefinesByType"):
            relating = util.attr(rel, "RelatingType")
            if relating is not None:
                return relating
    return None


def _material_properties_ifc2x3(material: Any) -> list[Any]:
    """IFC2X3 has no inverse attribute from IfcMaterial to its property entities."""
    try:
        inverses = material.file.get_inverse(material)
    except Exception:
        return []
    return sorted(
        (item for item in inverses if util.is_a(item, "IfcMaterialProperties")),
        key=lambda item: item.id(),
    )


def _new_set(definition: Any, *, is_quantity: bool) -> PropertySet:
    return PropertySet(
        name=str(util.attr(definition, "Name") or util.ifc_class(definition)),
        ifc_class=util.ifc_class(definition),
        is_quantity=is_quantity,
        global_id=util.attr(definition, "GlobalId"),
        step_id=util.step_id(definition),
        description=util.attr(definition, "Description"),
    )


def _with_source(pset: PropertySet, source: str) -> PropertySet:
    """Copy of a (cached, shared) set tagged with where it came from."""
    values = [
        PropertyValue(
            list(v.path),
            v.display_name,
            v.value,
            list(v.targets),
            {**v.metadata, **({"ifcSource": source} if source != "occurrence" else {})},
            v.allowed_tokens,
        )
        for v in pset.values
    ]
    return PropertySet(
        name=pset.name,
        ifc_class=pset.ifc_class,
        is_quantity=pset.is_quantity,
        global_id=pset.global_id,
        step_id=pset.step_id,
        description=pset.description,
        source=source,
        values=values,
        extra=dict(pset.extra),
    )


def _merge(inherited: list[PropertySet], occurrence: list[PropertySet]) -> list[PropertySet]:
    """Merge type-level sets into occurrence sets; occurrence values win per property."""
    result: list[PropertySet] = list(occurrence)
    by_name = {(s.name, s.is_quantity): s for s in occurrence}
    for type_set in inherited:
        existing = by_name.get((type_set.name, type_set.is_quantity))
        if existing is None:
            result.append(type_set)
            by_name[(type_set.name, type_set.is_quantity)] = type_set
            continue
        present = {v.display_name for v in existing.values}
        for value in type_set.values:
            if value.display_name not in present:
                existing.values.append(_dedupe_path(value, existing))
        existing.source = "type+occurrence"
    return result


def _dedupe_path(value: PropertyValue, target: PropertySet) -> PropertyValue:
    used = {tuple(v.path) for v in target.values}
    if tuple(value.path) not in used:
        return value
    base = value.path[-1]
    index = 2
    while tuple([*value.path[:-1], f"{base}_{index}"]) in used:
        index += 1
    value.path = [*value.path[:-1], f"{base}_{index}"]
    return value


def _sorted(sets: list[PropertySet]) -> list[PropertySet]:
    return sorted(sets, key=lambda s: (s.is_quantity, s.name, s.step_id or 0))
