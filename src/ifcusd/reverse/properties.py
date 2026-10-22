"""Property and quantity sets: reading them from USD, writing them to IFC.

Reading is the inverse of :mod:`ifcusd.ifc.properties`. ``customData.ifcType``
on each USD property tells which IFC property kind it came from, so every
kind is rebuilt faithfully:

* ``ifc:pset:<Set>:<Prop>``                       -> ``IfcPropertySingleValue``
* ``... allowedTokens`` / ``IfcPropertyEnumeratedValue`` -> enumerated value + ``IfcPropertyEnumeration``
* arrays, vectors / ``IfcPropertyListValue``      -> ``IfcPropertyListValue``
* ``<Prop>:upperBoundValue`` ...                  -> ``IfcPropertyBoundedValue``
* ``<Prop>:definingValues`` / ``definedValues``   -> ``IfcPropertyTableValue``
* relationships                                    -> ``IfcPropertyReferenceValue`` (or a list of GlobalIds)
* time-sampled values                              -> ``IfcPropertyReferenceValue`` to an ``IfcTimeSeries``
* deeper namespaces                                -> ``IfcComplexProperty``
* ``ifc:qto:<Set>:<Q>``                            -> ``IfcQuantity*`` in an ``IfcElementQuantity``

Attributes in an extension namespace (``robot:navigation:clearance``) are read
into property sets named ``Robot_Navigation``; their values are SI units and
are converted to project units here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import ifcopenshell.guid
import ifcopenshell.util.element

from ifcusd.ifc import util
from ifcusd.ifc.properties import PropertySet
from ifcusd.naming import UniqueNames, safe_identifier
from ifcusd.reverse.attributes import AttributeWriter, create
from ifcusd.reverse.stage import attribute_value, layer_name, time_samples
from ifcusd.reverse.values import ValueFactory, cast_candidates, usd_element_type, values_equal

BOUND_ATTRIBUTES = {
    "upperBoundValue": "UpperBoundValue",
    "lowerBoundValue": "LowerBoundValue",
    "setPointValue": "SetPointValue",
}
TABLE_ATTRIBUTES = {"definingValues": "DefiningValues", "definedValues": "DefinedValues"}
QUANTITY_FOR_MEASURE = {
    "IfcLengthMeasure": "IfcQuantityLength",
    "IfcPositiveLengthMeasure": "IfcQuantityLength",
    "IfcNonNegativeLengthMeasure": "IfcQuantityLength",
    "IfcAreaMeasure": "IfcQuantityArea",
    "IfcVolumeMeasure": "IfcQuantityVolume",
    "IfcCountMeasure": "IfcQuantityCount",
    "IfcMassMeasure": "IfcQuantityWeight",
    "IfcTimeMeasure": "IfcQuantityTime",
    "IfcNumericMeasure": "IfcQuantityNumber",
}
QUANTITY_VALUE_ATTRIBUTE = {
    "IfcQuantityLength": "LengthValue",
    "IfcQuantityArea": "AreaValue",
    "IfcQuantityVolume": "VolumeValue",
    "IfcQuantityCount": "CountValue",
    "IfcQuantityWeight": "WeightValue",
    "IfcQuantityTime": "TimeValue",
    "IfcQuantityNumber": "NumberValue",
}
# Metadata key marking values USD stores as 32-bit floats (UsdPhysics): they compare at that precision,
# so a value that only went through float storage on a round trip does not count as changed.
SINGLE_PRECISION = "ifcPrecision"

# IfcObjectReferenceSelect: entity classes an IfcPropertyReferenceValue may point to.
REFERENCE_TARGETS = (
    "IfcMaterialDefinition",
    "IfcMaterial",
    "IfcMaterialList",
    "IfcMaterialLayer",
    "IfcPerson",
    "IfcOrganization",
    "IfcPersonAndOrganization",
    "IfcExternalReference",
    "IfcTimeSeries",
    "IfcAddress",
    "IfcAppliedValue",
    "IfcTable",
)


@dataclass
class SetValue:
    """One property or quantity value read from USD, below its set."""

    path: tuple[str, ...]
    name: str
    usd_type: str = ""
    value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tokens: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    samples: list[tuple[float, Any]] = field(default_factory=list)
    source: str = ""  # "<prim path>.<attribute>" the value was read from
    source_layer: str = ""  # file name of the strongest layer authoring it

    @property
    def ifc_type(self) -> str | None:
        return self.metadata.get("ifcType")

    @property
    def value_type(self) -> str | None:
        return self.metadata.get("ifcValueType")

    @property
    def inherited(self) -> bool:
        return self.metadata.get("ifcSource") == "type"

    @property
    def has_content(self) -> bool:
        return self.value is not None or bool(self.targets) or bool(self.samples)

    @property
    def tolerance(self) -> float:
        """Relative tolerance when comparing this value with IFC data."""
        return 1e-6 if self.metadata.get(SINGLE_PRECISION) == "single" else 1e-9

    def same_as(self, other: Any) -> bool:
        """Whether this value equals ``other`` (a plain value) at this value's precision."""
        return values_equal(_comparable(self.value), _comparable(other), self.tolerance)


