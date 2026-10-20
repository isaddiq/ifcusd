"""Scene planning: where every IFC entity goes in the USD namespace.

The planner runs before anything is written. It decides, for each IFC entity
that becomes a prim, its parent, its prim type, its USD ``kind`` and its final
name. Paths are therefore known up front, which lets attributes reference
prims that are written later and keeps the output independent of geometry
processing order.

Layout::

    /World                                  Xform  kind=assembly   (defaultPrim)
      <IfcProject>                          Xform  kind=assembly
        <IfcSite>/<IfcBuilding>/<Storey>    Xform  kind=group      spatial structure
          <IfcWall>                         Xform  kind=component  element
            Body                            Mesh                   tessellated geometry
            Axis                            BasisCurves            curve representation
            <IfcOpeningElement>             Xform  kind=subcomponent
        Unassigned                          Xform  kind=group      products outside the spatial tree
      IfcData                               Scope                  non-geometric IFC data
        Types, Groups, Processes, Resources, Controls, Actors, Objects,
        Materials, Classifications, Documents, Libraries, Approvals,
        Constraints, Profiles, Layers, OwnerHistory, PropertySets,
        PropertyTemplates, Relationships/<IfcRel...>
      Looks                                 Scope                  UsdShade render materials
      Prototypes                            Scope                  shared meshes for instancing

Decomposition relationships (aggregation, nesting, spatial containment,
voiding, projection, adherence) become namespace parenting. Every other
relationship is either written as a USD relationship property on the
participating prims (types, materials, classifications, documents, groups...)
or, when it has no direct USD equivalent, as a prim of its own under
``IfcData/Relationships`` so no relationship is ever dropped.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ifcusd.ifc import util
from ifcusd.naming import UniqueNames, prim_base_name, safe_identifier
from ifcusd.options import ConversionOptions

DATA_SCOPE = "IfcData"
LOOKS_SCOPE = "Looks"
PROTOTYPES_SCOPE = "Prototypes"
UNASSIGNED = "Unassigned"

PRODUCT_KINDS = ("spatial", "element", "feature", "port")
OBJECT_KINDS = ("context", *PRODUCT_KINDS, "type", "group", "process", "resource", "control", "actor", "object")

# Non-product objects: (IFC class, node kind, IfcData container)
_OBJECT_CATEGORIES = (
    ("IfcTypeObject", "type", "Types"),
    ("IfcGroup", "group", "Groups"),
    ("IfcProcess", "process", "Processes"),
    ("IfcResource", "resource", "Resources"),
    ("IfcControl", "control", "Controls"),
    ("IfcActor", "actor", "Actors"),
)

# Resource (non-rooted) entities written as prims: (classes, node kind, container, option)
_RESOURCE_CATEGORIES = (
    (("IfcOwnerHistory",), "owner_history", "OwnerHistory", "include_owner_history"),
    (
        (
            "IfcMaterialDefinition",
            "IfcMaterialUsageDefinition",
            "IfcMaterial",
            "IfcMaterialLayer",
            "IfcMaterialLayerSet",
            "IfcMaterialLayerSetUsage",
            "IfcMaterialList",
        ),
        "material",
        "Materials",
        "include_materials",
    ),
    (("IfcClassification", "IfcClassificationReference"), "classification", "Classifications", "include_relationships"),
    (("IfcDocumentInformation", "IfcDocumentReference"), "document", "Documents", "include_relationships"),
    (("IfcLibraryInformation", "IfcLibraryReference"), "library", "Libraries", "include_relationships"),
    (("IfcApproval",), "approval", "Approvals", "include_relationships"),
    (("IfcConstraint",), "constraint", "Constraints", "include_relationships"),
    (("IfcPresentationLayerAssignment",), "layer", "Layers", "include_relationships"),
)

# Parenting relationships in priority order: (class, label, parent attribute, child attribute).
# They are read from the relationship side, so files that relate entity types the
# schema's inverse attributes do not declare (common in practice) still nest correctly.
_DECOMPOSITION = (
    ("IfcRelAggregates", "aggregates", "RelatingObject", "RelatedObjects"),
    ("IfcRelNests", "nests", "RelatingObject", "RelatedObjects"),
    ("IfcRelVoidsElement", "voids", "RelatingBuildingElement", "RelatedOpeningElement"),
    ("IfcRelProjectsElement", "projects", "RelatingElement", "RelatedFeatureElement"),
    ("IfcRelAdheresToElement", "adheres", "RelatingElement", "RelatedSurfaceFeatures"),
    ("IfcRelConnectsPortToElement", "port", "RelatedElement", "RelatingPort"),
    ("IfcRelContainedInSpatialStructure", "contains", "RelatingStructure", "RelatedElements"),
)

# Relationship classes consumed by namespace parenting when all children use them.
_PARENTING_RELATIONSHIPS = (
    "IfcRelAggregates",
    "IfcRelNests",
    "IfcRelDecomposes",
    "IfcRelContainedInSpatialStructure",
    "IfcRelVoidsElement",
    "IfcRelProjectsElement",
    "IfcRelAdheresToElement",
    "IfcRelConnectsPortToElement",
)

# Association relationship -> (relating attribute, USD relationship name on related objects)
ASSOCIATIONS = {
    "IfcRelAssociatesMaterial": ("RelatingMaterial", "ifc:hasMaterial"),
    "IfcRelAssociatesClassification": ("RelatingClassification", "ifc:hasClassification"),
    "IfcRelAssociatesDocument": ("RelatingDocument", "ifc:hasDocument"),
    "IfcRelAssociatesLibrary": ("RelatingLibrary", "ifc:hasLibrary"),
    "IfcRelAssociatesApproval": ("RelatingApproval", "ifc:hasApproval"),
    "IfcRelAssociatesConstraint": ("RelatingConstraint", "ifc:hasConstraint"),
    "IfcRelAssociatesProfileDefinition": ("RelatingProfileDefinition", "ifc:hasProfile"),
    "IfcRelAssociatesProfileProperties": ("RelatingProfileProperties", "ifc:hasProfile"),
}

_RANK = {
    "context": 0,
    "spatial": 1,
    "element": 2,
    "feature": 3,
    "port": 4,
    "container": 9,
}


@dataclass(eq=False)
class Node:
    """A planned prim."""

    kind: str
    entity: Any = None
    base_name: str = ""
    display_name: str | None = None
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    parent_relationship: str | None = None
    parent_relationship_id: int | None = None
    prim_type: str = "Xform"
    usd_kind: str | None = None
    name: str = ""
    path: str = ""
    sort_key: tuple[Any, ...] = ()

    @property
    def step_id(self) -> int | None:
        return util.step_id(self.entity) if self.entity is not None else None

    @property
    def ifc_class(self) -> str | None:
        return util.ifc_class(self.entity) if self.entity is not None else None

    def add(self, child: Node) -> Node:
        child.parent = self
        self.children.append(child)
        return child

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class ScenePlan:
    root: Node
    data_scope: Node | None
    by_id: dict[int, Node]
    consumed_relationships: set[int]
    generic_relationships: list[Node]
    skipped_relationships: set[int]
    filtered_products: list[Any]
    projects: list[Node]

    def node_for(self, entity: Any) -> Node | None:
        identifier = util.step_id(entity)
        return self.by_id.get(identifier) if identifier is not None else None

    def path_for(self, entity: Any) -> str | None:
        node = self.node_for(entity)
        return node.path if node is not None else None

    def nodes(self) -> Iterator[Node]:
        return self.root.walk()


class _Planner:
    def __init__(self, model: Any, options: ConversionOptions) -> None:
        self.model = model
        self.options = options
        self.by_id: dict[int, Node] = {}
        self.root = Node(kind="root", base_name=options.root_prim_name, prim_type="Xform", usd_kind="assembly")
        self.root.name = options.root_prim_name
        self.data_scope: Node | None = None
        self._containers: dict[tuple[int, str], Node] = {}
        self.filtered_products: list[Any] = []
        self.parent_rel_children: dict[int, list[int]] = {}

    # ------------------------------------------------------------------ helpers

    def container(self, parent: Node, name: str, *, prim_type: str = "Scope", usd_kind: str | None = None) -> Node:
        key = (id(parent), name)
        node = self._containers.get(key)
        if node is None:
            node = Node(kind="container", base_name=name, prim_type=prim_type, usd_kind=usd_kind)
            node.sort_key = (_RANK["container"], name)
            parent.add(node)
            self._containers[key] = node
        return node

    def data(self, category: str) -> Node:
        if self.data_scope is None:
            self.data_scope = self.container(self.root, DATA_SCOPE)
        return self.container(self.data_scope, category)

    def _passes_filter(self, entity: Any) -> bool:
        options = self.options
        if options.include_types and not util.is_any(entity, options.include_types):
            return False
        if options.exclude_types and util.is_any(entity, options.exclude_types):
            return False
        return True

    # ------------------------------------------------------------------ objects

    def classify(self, entity: Any) -> str | None:
        if util.is_a(entity, "IfcContext") or util.is_a(entity, "IfcProject"):
            return "context"
        if util.is_a(entity, "IfcProduct"):
            if util.is_a(entity, "IfcSpatialElement") or util.is_a(entity, "IfcSpatialStructureElement"):
                return "spatial"
            if util.is_a(entity, "IfcFeatureElement"):
                return "feature"
            if util.is_a(entity, "IfcPort"):
                return "port"
            return "element"
        for ifc_class, kind, _container in _OBJECT_CATEGORIES:
            if util.is_a(entity, ifc_class):
                return kind
        if util.is_a(entity, "IfcObjectDefinition"):
            return "object"
        return None

    def plan_objects(self) -> None:
        objects = sorted(util.by_type(self.model, "IfcObjectDefinition"), key=lambda e: e.id())
        for entity in objects:
            kind = self.classify(entity)
            if kind is None:
                continue
            if kind in ("element", "feature", "port") and not self._passes_filter(entity):
                self.filtered_products.append(entity)
                continue
            if not self.options.include_metadata and kind not in ("context", *PRODUCT_KINDS):
                continue
            node = Node(kind=kind, entity=entity, prim_type="Xform" if kind in ("context", *PRODUCT_KINDS) else "Scope")
            node.display_name = _display_name(entity)
            node.base_name = prim_base_name(
                ifc_class=util.ifc_class(entity),
                name=node.display_name,
                global_id=util.attr(entity, "GlobalId"),
                step_id=util.step_id(entity),
                mode=self.options.naming,
            )
            node.sort_key = _sort_key(entity, kind)
            self.by_id[entity.id()] = node

        # Resolve decomposition parents, then attach (cycle-safe).
        candidates = self._parent_candidates()
        raw_parent: dict[int, tuple[Node, str, int | None]] = {}
        for identifier, node in self.by_id.items():
            for _priority, _rel_id, parent, label, rel_id in sorted(
                candidates.get(identifier, ()), key=lambda c: c[:2]
            ):
                if _compatible(node.kind, parent.kind):
                    raw_parent[identifier] = (parent, label, rel_id)
                    break

        for identifier in sorted(self.by_id):
            node = self.by_id[identifier]
            parent_info = raw_parent.get(identifier)
            if parent_info is not None and not self._creates_cycle(identifier, raw_parent):
                parent, relation, rel_id = parent_info
                node.parent_relationship = relation
                node.parent_relationship_id = rel_id
                parent.add(node)
                if rel_id is not None:
                    self.parent_rel_children.setdefault(rel_id, []).append(identifier)
            else:
                raw_parent.pop(identifier, None)

        self.projects = [n for i, n in sorted(self.by_id.items()) if n.kind == "context" and n.parent is None]
        primary = next((n for n in self.projects if util.is_a(n.entity, "IfcProject")), None)
        for identifier in sorted(self.by_id):
            node = self.by_id[identifier]
            if node.parent is not None:
                continue
            if node.kind == "context":
                self.root.add(node)
                node.parent_relationship = "root"
            elif node.kind in PRODUCT_KINDS:
                anchor = primary or self.root
                self.container(anchor, UNASSIGNED, prim_type="Xform", usd_kind="group").add(node)
                node.parent_relationship = "unassigned"
            else:
                category = next((c for cls, k, c in _OBJECT_CATEGORIES if k == node.kind), "Objects")
                self.data(category).add(node)
                node.parent_relationship = "none"

    def _parent_candidates(self) -> dict[int, list[tuple[int, int, Node, str, int]]]:
        """``child id -> [(priority, rel id, parent node, label, rel id)]`` from all parenting relationships."""
        candidates: dict[int, list[tuple[int, int, Node, str, int]]] = {}
        for priority, (rel_class, label, parent_attribute, child_attribute) in enumerate(_DECOMPOSITION):
            for rel in util.by_type(self.model, rel_class, include_subtypes=False):
                parent_entity = util.attr(rel, parent_attribute)
                parent = self.by_id.get(parent_entity.id()) if parent_entity is not None else None
                if parent is None:
                    continue
                children = util.attr(rel, child_attribute)
                if children is None:
                    continue
                for child in children if isinstance(children, (list, tuple)) else (children,):
                    if child is None or child.id() == parent_entity.id():
                        continue
                    candidates.setdefault(child.id(), []).append((priority, rel.id(), parent, label, rel.id()))
        return candidates

    def _creates_cycle(self, identifier: int, raw_parent: dict[int, tuple[Node, str, int | None]]) -> bool:
        seen = {identifier}
        current = raw_parent.get(identifier)
        while current is not None:
            parent_id = current[0].step_id
            if parent_id is None:
                return False
            if parent_id in seen:
                return True
            seen.add(parent_id)
            current = raw_parent.get(parent_id)
        return False

    # ------------------------------------------------------------------ resources

    def plan_resources(self) -> None:
        if not self.options.include_metadata:
            return
        for classes, kind, category, option_name in _RESOURCE_CATEGORIES:
            if not getattr(self.options, option_name):
                continue
            entities: dict[int, Any] = {}
            for ifc_class in classes:
                for entity in util.by_type(self.model, ifc_class):
                    entities.setdefault(entity.id(), entity)
            for identifier in sorted(entities):
                if identifier in self.by_id:
                    continue
                entity = entities[identifier]
                node = Node(kind=kind, entity=entity, prim_type="Scope")
                label = _resource_label(entity)
                node.display_name = label
                node.base_name = prim_base_name(
                    ifc_class=util.ifc_class(entity),
                    name=label,
                    global_id=None,
                    step_id=identifier,
                    mode=self.options.naming,
                )
                node.sort_key = (util.ifc_class(entity), label or "", identifier)
                self.data(category).add(node)
                node.parent_relationship = "none"
                self.by_id[identifier] = node

        if self.options.include_relationships:
            for rel_class in ("IfcRelAssociatesProfileDefinition", "IfcRelAssociatesProfileProperties"):
                for rel in util.by_type(self.model, rel_class):
                    profile = util.attr(rel, ASSOCIATIONS[rel_class][0])
                    if profile is None or profile.id() in self.by_id:
                        continue
                    node = Node(kind="profile", entity=profile, prim_type="Scope")
                    label = _resource_label(profile)
                    node.display_name = label
                    node.base_name = prim_base_name(
                        ifc_class=util.ifc_class(profile),
                        name=label,
                        global_id=None,
                        step_id=profile.id(),
                        mode=self.options.naming,
                    )
                    node.sort_key = (util.ifc_class(profile), label or "", profile.id())
                    self.data("Profiles").add(node)
                    self.by_id[profile.id()] = node

    def plan_property_definitions(self) -> None:
        """Property sets that are not attached to any object, and property templates."""
        if not self.options.include_metadata or not (
            self.options.include_properties or self.options.include_quantities
        ):
            return
        attached: set[int] = set()
        for rel in util.by_type(self.model, "IfcRelDefinesByProperties"):
            relating = util.attr(rel, "RelatingPropertyDefinition")
            for definition in relating if isinstance(relating, (list, tuple)) else (relating,):
                if definition is not None:
                    attached.add(definition.id())
        for type_object in util.by_type(self.model, "IfcTypeObject"):
            for definition in util.attr(type_object, "HasPropertySets", ()) or ():
                attached.add(definition.id())
        for definition in sorted(util.by_type(self.model, "IfcPropertySetDefinition"), key=lambda e: e.id()):
            if definition.id() in attached or definition.id() in self.by_id:
                continue
            if util.is_a(definition, "IfcElementQuantity") and not self.options.include_quantities:
                continue
            if not util.is_a(definition, "IfcElementQuantity") and not self.options.include_properties:
                continue
            self._data_node(definition, "pset", "PropertySets")
        for template in sorted(util.by_type(self.model, "IfcPropertyTemplateDefinition"), key=lambda e: e.id()):
            if template.id() not in self.by_id:
                self._data_node(template, "template", "PropertyTemplates")

    def _data_node(self, entity: Any, kind: str, category: str) -> Node:
        node = Node(kind=kind, entity=entity, prim_type="Scope")
        node.display_name = _display_name(entity)
        node.base_name = prim_base_name(
            ifc_class=util.ifc_class(entity),
            name=node.display_name,
            global_id=util.attr(entity, "GlobalId"),
            step_id=entity.id(),
            mode=self.options.naming,
        )
        node.sort_key = (util.ifc_class(entity), node.display_name or "", entity.id())
        self.data(category).add(node)
        node.parent_relationship = "none"
        self.by_id[entity.id()] = node
        return node

    # ------------------------------------------------------------------ relationships

    def plan_relationships(self) -> tuple[set[int], list[Node], set[int]]:
        consumed: set[int] = set()
        skipped: set[int] = set()
        generic: list[Node] = []
        if not self.options.include_metadata:
            return consumed, generic, skipped
        options = self.options
        all_prims = options.relationship_prims == "all"
        for rel in sorted(util.by_type(self.model, "IfcRelationship"), key=lambda e: e.id()):
            cls = rel.is_a()
            identifier = rel.id()
            status = self._relationship_status(rel)
            if status == "consumed":
                consumed.add(identifier)
                if not (all_prims and options.include_relationships):
                    continue
            elif status == "skipped" or not options.include_relationships:
                skipped.add(identifier)
                continue
            node = Node(kind="relationship", entity=rel, prim_type="Scope")
            node.display_name = _display_name(rel)
            node.base_name = prim_base_name(
                ifc_class=cls,
                name=node.display_name,
                global_id=util.attr(rel, "GlobalId"),
                step_id=identifier,
                mode=options.naming,
            )
            node.sort_key = (cls, node.display_name or "", util.attr(rel, "GlobalId") or "", identifier)
            relationships = self.data("Relationships")
            self.container(relationships, cls).add(node)
            node.parent_relationship = "none"
            self.by_id[identifier] = node
            if status != "consumed":
                generic.append(node)
        return consumed, generic, skipped

    def _relationship_status(self, rel: Any) -> str:
        """``consumed`` (expressed natively), ``skipped`` (data group disabled) or ``generic``."""
        options = self.options
        cls = rel.is_a()
        if util.is_any(rel, _PARENTING_RELATIONSHIPS) and self._parenting_consumed(rel):
            return "consumed"
        if cls == "IfcRelDefinesByProperties":
            return "consumed" if options.include_properties or options.include_quantities else "skipped"
        if cls == "IfcRelDefinesByType" and self.by_id.get(_id(util.attr(rel, "RelatingType"))) is not None:
            return "consumed"
        if cls == "IfcRelAssignsToGroup" and self.by_id.get(_id(util.attr(rel, "RelatingGroup"))) is not None:
            return "consumed"
        if cls == "IfcRelFillsElement":
            opening = self.by_id.get(_id(util.attr(rel, "RelatingOpeningElement")))
            filler = self.by_id.get(_id(util.attr(rel, "RelatedBuildingElement")))
            if opening is not None and filler is not None:
                return "consumed"
        if cls in ASSOCIATIONS:
            if cls == "IfcRelAssociatesMaterial" and not options.include_materials:
                return "skipped"
            if self.by_id.get(_id(util.attr(rel, ASSOCIATIONS[cls][0]))) is not None:
                return "consumed"
        return "generic"

    def _parenting_consumed(self, rel: Any) -> bool:
        child_attribute = next((child for cls, _label, _parent, child in _DECOMPOSITION if cls == rel.is_a()), None)
        if child_attribute is None:  # e.g. an IFC2X3 IfcRelDecomposes subtype we do not parent by
            return False
        related = util.attr(rel, child_attribute)
        related = related if isinstance(related, (list, tuple)) else ((related,) if related is not None else ())
        children = set(self.parent_rel_children.get(rel.id(), ()))
        related_ids = {_id(item) for item in related if item is not None}
        related_ids.discard(None)
        # Related objects removed by element filters are not expected to be parented.
        filtered = {item.id() for item in self.filtered_products}
        expected = {identifier for identifier in related_ids if identifier not in filtered}
        return bool(expected) and expected <= children

    # ------------------------------------------------------------------ finishing

    def finish(self) -> None:
        self._assign_kinds(self.root)
        self._sort_and_name(self.root, "")

    def _assign_kinds(self, node: Node) -> None:
        for child in node.children:
            self._assign_kinds(child)
        if node.kind == "context":
            node.usd_kind = "assembly"
        elif node.kind == "spatial":
            node.usd_kind = "group"
        elif node.kind == "element":
            has_models = any(c.kind in ("element", "spatial") for c in node.children)
            node.usd_kind = "assembly" if has_models else "component"
        elif node.kind in ("feature", "port"):
            node.usd_kind = "subcomponent"
        if node.kind in PRODUCT_KINDS or node.kind == "context":
            node.prim_type = "Xform"

    def _sort_and_name(self, node: Node, parent_path: str) -> None:
        node.path = f"{parent_path}/{node.name}"
        names = UniqueNames()
        node.children.sort(key=lambda child: child.sort_key)
        for child in node.children:
            child.name = names.claim(child.base_name or safe_identifier(child.kind))
        for child in node.children:
            self._sort_and_name(child, node.path)


def build_plan(model: Any, options: ConversionOptions) -> ScenePlan:
    planner = _Planner(model, options)
    planner.plan_objects()
    planner.plan_resources()
    planner.plan_property_definitions()
    consumed, generic, skipped = planner.plan_relationships()
    planner.finish()
    return ScenePlan(
        root=planner.root,
        data_scope=planner.data_scope,
        by_id=planner.by_id,
        consumed_relationships=consumed,
        generic_relationships=generic,
        skipped_relationships=skipped,
        filtered_products=planner.filtered_products,
        projects=planner.projects,
    )


# ---------------------------------------------------------------------- helpers


def _compatible(child_kind: str, parent_kind: str) -> bool:
    if child_kind in PRODUCT_KINDS:
        return parent_kind in ("context", *PRODUCT_KINDS)
    if child_kind == "context":
        return False
    return child_kind == parent_kind


def _id(entity: Any) -> int | None:
    return util.step_id(entity) if entity is not None else None


def _display_name(entity: Any) -> str | None:
    for attribute in ("Name", "LongName"):
        value = util.attr(entity, attribute)
        if value:
            return str(value)
    return None


def _resource_label(entity: Any) -> str | None:
    for attribute in ("Name", "LayerSetName", "Identification", "ItemReference", "Location", "Description"):
        value = util.attr(entity, attribute)
        if value and isinstance(value, str):
            return value
    if util.is_a(entity, "IfcOwnerHistory"):
        return "OwnerHistory"
    return None


def _sort_key(entity: Any, kind: str) -> tuple[Any, ...]:
    rank = _RANK.get(kind, 5)
    name = _display_name(entity) or ""
    guid = util.attr(entity, "GlobalId") or ""
    if util.is_a(entity, "IfcBuildingStorey"):
        elevation = util.attr(entity, "Elevation")
        try:
            elevation_value = float(elevation) if elevation is not None else 0.0
        except (TypeError, ValueError):
            elevation_value = 0.0
        return (rank, "", elevation_value, name, guid, entity.id())
    return (rank, util.ifc_class(entity), 0.0, name, guid, entity.id())
