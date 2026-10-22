"""Writing a scanned USD stage into an IFC model.

The same writer serves both modes. In *merge* mode the model is the source
IFC: prims are matched to its entities and only differences and additions are
written. In *standalone* mode the model starts empty and every prim is
created. The phases run in this order so that each can rely on the previous:

1. match prims to entities (merge) or set up project, units and contexts (standalone)
2. create entities for unmatched IFC prims and new objects
3. attributes (all of them for created entities, changed ones for matched)
4. relationships of created entities (decomposition, containment, types,
   associations, fillings, groups, generic relationship prims)
5. placements (created products, and moved products in merge mode)
6. geometry of created products, then presentation layers
7. property and quantity sets, including the robot/simulation extension sets
8. semantic labels as classifications, property set templates
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.unit
import numpy as np

from ifcusd.ifc import util
from ifcusd.ifc.properties import PropertyExtractor, PropertySet, occurrence_definitions
from ifcusd.ifc.structure import ASSOCIATIONS
from ifcusd.ifc.units import UnitResolver
from ifcusd.ifc.values import unwrap
from ifcusd.naming import lower_first
from ifcusd.options import IfcExportOptions
from ifcusd.reverse.attributes import AttributeWriter, attribute_names, create, express_name
from ifcusd.reverse.extension import (
    ClassificationWriter,
    TemplateWriter,
    TimeSeriesWriter,
    physics_set,
    semantic_labels,
    simulation_set,
)
from ifcusd.reverse.geometry import MeshPart, RepresentationWriter, mesh_parts
from ifcusd.reverse.properties import (
    PropertyWriter,
    SetData,
    SetValue,
    changed_values,
    read_ifc_sets,
    read_namespace_sets,
)
from ifcusd.reverse.stage import PrimRecord, UsdScene, attribute_value, rigid
from ifcusd.reverse.values import ValueFactory, values_equal

GUID_NAMESPACE = uuid.UUID("6f1c3b9e-2a47-5d1e-9c0b-1f5e2d7a4c80")

# Attributes represented by other USD constructs (see ifcusd.usd.scene).
_STRUCTURAL_ATTRIBUTES = {
    "GlobalId",
    "OwnerHistory",
    "ObjectPlacement",
    "Representation",
    "RepresentationMaps",
    "HasPropertySets",
    "RepresentationContexts",
    "UnitsInContext",
}
_IDENTITY_ATTRIBUTES = {"ifc:class", "ifc:stepId", "ifc:parentRelationship", "ifc:globalId"}

# ifc:parentRelationship -> (relationship class, parent attribute, child attribute, child is a set)
PARENTING = {
    "aggregates": ("IfcRelAggregates", "RelatingObject", "RelatedObjects", True),
    "nests": ("IfcRelNests", "RelatingObject", "RelatedObjects", True),
    "contains": ("IfcRelContainedInSpatialStructure", "RelatingStructure", "RelatedElements", True),
    "voids": ("IfcRelVoidsElement", "RelatingBuildingElement", "RelatedOpeningElement", False),
    "projects": ("IfcRelProjectsElement", "RelatingElement", "RelatedFeatureElement", False),
    "adheres": ("IfcRelAdheresToElement", "RelatingElement", "RelatedSurfaceFeatures", True),
    "port": ("IfcRelConnectsPortToElement", "RelatedElement", "RelatingPort", False),
}
# Relationships rebuilt from the namespace and USD relationships, never from relationship prims.
NATIVE_RELATIONSHIPS = {
    *(spec[0] for spec in PARENTING.values()),
    "IfcRelDecomposes",
    "IfcRelDefinesByType",
    "IfcRelDefinesByProperties",
    "IfcRelAssignsToGroup",
    "IfcRelFillsElement",
    *ASSOCIATIONS,
}
_SPATIAL = ("IfcSpatialElement", "IfcSpatialStructureElement")


class ModelWriter:
    """Applies one scanned stage to one IFC model."""

    def __init__(
        self,
        model: Any,
        scene: UsdScene,
        options: IfcExportOptions,
        *,
        merge: bool,
        identity: str,
        owner_history: Callable[[], Any],
        project_name: str,
    ) -> None:
        self.model = model
        self.scene = scene
        self.options = options
        self.merge = merge
        self.identity = identity
        self.owner_history = owner_history
        self.project_name = project_name
        self.counts: Counter[str] = Counter()
        self.warnings: list[str] = []
        self.created_objects: list[dict[str, Any]] = []
        self.not_in_usd: list[dict[str, Any]] = []
        self.factory = ValueFactory(model)
        self.attributes = AttributeWriter(model, self.factory, self.warn)
        self.project: Any = None
        self.records: list[PrimRecord] = []
        self._placements: dict[int, np.ndarray] = {}
        self._entity_by_path: dict[str, Any] = {}
        self._entity_by_step: dict[int, Any] = {}
        self._generic_parented: set[tuple[int, str]] = set()
        self._default_container: Any = None
        self._json_cache: dict[int, Any] = {}
        provenance_options = dict(scene.provenance.get("options", {}) or {})
        self._relationship_prims = str(provenance_options.get("relationship_prims", "generic"))
        self._inherit_type_properties = bool(provenance_options.get("inherit_type_properties", True))
        self._world_coords = bool(provenance_options.get("use_world_coords", False))

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # ================================================================== driver

    def run(self) -> None:
        self.records = self.scene.scan()
        self.warnings.extend(self.scene.warnings)
        if self.merge:
            self.project = next(iter(util.by_type(self.model, "IfcProject")), None)
            self._source_products = [
                {"id": p.id(), "globalId": p.GlobalId, "ifcClass": p.is_a(), "name": util.attr(p, "Name")}
                for p in util.by_type(self.model, "IfcProduct")
                if not util.is_a(p, "IfcOpeningElement")
            ]
            self._match()
        else:
            self._prepare_standalone()
        self.length_scale = float(ifcopenshell.util.unit.calculate_unit_scale(self.model)) or 1.0
        self.representations = RepresentationWriter(
            self.model, self.factory, length_scale=self.length_scale, warn=self.warn
        )
        self.templates = TemplateWriter(self.model, self.factory, self.owner_history, self.warn)
        self.classifications = ClassificationWriter(self.model, self.owner_history, self.warn)
        time_series = None
        if self.options.include_time_series:
            time_series = TimeSeriesWriter(
                self.model,
                self.factory,
                origin=self.options.time_origin,
                time_codes_per_second=self.scene.time_codes_per_second,
                warn=self.warn,
            )
        self.time_series = time_series
        self.properties = PropertyWriter(
            self.model,
            self.factory,
            self.attributes,
            owner_history=self.owner_history,
            entity_for_path=self._entity_for_path,
            time_series=time_series,
            warn=self.warn,
        )

        self._create()
        self._write_attributes()
        self._write_relationships()
        self._write_placements()
        if self.options.include_geometry:
            self._write_geometry()
        self._write_layers()
        self._write_property_sets()
        if self.options.include_semantics:
            for record in self.records:
                if record.entity is not None:
                    labels = [
                        label
                        for prim in self._data_prims(record)
                        for label in semantic_labels(prim, record.entity.is_a())
                    ]
                    self.classifications.assign(record.entity, list(dict.fromkeys(labels)))
            self.classifications.finish()
        if self.options.property_templates:
            if not self.templates.supported and self.templates._uses:
                self.warn(f"{self.factory.schema_name} has no property set templates; new sets are not declared")
            self.templates.finish(self.project)
        if self.merge:
            self._find_not_in_usd()
        self._finish_counts()

    # ================================================================== phase 1

    def _match(self) -> None:
        claimed: set[int] = set()
        for record in self.records:
            if record.new_object:
                continue
            entity = None
            if record.global_id:
                entity = self._by_guid(record.global_id)
            elif record.step_id is not None:
                try:
                    candidate = self.model.by_id(record.step_id)
                except Exception:
                    candidate = None
                if (
                    candidate is not None
                    and candidate.is_a() == record.ifc_class
                    and not hasattr(candidate, "GlobalId")
                ):
                    entity = candidate
            if entity is None:
                continue
            if entity.id() in claimed:
                # A copied prim keeps the GlobalId of its original: it becomes a new entity.
                self.counts["duplicates"] += 1
                self.warn(f"{record.path} repeats GlobalId {record.global_id}; written as a new {record.ifc_class}")
                record.global_id = None
                continue
            claimed.add(entity.id())
            record.entity = entity
            record.matched = True
            self.counts["matched"] += 1

    def _prepare_standalone(self) -> None:
        info = self.scene.file_info
        project_record = next((r for r in self.records if r.ifc_class == "IfcProject" and not r.new_object), None)
        self.project = create(
            self.model,
            "IfcProject",
            self.warn,
            GlobalId=(
                project_record.global_id if project_record and project_record.global_id else self._guid("project")
            ),
            OwnerHistory=self.owner_history(),
            Name=project_record.name if project_record else self.project_name,
        )
        if project_record is not None:
            project_record.entity = self.project
        self._write_units(info)
        model_context = ifcopenshell.api.context.add_context(self.model, context_type="Model")
        ifcopenshell.api.context.add_context(
            self.model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model_context
        )
        self._write_georeference(info, model_context)

    def _write_units(self, info: dict[str, Any]) -> None:
        labels = {
            key.split(":", 2)[2]: str(value)
            for key, value in info.items()
            if key.startswith("ifc:units:") and key != "ifc:units:lengthScale" and value
        }
        if "LENGTHUNIT" not in labels:
            labels.update({"LENGTHUNIT": "METRE", "AREAUNIT": "SQUARE_METRE", "VOLUMEUNIT": "CUBIC_METRE"})
            labels.setdefault("PLANEANGLEUNIT", "RADIAN")
        units = []
        lost = []
        for unit_type, label in sorted(labels.items()):
            unit = self._unit(unit_type, label)
            if unit is not None:
                units.append(unit)
            else:
                lost.append(f"{unit_type} ({label})")
        if lost:
            self.warn(f"{len(lost)} project unit(s) not recreated (IFC then assumes SI units): {', '.join(lost)}")
        if units:
            assignment = ifcopenshell.api.unit.assign_unit(self.model, units=units)
            assignment.Units = sorted(assignment.Units, key=lambda unit: unit.id())  # the API orders them by hash

    def _unit(self, unit_type: str, label: str) -> Any | None:
        if unit_type == "CURRENCYUNIT":
            return ifcopenshell.api.unit.add_monetary_unit(self.model, currency=label)
        conversion = label.lower().replace("_", " ")
        if conversion in ifcopenshell.util.unit.si_conversions:
            unit = ifcopenshell.api.unit.add_conversion_based_unit(self.model, name=conversion)
            return unit if util.attr(unit, "UnitType") == unit_type else unit
        names = ifcopenshell.util.unit.unit_names
        prefix = next(
            (p for p in ifcopenshell.util.unit.prefixes if label.startswith(p) and label[len(p) :] in names), None
        )
        name = label[len(prefix) :] if prefix else label
        if name not in names:
            return None
        unit = ifcopenshell.api.unit.add_si_unit(self.model, unit_type=unit_type, prefix=prefix)
        unit.Name = name
        return unit

    def _write_georeference(self, info: dict[str, Any], context: Any) -> None:
        north = info.get("ifc:geo:trueNorth")
        if north:
            context.TrueNorth = self.model.create_entity("IfcDirection", DirectionRatios=tuple(float(v) for v in north))
        if not info.get("ifc:geo:conversionClass") or not self.factory.is_entity_class("IfcMapConversion"):
            return
        crs = create(
            self.model,
            "IfcProjectedCRS",
            self.warn,
            Name=str(info.get("ifc:geo:crs:name") or "Unknown"),
            Description=info.get("ifc:geo:crs:description"),
            GeodeticDatum=info.get("ifc:geo:crs:geodeticDatum"),
            VerticalDatum=info.get("ifc:geo:crs:verticalDatum"),
            MapProjection=info.get("ifc:geo:crs:mapProjection"),
            MapZone=info.get("ifc:geo:crs:mapZone"),
        )
        create(
            self.model,
            "IfcMapConversion",
            self.warn,
            SourceCRS=context,
            TargetCRS=crs,
            Eastings=info.get("ifc:geo:eastings", 0.0),
            Northings=info.get("ifc:geo:northings", 0.0),
            OrthogonalHeight=info.get("ifc:geo:orthogonalHeight", 0.0),
            XAxisAbscissa=info.get("ifc:geo:xAxisAbscissa"),
            XAxisOrdinate=info.get("ifc:geo:xAxisOrdinate"),
            Scale=info.get("ifc:geo:scale"),
        )

    # ================================================================== phase 2

    def _create(self) -> None:
        skip_native = self._relationship_prims == "all"
        for record in self.records:
            if record.entity is not None:
                continue
            if record.new_object:
                record.entity = self._create_object(record)
                continue
            cls = record.ifc_class
            if cls == "IfcOwnerHistory" or util.is_subtype_name(
                self.factory.schema_name, cls, "IfcPropertySetDefinition"
            ):
                continue  # owner history is regenerated; property sets are written in phase 7
            if cls == "IfcRelDefinesByProperties" or (skip_native and cls in NATIVE_RELATIONSHIPS):
                continue
            record.entity = self._create_entity(record)
        for record in self.records:
            if record.entity is not None:
                self._entity_by_path[record.path] = record.entity
                if record.step_id is not None:
                    self._entity_by_step.setdefault(record.step_id, record.entity)

    def _create_entity(self, record: PrimRecord) -> Any | None:
        cls = record.ifc_class
        declaration = util.declaration(self.factory.schema_name, cls)
        entity_declaration = declaration.as_entity() if declaration is not None else None
        if entity_declaration is None or entity_declaration.is_abstract():
            if record.prim.HasAttribute("ifc:globalId") and (
                record.geometry or record.parent_relationship in PARENTING
            ):
                self.warn(
                    f"{cls} is not a concrete class of {self.factory.schema_name}; "
                    f"{record.path} written as {self.options.new_object_class}"
                )
                cls = self.options.new_object_class
            else:
                self.warn(f"{cls} is not a concrete class of {self.factory.schema_name}; {record.path} skipped")
                return None
        attributes: dict[str, Any] = {}
        if self.factory.is_a(cls, "IfcRoot"):
            global_id = record.global_id if record.global_id and self._by_guid(record.global_id) is None else None
            attributes["GlobalId"] = global_id or self._guid(record.path)
            attributes["OwnerHistory"] = self.owner_history()
        try:
            entity = create(self.model, cls, self.warn, **attributes)
        except Exception as exc:
            self.warn(f"{record.path}: {cls} could not be created ({exc})")
            return None
        self.counts["created"] += 1
        return entity

    def _create_object(self, record: PrimRecord) -> Any | None:
        cls = record.ifc_class
        for candidate in (record.ifc_class, self.options.new_object_class, "IfcBuildingElementProxy"):
            if self.factory.is_entity_class(candidate):
                cls = candidate
                break
        predefined = None
        if record.object_type and "USERDEFINED" in self.factory.enum_items(cls, "PredefinedType"):
            predefined = "USERDEFINED"
        entity = create(
            self.model,
            cls,
            self.warn,
            GlobalId=self._guid(record.path),
            OwnerHistory=self.owner_history(),
            Name=record.name,
            Description=f"Created by IfcUSD from the OpenUSD prim {record.path}",
            ObjectType=record.object_type,
            Tag=record.path,
            PredefinedType=predefined,
        )
        self.counts["newObjects"] += 1
        self.created_objects.append(
            {
                "globalId": entity.GlobalId,
                "ifcClass": cls,
                "name": record.name,
                "usdPath": record.path,
                "objectType": record.object_type,
            }
        )
        return entity

    # ================================================================== phase 3

    def _write_attributes(self) -> None:
        pending: list[tuple[Any, str, list[str]]] = []
        for record in self.records:
            entity = record.entity
            if entity is None or record.new_object:
                continue
            if not record.matched:
                pending.extend(self._all_attributes(record))
            elif self.options.update_attributes:
                self._changed_attributes(record)
        for entity, name, targets in pending:
            entities = [self._entity_for_path(t) for t in targets]
            if not entities or any(e is None for e in entities):
                continue
            for candidate in ([entities[0]] if len(entities) == 1 else []) + [entities]:
                try:
                    setattr(entity, name, candidate)
                    break
                except Exception:
                    continue
            if entity.is_a() in PARENTING_CLASSES:
                children = getattr(entity, PARENTING_CLASSES[entity.is_a()], None)
                for child in children if isinstance(children, (list, tuple)) else [children]:
                    if child is not None:
                        self._generic_parented.add((child.id(), entity.is_a()))

    def _all_attributes(self, record: PrimRecord) -> list[tuple[Any, str, list[str]]]:
        """Write every attribute of a created entity; returns entity references to resolve later."""
        entity, prim = record.entity, record.prim
        references = []
        for name in attribute_names(entity):
            if name in _STRUCTURAL_ATTRIBUTES:
                continue
            usd_name = f"ifc:{lower_first(name)}"
            relationship = prim.GetRelationship(usd_name)
            if relationship and relationship.GetTargets():
                references.append((entity, name, [t.pathString for t in relationship.GetTargets()]))
                continue
            attribute = prim.GetAttribute(usd_name)
            if not attribute or not attribute.HasAuthoredValue():
                attribute = prim.GetAttribute(f"{usd_name}Value")
            if not attribute or not attribute.HasAuthoredValue():
                continue
            metadata = dict(attribute.GetCustomData() or {})
            value = attribute_value(attribute, self.scene.time)
            if metadata.get("ifcEncoding") == "json":
                rebuilt = self._from_json(_load_json(value))
                if rebuilt is not None:
                    try:
                        setattr(entity, name, rebuilt)
                    except Exception:
                        self.warn(f"{entity.is_a()}.{name} could not be rebuilt from its JSON value")
                continue
            self.attributes.set(entity, name, value, value_type=metadata.get("ifcValueType"))
        self._fill_empty_aggregates(entity)
        return references

    def _fill_empty_aggregates(self, entity: Any) -> None:
        """Required lists that may be empty (``LIST [0:?]``) carry no USD value: restore them empty."""
        declaration = util.declaration(self.factory.schema_name, entity.is_a()).as_entity()
        derived = declaration.derived()
        for index, attribute in enumerate(declaration.all_attributes()):
            if attribute.optional() or derived[index] or entity[index] is not None:
                continue
            aggregation = attribute.type_of_attribute().as_aggregation_type()
            if aggregation is not None and aggregation.bound1() == 0:
                try:
                    setattr(entity, attribute.name(), ())
                except Exception:
                    continue

    def _changed_attributes(self, record: PrimRecord) -> None:
        entity, prim = record.entity, record.prim
        if record.ifc_class != entity.is_a() and self.factory.is_entity_class(record.ifc_class):
            try:
                entity = ifcopenshell.api.root.reassign_class(self.model, product=entity, ifc_class=record.ifc_class)
                record.entity = entity
                self._entity_by_path[record.path] = entity
                self.counts["reclassified"] += 1
            except Exception as exc:
                self.warn(f"{record.path}: could not change class to {record.ifc_class} ({exc})")
        names = set(attribute_names(entity))
        for attribute in prim.GetAttributes():
            usd_name = attribute.GetName()
            if not usd_name.startswith("ifc:") or usd_name.count(":") != 1 or usd_name in _IDENTITY_ATTRIBUTES:
                continue
            name = express_name(usd_name)
            if name not in names and name.endswith("Value") and name[:-5] in names:
                name = name[:-5]
            if name not in names or name in _STRUCTURAL_ATTRIBUTES or not attribute.HasAuthoredValue():
                continue
            metadata = dict(attribute.GetCustomData() or {})
            if metadata.get("ifcEncoding") == "json":
                continue
            value = attribute_value(attribute, self.scene.time)
            current, _defined = unwrap(self.attributes.get(entity, name))
            if values_equal(_plain(value), _plain(current)):
                continue
            if self.attributes.set(entity, name, value, value_type=metadata.get("ifcValueType")):
                self.counts["attributesUpdated"] += 1

    def _from_json(self, data: Any) -> Any:
        """Rebuild an entity graph IfcUSD stored as JSON; ``None`` when it is incomplete."""
        if isinstance(data, list):
            items = [self._from_json(item) for item in data]
            return None if any(item is None for item in items) else items
        if not isinstance(data, dict):
            return data
        cls = data.get("type")
        identifier = data.get("id")
        if identifier is not None and identifier in self._json_cache:
            return self._json_cache[identifier]
        attributes = {k: v for k, v in data.items() if k not in ("type", "id")}
        if not attributes:  # truncated reference: only resolvable when the entity has a prim
            return self._entity_by_step.get(identifier) if identifier is not None else None
        if not self.factory.is_entity_class(cls):
            return None
        entity = self.model.create_entity(cls)
        for name, value in attributes.items():
            converted = self._from_json(value)
            if converted is None:
                continue
            try:
                setattr(entity, name, converted)
            except Exception:
                pass
        declaration = util.declaration(self.factory.schema_name, cls).as_entity()
        derived = declaration.derived()
        for index, attribute in enumerate(declaration.all_attributes()):
            if not attribute.optional() and not derived[index] and entity[index] is None:
                self.model.remove(entity)
                return None
        if identifier is not None:
            self._json_cache[identifier] = entity
        return entity

    # ================================================================== phase 4

    def _write_relationships(self) -> None:
        parenting: dict[tuple[int, str], tuple[Any, list[Any]]] = {}
        contained: dict[int, tuple[Any, list[Any]]] = {}
        typed: dict[int, tuple[Any, list[Any]]] = {}
        associated: dict[tuple[str, int], tuple[str, str, Any, list[Any]]] = {}
        connections: list[tuple[Any, Any]] = []
        for record in self.records:
            entity = record.entity
            if entity is None or record.matched:
                continue
            if record.new_object:
                container, host = self._structure_for_new_object(record)
                if container is not None:
                    contained.setdefault(container.id(), (container, []))[1].append(entity)
                if host is not None:
                    connections.append((host, entity))
                continue
            parent = record.parent.entity if record.parent is not None else None
            label = record.parent_relationship
            if label not in PARENTING:
                label = self._default_relationship(entity, parent)
            if label in PARENTING and parent is not None:
                cls = PARENTING[label][0]
                if (entity.id(), cls) not in self._generic_parented:
                    if label == "contains":
                        contained.setdefault(parent.id(), (parent, []))[1].append(entity)
                    else:
                        parenting.setdefault((parent.id(), label), (parent, []))[1].append(entity)
            prim = record.prim
            for target in self._targets(prim, "ifc:isTypedBy")[:1]:
                typed.setdefault(target.id(), (target, []))[1].append(entity)
            for cls, (relating, usd_name) in ASSOCIATIONS.items():
                for target in self._targets(prim, usd_name):
                    if usd_name == "ifc:hasProfile" and not _profile_association_matches(cls, target):
                        continue
                    if not self.factory.is_entity_class(cls):
                        continue
                    associated.setdefault((cls, target.id()), (cls, relating, target, []))[3].append(entity)
            for opening in self._targets(prim, "ifc:fills")[:1]:
                self._relationship("IfcRelFillsElement", RelatingOpeningElement=opening, RelatedBuildingElement=entity)
            if util.is_a(entity, "IfcGroup"):
                members = [
                    m for m in self._targets(prim, "collection:members:includes") if util.is_a(m, "IfcObjectDefinition")
                ]
                if members:
                    self._relate("IfcRelAssignsToGroup", "RelatingGroup", entity, "RelatedObjects", members)

        for (_parent_id, label), (parent, children) in parenting.items():
            cls, parent_attribute, child_attribute, is_set = PARENTING[label]
            if is_set:
                self._relate(cls, parent_attribute, parent, child_attribute, children)
            else:
                for child in children:
                    self._relationship(cls, **{parent_attribute: parent, child_attribute: child})
        for container, elements in contained.values():
            self._relate(
                "IfcRelContainedInSpatialStructure", "RelatingStructure", container, "RelatedElements", elements
            )
        for type_object, objects in typed.values():
            self._relate("IfcRelDefinesByType", "RelatingType", type_object, "RelatedObjects", objects)
        for cls, relating, target, objects in associated.values():
            self._relate(cls, relating, target, "RelatedObjects", objects)
        for host, element in connections:
            self._relationship("IfcRelConnectsElements", RelatingElement=host, RelatedElement=element)

    def _relate(
        self, cls: str, relating_attribute: str, relating: Any, related_attribute: str, related: list[Any]
    ) -> Any:
        """Add ``related`` to the existing ``cls`` relationship of ``relating``, or create one.

        Some inverses allow a single relationship (an IFC4 type has one
        ``IfcRelDefinesByType``), and one relationship per relating entity is
        the cleaner model everywhere.
        """
        existing = next(
            (
                rel
                for rel in self.model.get_inverse(relating)
                if rel.is_a() == cls and getattr(rel, relating_attribute, None) == relating
            ),
            None,
        )
        if existing is None:
            return self._relationship(cls, **{relating_attribute: relating, related_attribute: related})
        current = list(getattr(existing, related_attribute) or ())
        setattr(existing, related_attribute, current + [r for r in related if r not in current])
        return existing

    def _relationship(self, cls: str, **attributes: Any) -> Any | None:
        if not self.factory.is_entity_class(cls):
            self.warn(f"{cls} does not exist in {self.factory.schema_name}; relationship skipped")
            return None
        self.counts["relationships"] += 1
        return create(
            self.model,
            cls,
            self.warn,
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner_history(),
            **attributes,
        )

    def _targets(self, prim: Any, name: str) -> list[Any]:
        relationship = prim.GetRelationship(name)
        if not relationship:
            return []
        targets = [self._entity_for_path(t.pathString) for t in relationship.GetTargets()]
        return [t for t in targets if t is not None]

    def _default_relationship(self, child: Any, parent: Any) -> str | None:
        """Decomposition for user-authored IFC prims that carry no ``ifc:parentRelationship``."""
        if parent is None:
            return None
        if util.is_a(child, "IfcFeatureElementSubtraction") and util.is_a(parent, "IfcElement"):
            return "voids"
        child_spatial = util.is_any(child, _SPATIAL)
        if child_spatial and (util.is_any(parent, _SPATIAL) or util.is_a(parent, "IfcProject")):
            return "aggregates"
        if util.is_a(child, "IfcElement") and util.is_any(parent, _SPATIAL):
            return "contains"
        if util.is_a(child, "IfcElement") and util.is_a(parent, "IfcElement"):
            return "aggregates"
        return None

    def _structure_for_new_object(self, record: PrimRecord) -> tuple[Any | None, Any | None]:
        """``(spatial container, host element)`` of a new object."""
        anchor = record.parent
        while anchor is not None and anchor.entity is None:
            anchor = anchor.parent
        host = anchor.entity if anchor is not None else None
        if host is not None and util.is_any(host, _SPATIAL):
            return host, None
        if host is not None and util.is_a(host, "IfcElement"):
            container = ifcopenshell.util.element.get_container(host)
            if container is None:  # the host may be new as well: use its own planned parent
                parent = anchor.parent
                while parent is not None and not (parent.entity is not None and util.is_any(parent.entity, _SPATIAL)):
                    parent = parent.parent
                container = parent.entity if parent is not None else self._default_spatial_container()
            return container, host if util.is_a(record.entity, "IfcElement") else None
        return self._default_spatial_container(), None

    def _default_spatial_container(self) -> Any | None:
        if self._default_container is not None:
            return self._default_container
        if self.project is None:
            return None
        for rel in util.inverse(self.project, "IsDecomposedBy"):
            for item in rel.RelatedObjects or ():
                if util.is_any(item, _SPATIAL):
                    self._default_container = item
                    return item
        for record in self.records:  # standalone: spatial prims planned under the project
            if record.entity is not None and util.is_any(record.entity, _SPATIAL):
                self._default_container = record.entity
                return record.entity
        site = create(
            self.model,
            "IfcSite",
            self.warn,
            GlobalId=self._guid("site"),
            OwnerHistory=self.owner_history(),
            Name="Default Site",
        )
        building = create(
            self.model,
            "IfcBuilding",
            self.warn,
            GlobalId=self._guid("building"),
            OwnerHistory=self.owner_history(),
            Name="Default Building",
        )
        storey = create(
            self.model,
            "IfcBuildingStorey",
            self.warn,
            GlobalId=self._guid("storey"),
            OwnerHistory=self.owner_history(),
            Name="Default Storey",
        )
        for parent, child in ((self.project, site), (site, building), (building, storey)):
            self._relationship("IfcRelAggregates", RelatingObject=parent, RelatedObjects=[child])
        for product in (site, building, storey):
            ifcopenshell.api.geometry.edit_object_placement(self.model, product=product, matrix=np.eye(4), is_si=True)
        self._default_container = storey
        return storey

    # ================================================================== phase 5

    def _write_placements(self) -> None:
        moves = self.merge and self.options.update_placements and not self._world_coords
        if self.merge and self.options.update_placements and self._world_coords:
            self.warn("The USD file was written with world coordinates; placements are not compared")
        for record in self.records:
            entity = record.entity
            if entity is None or not util.is_a(entity, "IfcProduct"):
                continue
            if record.matched and not moves:
                continue
            placement, residual = rigid(self.scene.ifc_matrix(record.prim))
            self._placements[id(record)] = placement
            if not record.matched:
                ifcopenshell.api.geometry.edit_object_placement(
                    self.model, product=entity, matrix=placement.copy(), is_si=True
                )
                continue
            if not np.allclose(residual, np.eye(4), atol=1e-6):
                self.warn(f"{record.path}: scale or shear in USD cannot be applied to an existing product's placement")
            current = self._ifc_world(entity)
            if current is None or _same_placement(current, placement):
                continue
            if util.attr(entity, "ObjectPlacement") is not None and not entity.ObjectPlacement.is_a(
                "IfcLocalPlacement"
            ):
                self.warn(f"{record.path}: {entity.ObjectPlacement.is_a()} is not changed (only local placements are)")
                continue
            ifcopenshell.api.geometry.edit_object_placement(
                self.model, product=entity, matrix=placement.copy(), is_si=True
            )
            self.counts["placementsUpdated"] += 1

    def _ifc_world(self, entity: Any) -> np.ndarray | None:
        placement = util.attr(entity, "ObjectPlacement")
        if placement is None:
            return np.eye(4)
        try:
            matrix = np.array(ifcopenshell.util.placement.get_local_placement(placement), dtype=np.float64)
        except Exception:
            return None
        matrix[:3, 3] *= self.length_scale
        return matrix

    # ================================================================== phase 6

    def _write_geometry(self) -> None:
        for record in self.records:
            entity = record.entity
            if entity is None or record.matched or not util.is_a(entity, "IfcProduct") or record.entity.Representation:
                continue
            inverse = np.linalg.inv(self._placements.get(id(record), np.eye(4)))
            shape = None
            if record.new_object:
                parts: list[MeshPart] = []
                for mesh in self.scene.mesh_prims(record.prim):
                    parts.extend(mesh_parts(self.scene, mesh, inverse @ self.scene.ifc_matrix(mesh)))
                shape = self.representations.shape(parts)
            else:
                shape = self._product_shape(record, inverse)
            if shape is not None:
                self.representations.assign(entity, shape)

    def _product_shape(self, record: PrimRecord, inverse: np.ndarray) -> Any | None:
        meshes = [g for g in record.geometry if self.scene.is_mesh(g)]
        if not meshes:
            return None
        instances = [g for g in meshes if g.IsInstance()]
        if instances and len(instances) == len(meshes):
            mapped = self.representations.mapped_shape(
                [
                    (
                        g.GetPrototype().GetPath().pathString,
                        lambda g=g: mesh_parts(self.scene, g, np.eye(4)),
                        inverse @ self.scene.ifc_matrix(g),
                    )
                    for g in instances
                ]
            )
            if mapped is not None:
                return mapped
        parts: list[MeshPart] = []
        for mesh in meshes:
            parts.extend(mesh_parts(self.scene, mesh, inverse @ self.scene.ifc_matrix(mesh)))
        return self.representations.shape(parts)

    def _write_layers(self) -> None:
        for record in self.records:
            entity = record.entity
            if entity is None or record.matched or not util.is_a(entity, "IfcPresentationLayerAssignment"):
                continue
            items = []
            for member in self._targets(record.prim, "collection:members:includes"):
                shape = util.attr(member, "Representation")
                items.extend(util.attr(shape, "Representations", ()) or () if shape is not None else ())
            if items:
                entity.AssignedItems = items
            else:
                self.warn(f"{record.path}: presentation layer without written geometry removed")
                self.model.remove(entity)
                record.entity = None

    # ================================================================== phase 7

    def _write_property_sets(self) -> None:
        extractor = PropertyExtractor(UnitResolver(self.model), include_properties=True, include_quantities=True)
        updates: list[tuple[PrimRecord, SetData, list[SetValue]]] = []
        creations: list[tuple[PrimRecord, list[SetData]]] = []
        for record in self.records:
            if record.entity is None and not self._is_set_prim(record):
                continue
            if record.matched and not self.options.update_properties:
                continue
            existing = {}
            if record.matched:
                existing = {(s.name, s.is_quantity): s for s in self._ifc_sets(extractor, record.entity)}
            sets = self._usd_sets(record, existing)
            if not sets:
                continue
            if record.matched:
                for data in sets:
                    changed = changed_values(data, existing.get((data.name, data.is_quantity)), self._entity_for_path)
                    if changed:
                        updates.append((record, data, changed))
            else:
                creations.append((record, sets))
        shared: dict[str, tuple[Any, tuple[Any, ...]]] = {}
        for record, sets in creations:
            for data in sets:
                self._create_set(record, data, shared)
        self._apply_updates(updates)
        self.counts["propertyValues"] = self.properties.values_written

    def _create_set(self, record: PrimRecord, data: SetData, shared: dict[str, tuple[Any, tuple[Any, ...]]]) -> None:
        values = [v for v in data.values if v.has_content and not v.inherited]
        if not values:
            return
        if self._is_set_prim(record):  # a property set prim (not attached to any object)
            definition = self.properties.create_set(None, _with_global_id(data, record.global_id), values)
            if definition is not None:
                record.entity = definition
                self.counts["propertySetsCreated"] += 1
            return
        entity = record.entity
        signature = _signature(values)
        if data.global_id and data.origin == "ifc" and data.global_id in shared:
            definition, existing_signature = shared[data.global_id]
            if existing_signature == signature:
                self.properties.relate(entity, definition)
                return
        definition = self.properties.create_set(entity, data, values)
        if definition is None:
            return
        self.counts["propertySetsCreated"] += 1
        if data.global_id and data.origin == "ifc":
            shared.setdefault(data.global_id, (definition, signature))
        if data.origin != "ifc" or not data.info:
            self.templates.record(definition, entity, quantity=data.is_quantity)

    def _apply_updates(self, updates: list[tuple[PrimRecord, SetData, list[SetValue]]]) -> None:
        planned: list[tuple[PrimRecord, SetData, list[SetValue], Any]] = []
        by_target: dict[int, list[tuple[Any, tuple[Any, ...]]]] = {}
        for record, data, changed in updates:
            target = self._target_set(record, data)
            planned.append((record, data, changed, target))
            if target is not None:
                by_target.setdefault(target.id(), []).append((record.entity, _signature(changed)))
        done: set[int] = set()
        for record, data, changed, target in planned:
            entity = record.entity
            if target is None:
                owner = None if self._is_set_prim(record) else entity
                definition = self.properties.create_set(owner, data, changed)
                if definition is not None:
                    self.counts["propertySetsCreated"] += 1
                    if data.origin != "ifc" or not data.info:
                        self.templates.record(definition, owner, quantity=data.is_quantity)
                continue
            owners = set() if self._is_set_prim(record) else _owners(target)
            requests = by_target[target.id()]
            same_everywhere = len({sig for _e, sig in requests}) == 1 and owners <= {e for e, _s in requests}
            if len(owners) > 1 and not same_everywhere:
                target = ifcopenshell.api.pset.unshare_pset(self.model, products=[entity], pset=target)[0]
            elif target.id() in done:
                continue
            done.add(target.id())
            self.properties.update_set(target, changed, quantity=data.is_quantity)
            self.counts["propertySetsUpdated"] += 1
            if data.origin != "ifc" or not data.info:
                self.templates.record(target, None if self._is_set_prim(record) else entity, quantity=data.is_quantity)

    def _target_set(self, record: PrimRecord, data: SetData) -> Any | None:
        """The existing definition of ``record`` a changed set belongs to, if any."""
        entity = record.entity
        if self._is_set_prim(record):
            return entity
        own = self._own_definitions(entity)
        if data.global_id:
            candidate = self._by_guid(data.global_id)
            if candidate is not None and candidate in own:
                return candidate
        quantity_class = "IfcElementQuantity"
        for definition in own:
            if util.attr(definition, "Name") == data.name and util.is_a(definition, quantity_class) == data.is_quantity:
                return definition
        return None

    def _own_definitions(self, entity: Any) -> list[Any]:
        if util.is_a(entity, "IfcTypeObject"):
            return list(util.attr(entity, "HasPropertySets", ()) or ())
        if util.is_a(entity, "IfcMaterialDefinition") or util.is_a(entity, "IfcMaterial"):
            definitions = list(util.inverse(entity, "HasProperties"))
            if not definitions:
                definitions = [i for i in self.model.get_inverse(entity) if util.is_a(i, "IfcMaterialProperties")]
            return definitions
        return occurrence_definitions(entity)

    def _ifc_sets(self, extractor: PropertyExtractor, entity: Any) -> list[PropertySet]:
        if util.is_a(entity, "IfcPropertySetDefinition"):
            converted = extractor.convert(entity)
            return [converted] if converted is not None else []
        if util.is_a(entity, "IfcMaterialDefinition") or util.is_a(entity, "IfcMaterial"):
            return extractor.for_material(entity)
        if util.is_a(entity, "IfcObjectDefinition"):
            return extractor.for_object(entity, inherit_from_type=self._inherit_type_properties)
        return []

    def _usd_sets(self, record: PrimRecord, source: dict[tuple[str, bool], PropertySet] | None = None) -> list[SetData]:
        """Every set to write for a record, merged by name.

        One property can arrive twice: as an ``ifc:pset`` attribute and from
        its native USD form (``physics:mass`` for ``IfcUSD_Physics.Mass``, a
        ``robot:`` attribute). See :meth:`_later_wins` for which is kept.
        """
        time = self.scene.time
        sets = read_ifc_sets(record.prim, time)
        if self.options.extension_namespaces:
            for prim in self._data_prims(record):
                sets += read_namespace_sets(
                    prim, self.options.extension_namespaces, time, self.factory, self.scene.meters_per_unit
                )
        if self.options.include_physics and not self._is_set_prim(record):
            physics = physics_set(self.scene, record, self.factory)
            if physics is not None:
                sets.append(physics)
        if self.options.include_trajectories and util.is_a(record.entity, "IfcProduct"):
            simulation = simulation_set(self.scene, record, self.factory)
            if simulation is not None:
                sets.append(simulation)
        merged: dict[tuple[str, bool], SetData] = {}
        for data in sets:
            key = (data.name, data.is_quantity)
            current = merged.get(key)
            if current is None:
                merged[key] = data
                continue
            source_set = (source or {}).get(key)
            source_values = (
                {tuple(v.path): v.value.value if v.value else None for v in source_set.values} if source_set else {}
            )
            by_path = {v.path: v for v in current.values}
            for value in data.values:
                previous = by_path.get(value.path)
                if previous is None or _later_wins(previous, value, source_values):
                    by_path[value.path] = value
            current.values = list(by_path.values())
            if data.origin != "ifc":
                current.origin = data.origin
        return list(merged.values())

    @staticmethod
    def _data_prims(record: PrimRecord) -> list[Any]:
        """Prims whose robot data belongs to the record: the prim, plus the representation
        prims (``Body``...) of an IFC element, where tools like Replicator often put labels."""
        return [record.prim] if record.new_object else [record.prim, *record.geometry]

    def _is_set_prim(self, record: PrimRecord) -> bool:
        return not record.new_object and util.is_subtype_name(
            self.factory.schema_name, record.ifc_class, "IfcPropertySetDefinition"
        )

    # ================================================================== helpers

    def _entity_for_path(self, path: str) -> Any | None:
        return self._entity_by_path.get(path)

    def _by_guid(self, global_id: str) -> Any | None:
        try:
            return self.model.by_guid(global_id)
        except Exception:
            return None

    def _guid(self, key: str) -> str:
        """GlobalId of a prim path within one model: a robot keeps its id across exports and runs.

        ``identity`` names the model (the source IFC checksum), not the USD
        content, so new training layers do not change the ids of known prims.
        """
        return ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{self.identity}:{key}").hex)

    def _find_not_in_usd(self) -> None:
        """Source products no prim matched (deleted, deactivated or filtered out at conversion)."""
        matched = {r.entity.id() for r in self.records if r.matched and r.entity is not None}
        self.not_in_usd = [
            {k: v for k, v in p.items() if k != "id"} for p in self._source_products if p["id"] not in matched
        ]

    def _finish_counts(self) -> None:
        self.counts["representations"] = self.representations.representations
        self.counts["triangles"] = self.representations.triangles
        self.counts["classificationReferences"] = self.classifications.references
        self.counts["templates"] = self.templates.templates
        self.counts["timeSeries"] = self.time_series.count if self.time_series is not None else 0
        self.counts["notInUsd"] = len(self.not_in_usd)


PARENTING_CLASSES = {spec[0]: spec[2] for spec in PARENTING.values()}


def _later_wins(previous: SetValue, value: SetValue, source: dict[tuple[str, ...], Any]) -> bool:
    """Whether ``value`` (a native USD form) replaces ``previous`` (an ``ifc:pset`` attribute).

    Equal values keep the attribute, which holds the exact IFC value (UsdPhysics
    stores 32-bit floats). Different values mean one side was edited: if the
    native value still equals the source IFC, the attribute was the edit and
    is kept; otherwise the native value is (the simulation wins when both changed).
    """
    if value.samples or value.targets or previous.samples or previous.targets:
        return True
    if value.same_as(previous.value):
        return False
    if value.path in source and value.same_as(source[value.path]):
        return False
    return True


def _plain(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _same_placement(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.allclose(a[:3, :3], b[:3, :3], atol=1e-7) and np.allclose(a[:3, 3], b[:3, 3], atol=1e-6))


def _signature(values: list[SetValue]) -> tuple[Any, ...]:
    return tuple(sorted((v.path, repr(v.value), v.targets, len(v.samples)) for v in values))


def _owners(definition: Any) -> set[Any]:
    try:
        return set(ifcopenshell.util.element.get_elements_by_pset(definition))
    except Exception:
        return set()


def _with_global_id(data: SetData, global_id: str | None) -> SetData:
    if global_id and not data.global_id:
        data.info = {**data.info, "globalId": global_id}
    return data


def _profile_association_matches(cls: str, target: Any) -> bool:
    if cls == "IfcRelAssociatesProfileDefinition":
        return util.is_a(target, "IfcProfileDef")
    return not util.is_a(target, "IfcProfileDef")


def _load_json(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