@dataclass
class SetData:
    """A property or quantity set read from USD."""

    name: str
    is_quantity: bool = False
    values: list[SetValue] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    origin: str = "ifc"  # "ifc", "extension", "physics" or "simulation"

    @property
    def global_id(self) -> str | None:
        value = self.info.get("globalId")
        return str(value) if value else None

    @property
    def source(self) -> str:
        return str(self.info.get("source") or "occurrence")

    @property
    def ifc_class(self) -> str:
        return str(self.info.get("class") or ("IfcElementQuantity" if self.is_quantity else "IfcPropertySet"))

    @property
    def description(self) -> str | None:
        value = self.info.get("description")
        return str(value) if value else None


# ---------------------------------------------------------------------- reading


def read_value(prim: Any, name: str, path: tuple[str, ...], time: Any) -> SetValue:
    attribute = prim.GetAttribute(name)
    if attribute:
        allowed = attribute.GetMetadata("allowedTokens")
        return SetValue(
            path=path,
            name=str(attribute.GetDisplayName() or path[-1]),
            usd_type=str(attribute.GetTypeName()),
            value=attribute_value(attribute, time),
            metadata=dict(attribute.GetCustomData() or {}),
            allowed_tokens=tuple(str(token) for token in allowed) if allowed else (),
            samples=time_samples(attribute),
            source=f"{prim.GetPath().pathString}.{name}",
            source_layer=layer_name(attribute),
        )
    relationship = prim.GetRelationship(name)
    return SetValue(
        path=path,
        name=str(relationship.GetDisplayName() or path[-1]),
        usd_type="rel",
        metadata=dict(relationship.GetCustomData() or {}),
        targets=tuple(target.pathString for target in relationship.GetTargets()),
    )


def read_ifc_sets(prim: Any, time: Any) -> list[SetData]:
    """``ifc:pset:*`` and ``ifc:qto:*`` sets authored on ``prim``."""
    info = prim.GetCustomDataByKey("ifc") or {}
    sets: dict[tuple[str, str], SetData] = {}
    for namespace, is_quantity, key in (("ifc:pset", False, "propertySets"), ("ifc:qto", True, "quantitySets")):
        set_info = dict(info.get(key, {}) or {})
        for prop in prim.GetPropertiesInNamespace(namespace):
            name = prop.GetName()
            parts = name.split(":")
            if len(parts) < 4:
                continue
            ident = parts[2]
            data = sets.get((namespace, ident))
            if data is None:
                meta = dict(set_info.get(ident, {}) or {})
                set_name = meta.get("name") or prop.GetDisplayGroup() or ident
                data = SetData(name=str(set_name), is_quantity=is_quantity, info=meta)
                sets[(namespace, ident)] = data
            data.values.append(read_value(prim, name, tuple(parts[3:]), time))
    return list(sets.values())


def read_namespace_sets(
    prim: Any, namespaces: tuple[str, ...], time: Any, factory: ValueFactory, meters_per_unit: float
) -> list[SetData]:
    """Extension namespace attributes (``robot:*``, ``sim:*``) as property sets in project units."""
    sets: dict[str, SetData] = {}
    for namespace in namespaces:
        prefix = namespace[:1].upper() + namespace[1:]
        for prop in prim.GetPropertiesInNamespace(namespace):
            name = prop.GetName()
            parts = name.split(":")[1:]
            if not parts:
                continue
            if len(parts) == 1:
                set_name, path = f"{prefix}_Common", (parts[0],)
            else:
                set_name, path = f"{prefix}_{parts[0][:1].upper()}{parts[0][1:]}", tuple(parts[1:])
            value = read_value(prim, name, path, time)
            group = prop.GetDisplayGroup()
            set_name = str(group) if group else set_name
            if value.usd_type != "rel":
                _to_project_units(value, factory, meters_per_unit)
            sets.setdefault(set_name, SetData(name=set_name, origin="extension")).values.append(value)
    return list(sets.values())


def _to_project_units(value: SetValue, factory: ValueFactory, meters_per_unit: float) -> None:
    """Extension values are SI with lengths in stage units: convert them to project units."""
    _base, _count, role = usd_element_type(value.usd_type)
    if role in ("point", "vector"):
        value.metadata.setdefault("ifcValueType", "IfcLengthMeasure")

        def convert(v: Any) -> Any:
            return factory.from_si(_scale(v, meters_per_unit), "IfcLengthMeasure")

    elif value.value_type:

        def convert(v: Any) -> Any:
            return factory.from_si(v, value.value_type)

    else:
        return
    value.value = convert(value.value) if value.value is not None else None
    value.samples = [(t, convert(v)) for t, v in value.samples]


def _scale(value: Any, factor: float) -> Any:
    if isinstance(value, (list, tuple)):
        return type(value)(_scale(v, factor) for v in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) * factor
    return value


# ---------------------------------------------------------------------- comparing


def changed_values(usd: SetData, existing: PropertySet | None, entity_for_path: Callable[[str], Any]) -> list[SetValue]:
    """Values of ``usd`` that are new or differ from the IFC set as the forward converter reads it."""
    if existing is None:
        return [v for v in usd.values if v.has_content]
    by_path = {tuple(v.path): v for v in existing.values}
    changed = []
    for value in usd.values:
        if not value.has_content:
            continue
        old = by_path.get(value.path)
        if old is None or value.samples:
            changed.append(value)
        elif value.targets:
            new_ids = sorted(e.id() for e in (entity_for_path(t) for t in value.targets) if e is not None)
            if new_ids != sorted(t.id() for t in old.targets):
                changed.append(value)
        elif not value.same_as(old.value.value if old.value else None):
            changed.append(value)
    return changed


def _comparable(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


# ---------------------------------------------------------------------- writing


class PropertyWriter:
    """Builds IFC properties and quantities and creates or updates their sets."""

    def __init__(
        self,
        model: Any,
        factory: ValueFactory,
        attributes: AttributeWriter,
        *,
        owner_history: Callable[[], Any],
        entity_for_path: Callable[[str], Any],
        time_series: Callable[[SetValue], Any] | None,
        warn: Callable[[str], None],
    ) -> None:
        self.model = model
        self.factory = factory
        self.attributes = attributes
        self.owner_history = owner_history
        self.entity_for_path = entity_for_path
        self.time_series = time_series
        self.warn = warn
        self.values_written = 0
        self._enumerations: dict[tuple[str, tuple[str, ...]], Any] = {}

    # ------------------------------------------------------------------ building

    def build(self, values: list[SetValue], *, quantity: bool) -> list[Any]:
        groups: dict[str, list[SetValue]] = {}
        for value in values:
            if value.path:
                groups.setdefault(value.path[0], []).append(value)
        out = []
        for key, members in groups.items():
            item = self._quantity_group(key, members) if quantity else self._property_group(key, members)
            if item is not None:
                out.append(item)
        return out

    def _property_group(self, key: str, members: list[SetValue]) -> Any | None:
        first = members[0]
        if len(members) == 1 and len(first.path) == 1:
            return self._simple_property(first)
        leaves = {m.path[-1] for m in members}
        if all(len(m.path) == 2 for m in members):
            if first.ifc_type == "IfcPropertyBoundedValue" and leaves <= set(BOUND_ATTRIBUTES):
                return self._bounded(members)
            if first.ifc_type == "IfcPropertyTableValue" and leaves <= set(TABLE_ATTRIBUTES):
                return self._table(members)
        children = self.build([replace(m, path=m.path[1:]) for m in members if len(m.path) > 1], quantity=False)
        if not children:
            return None
        name = next(
            (
                m.metadata["ifcComplexProperty"]
                for m in members
                if len(m.path) == 2 and m.metadata.get("ifcComplexProperty")
            ),
            key,
        )
        usage = next((m.metadata["ifcUsageName"] for m in members if m.metadata.get("ifcUsageName")), "IfcUSD")
        return create(
            self.model, "IfcComplexProperty", self.warn, Name=str(name), UsageName=str(usage), HasProperties=children
        )

    def _simple_property(self, value: SetValue) -> Any | None:
        description = value.metadata.get("ifcDescription")
        common = {"Name": value.name, "Description": str(description) if description else None}
        if value.samples:
            series = self.time_series(value) if self.time_series is not None else None
            if series is not None:
                self.values_written += 1
                return create(self.model, "IfcPropertyReferenceValue", self.warn, PropertyReference=series, **common)
        if value.targets:
            return self._reference(value, common)
        raw = value.value
        kind = value.ifc_type
        if raw is None:
            if kind == "IfcPropertyReferenceValue":
                return None
            self.values_written += 1
            return create(self.model, "IfcPropertySingleValue", self.warn, **common)
        if kind == "IfcPropertyReferenceValue":
            self.warn(f"Property {value.name}: a reference to an entity without a prim cannot be rebuilt (skipped)")
            return None
        if kind == "IfcPropertyEnumeratedValue" or (kind is None and value.allowed_tokens):
            items = self.factory.values(value.usd_type, raw if isinstance(raw, list) else [raw], value.value_type)
            enumeration = self._enumeration(value) if value.allowed_tokens else None
            self.values_written += 1
            return create(
                self.model,
                "IfcPropertyEnumeratedValue",
                self.warn,
                EnumerationValues=items or None,
                EnumerationReference=enumeration,
                **common,
            )
        if kind == "IfcPropertyListValue" or isinstance(raw, (list, tuple)):
            items = self.factory.values(
                value.usd_type, raw if isinstance(raw, (list, tuple)) else [raw], value.value_type
            )
            if not items:
                return None
            self.values_written += 1
            return create(self.model, "IfcPropertyListValue", self.warn, ListValues=items, **common)
        nominal = self.factory.value(value.usd_type, raw, value.value_type)
        self.values_written += 1
        return create(self.model, "IfcPropertySingleValue", self.warn, NominalValue=nominal, **common)

    def _reference(self, value: SetValue, common: dict[str, Any]) -> Any | None:
        targets = [self.entity_for_path(path) for path in value.targets]
        entities = [t for t in targets if t is not None]
        if not entities:
            return None
        usage = value.metadata.get("ifcUsageName")
        if len(entities) == 1 and any(util.is_a(entities[0], cls) for cls in REFERENCE_TARGETS):
            self.values_written += 1
            return create(
                self.model,
                "IfcPropertyReferenceValue",
                self.warn,
                UsageName=str(usage) if usage else None,
                PropertyReference=entities[0],
                **common,
            )
        # Objects cannot be property references: list their GlobalIds instead.
        identifiers = [str(util.attr(e, "GlobalId") or f"#{e.id()}") for e in entities]
        items = [self.factory.wrap("IfcIdentifier", identifier) for identifier in identifiers]
        self.values_written += 1
        return create(self.model, "IfcPropertyListValue", self.warn, ListValues=[i for i in items if i], **common)

    def _enumeration(self, value: SetValue) -> Any | None:
        name = str(value.metadata.get("ifcEnumeration") or value.name)
        key = (name, value.allowed_tokens)
        if key not in self._enumerations:
            existing = next(
                (
                    e
                    for e in util.by_type(self.model, "IfcPropertyEnumeration")
                    if e.Name == name
                    and tuple(str(v.wrappedValue) for v in e.EnumerationValues) == value.allowed_tokens
                ),
                None,
            )
            if existing is None:
                items = self.factory.values("token", list(value.allowed_tokens), value.value_type)
                existing = create(self.model, "IfcPropertyEnumeration", self.warn, Name=name, EnumerationValues=items)
            self._enumerations[key] = existing
        return self._enumerations[key]

    def _bounded(self, members: list[SetValue]) -> Any:
        first = members[0]
        attributes = {}
        for member in members:
            if member.value is not None:
                attributes[BOUND_ATTRIBUTES[member.path[-1]]] = self.factory.value(
                    member.usd_type, member.value, member.value_type
                )
        self.values_written += len(members)
        description = first.metadata.get("ifcDescription")
        return create(
            self.model,
            "IfcPropertyBoundedValue",
            self.warn,
            Name=first.name.rsplit(".", 1)[0],
            Description=str(description) if description else None,
            **attributes,
        )

    def _table(self, members: list[SetValue]) -> Any:
        first = members[0]
        attributes: dict[str, Any] = {}
        for member in members:
            raw = member.value if isinstance(member.value, (list, tuple)) else [member.value]
            items = self.factory.values(member.usd_type, [v for v in raw if v is not None], member.value_type)
            if items:
                attributes[TABLE_ATTRIBUTES[member.path[-1]]] = items
        for key in ("Expression", "CurveInterpolation"):
            if first.metadata.get(f"ifc{key}"):
                attributes[key] = str(first.metadata[f"ifc{key}"])
        self.values_written += len(members)
        return create(self.model, "IfcPropertyTableValue", self.warn, Name=first.name.rsplit(".", 1)[0], **attributes)

    def _quantity_group(self, key: str, members: list[SetValue]) -> Any | None:
        first = members[0]
        if len(members) == 1 and len(first.path) == 1:
            return self._simple_quantity(first)
        children = self.build([replace(m, path=m.path[1:]) for m in members if len(m.path) > 1], quantity=True)
        if not children:
            return None
        name = next(
            (
                m.metadata["ifcComplexQuantity"]
                for m in members
                if len(m.path) == 2 and m.metadata.get("ifcComplexQuantity")
            ),
            key,
        )
        discrimination = first.metadata.get("ifcDiscrimination") or "IfcUSD"
        return create(
            self.model,
            "IfcPhysicalComplexQuantity",
            self.warn,
            Name=str(name),
            HasQuantities=children,
            Discrimination=str(discrimination),
        )

    def _simple_quantity(self, value: SetValue) -> Any | None:
        raw = value.value
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            if raw is not None:
                self.warn(f"Quantity {value.name}: {raw!r} is not a number (skipped)")
            return None
        cls = (
            value.ifc_type if value.ifc_type in QUANTITY_VALUE_ATTRIBUTE else QUANTITY_FOR_MEASURE.get(value.value_type)
        )
        if not cls or not self.factory.is_entity_class(cls):
            cls = "IfcQuantityNumber" if self.factory.is_entity_class("IfcQuantityNumber") else "IfcQuantityCount"
        description = value.metadata.get("ifcDescription")
        formula = value.metadata.get("ifcFormula")
        quantity = create(
            self.model,
            cls,
            self.warn,
            Name=value.name,
            Description=str(description) if description else None,
            Formula=str(formula) if formula else None,
        )
        attribute = QUANTITY_VALUE_ATTRIBUTE[cls]
        for candidate in cast_candidates(raw):
            try:
                setattr(quantity, attribute, candidate)
                break
            except Exception:
                continue
        self.values_written += 1
        return quantity

    # ------------------------------------------------------------------ sets

    def create_set(self, owner: Any, data: SetData, values: list[SetValue] | None = None) -> Any | None:
        """Create the set for ``owner`` (related to it); ``None`` when nothing can be written."""
        values = data.values if values is None else values
        values = [v for v in values if v.has_content]
        global_id = data.global_id if data.global_id and not self._guid_taken(data.global_id) else None
        description = data.description
        if data.ifc_class not in ("IfcPropertySet", "IfcElementQuantity") and not data.is_quantity:
            definition = self._predefined(data, values, global_id)
        elif data.is_quantity:
            items = self.build(values, quantity=True)
            if not items:
                return None
            method = data.info.get("methodOfMeasurement")
            definition = create(
                self.model,
                "IfcElementQuantity",
                self.warn,
                GlobalId=global_id or ifcopenshell.guid.new(),
                OwnerHistory=self.owner_history(),
                Name=data.name,
                Description=description,
                MethodOfMeasurement=str(method) if method else None,
                Quantities=items,
            )
        elif owner is not None and _is_material(owner):
            items = self.build(values, quantity=False)
            if not items:
                return None
            if self.factory.is_ifc2x3:
                definition = create(
                    self.model,
                    "IfcExtendedMaterialProperties",
                    self.warn,
                    Material=owner,
                    ExtendedProperties=items,
                    Description=description,
                    Name=data.name,
                )
            else:
                definition = create(
                    self.model,
                    "IfcMaterialProperties",
                    self.warn,
                    Name=data.name,
                    Description=description,
                    Properties=items,
                    Material=owner,
                )
            return definition
        else:
            items = self.build(values, quantity=False)
            if not items:
                return None
            definition = create(
                self.model,
                "IfcPropertySet",
                self.warn,
                GlobalId=global_id or ifcopenshell.guid.new(),
                OwnerHistory=self.owner_history(),
                Name=data.name,
                Description=description,
                HasProperties=items,
            )
        if definition is not None and owner is not None:
            self.relate(owner, definition)
        return definition

    def _predefined(self, data: SetData, values: list[SetValue], global_id: str | None) -> Any | None:
        """Predefined property sets (``IfcDoorLiningProperties``...) store values as attributes."""
        if not self.factory.is_entity_class(data.ifc_class):
            self.warn(f"{data.ifc_class} does not exist in {self.factory.schema_name}; set {data.name!r} skipped")
            return None
        definition = create(
            self.model,
            data.ifc_class,
            self.warn,
            GlobalId=global_id or ifcopenshell.guid.new(),
            OwnerHistory=self.owner_history(),
            Name=data.name if data.name != data.ifc_class else None,
            Description=data.description,
        )
        for value in values:
            if len(value.path) == 1 and value.value is not None:
                self.attributes.set(definition, value.name, value.value, value_type=value.value_type)
                self.values_written += 1
        return definition

    def relate(self, owner: Any, definition: Any) -> Any | None:
        """Attach a property set definition to an object or type; returns the relationship, if any."""
        if util.is_a(owner, "IfcTypeObject"):
            owner.HasPropertySets = [*(owner.HasPropertySets or ()), definition]
            return None
        return create(
            self.model,
            "IfcRelDefinesByProperties",
            self.warn,
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner_history(),
            RelatedObjects=[owner],
            RelatingPropertyDefinition=definition,
        )

    def _guid_taken(self, global_id: str) -> bool:
        try:
            self.model.by_guid(global_id)
        except Exception:
            return False
        return True

    # ------------------------------------------------------------------ updating

    def update_set(self, definition: Any, values: list[SetValue], *, quantity: bool) -> None:
        """Write ``values`` into an existing set: replace changed properties, append new ones."""
        container = _container_attribute(definition)
        if container is None:  # predefined property set
            for value in values:
                if len(value.path) == 1 and value.value is not None:
                    self.attributes.set(definition, value.name, value.value, value_type=value.value_type)
                    self.values_written += 1
            return
        for value in values:
            self._update_value(definition, container, list(value.path), value, quantity)

    def _update_value(self, parent: Any, container: str, path: list[str], value: SetValue, quantity: bool) -> None:
        items = list(getattr(parent, container) or ())
        item = _find(items, path[0], "Quantity" if quantity else "Property")
        if item is None:
            built = self.build([replace(value, path=tuple(path))], quantity=quantity)
            if built:
                setattr(parent, container, items + built)
            return
        if len(path) == 1:
            built = self.build(
                [replace(value, path=(path[0],), name=util.attr(item, "Name") or value.name)], quantity=quantity
            )
            if built:
                setattr(parent, container, [built[0] if i == item else i for i in items])
                self._remove_if_unused(item)
            return
        owned = self._own(parent, container, item)
        if util.is_a(owned, "IfcComplexProperty"):
            self._update_value(owned, "HasProperties", path[1:], value, quantity)
        elif util.is_a(owned, "IfcPhysicalComplexQuantity"):
            self._update_value(owned, "HasQuantities", path[1:], value, quantity)
        elif len(path) == 2 and path[1] in BOUND_ATTRIBUTES:
            setattr(owned, BOUND_ATTRIBUTES[path[1]], self.factory.value(value.usd_type, value.value, value.value_type))
            self.values_written += 1
        elif len(path) == 2 and path[1] in TABLE_ATTRIBUTES:
            raw = value.value if isinstance(value.value, (list, tuple)) else [value.value]
            setattr(owned, TABLE_ATTRIBUTES[path[1]], self.factory.values(value.usd_type, raw, value.value_type))
            self.values_written += 1

    def _own(self, parent: Any, container: str, item: Any) -> Any:
        """Copy-on-write: properties shared with other sets are copied before they change."""
        if self.model.get_total_inverses(item) <= 1:
            return item
        copy = ifcopenshell.util.element.copy(self.model, item)
        setattr(parent, container, [copy if i == item else i for i in getattr(parent, container)])
        return copy

    def _remove_if_unused(self, item: Any) -> None:
        if self.model.get_total_inverses(item) == 0:
            ifcopenshell.util.element.remove_deep2(self.model, item)


def _find(items: list[Any], ident: str, fallback: str) -> Any | None:
    """The item a sanitized path component refers to, named the way the forward converter names it."""
    names = UniqueNames()
    for item in items:
        if names.claim(safe_identifier(util.attr(item, "Name") or util.ifc_class(item), fallback)) == ident:
            return item
    return None


def _container_attribute(definition: Any) -> str | None:
    for name in ("HasProperties", "Quantities", "Properties", "ExtendedProperties"):
        try:
            getattr(definition, name)
        except Exception:
            continue
        return name
    return None


def _is_material(entity: Any) -> bool:
    return util.is_a(entity, "IfcMaterialDefinition") or util.is_a(entity, "IfcMaterial")
