"""Writes a planned IFC scene into a USD layer.

Three phases keep the output deterministic regardless of geometry threading:

1. :meth:`SceneWriter.write_semantics` writes every planned prim with its IFC
   attributes, property sets and relationships, in plan order.
2. :meth:`SceneWriter.add_shape` receives tessellated shapes in whatever order
   the geometry threads produce them and writes them to a scratch layer.
3. :meth:`SceneWriter.finalize` copies geometry, prototypes and materials into
   the output layer in sorted order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import ifcopenshell.util.placement
import numpy as np

from ifcusd import schema
from ifcusd.ifc import util
from ifcusd.ifc.geometry import ShapeData
from ifcusd.ifc.properties import PropertyExtractor, PropertySet, object_type
from ifcusd.ifc.structure import (
    ASSOCIATIONS,
    LOOKS_SCOPE,
    OBJECT_KINDS,
    PRODUCT_KINDS,
    PROTOTYPES_SCOPE,
    Node,
    ScenePlan,
)
from ifcusd.ifc.styles import MaterialSpec, StyleLibrary
from ifcusd.ifc.units import UnitResolver
from ifcusd.ifc.values import jsonable, to_json, to_usd_value
from ifcusd.naming import UniqueNames, lower_first, safe_identifier
from ifcusd.options import ConversionOptions
from ifcusd.usd.geometry import write_curves, write_mesh
from ifcusd.usd.materials import write_material
from ifcusd.usd.sdf import Gf, LayerWriter, Sdf, matrix_from_rows, set_display_name, usd_dictionary

log = logging.getLogger(__name__)

# Explicit attributes that are represented by other USD constructs.
_SKIPPED_ATTRIBUTES = {
    "ObjectPlacement",  # -> xformOp:transform
    "Representation",  # -> Body/Axis geometry prims
    "RepresentationMaps",  # type geometry, realised through occurrences
    "HasPropertySets",  # -> ifc:pset / ifc:qto namespaces
    "RepresentationContexts",
    "UnitsInContext",  # -> ifc:units:* on the root prim
}
_SKIPPED_BY_CLASS = {
    "IfcPresentationLayerAssignment": {"AssignedItems"},  # -> collection:members
    "IfcPresentationLayerWithStyle": {"AssignedItems", "LayerStyles"},
    "IfcRelDefinesByProperties": {"RelatingPropertyDefinition"},
}
_HIDDEN_GEOMETRY = ("IfcSpace", "IfcSpatialZone", "IfcOpeningElement", "IfcVirtualElement", "IfcExternalSpatialElement")
_SEMANTIC_KINDS = ("element", "feature", "port")


@dataclass
class WriteStats:
    prims: int = 0
    entity_prims: int = 0
    property_sets: int = 0
    properties: int = 0
    quantities: int = 0
    relationships: int = 0
    meshes: int = 0
    curves: int = 0
    instances: int = 0
    prototypes: int = 0
    materials: int = 0
    points: int = 0
    triangles: int = 0
    shapes_without_prim: int = 0
    products_with_geometry: set[int] = field(default_factory=set)
    geometry_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _GeometryRecord:
    body_path: str
    instance_xform: Any | None
    hidden: bool
    promoted: str | None = None


class SceneWriter:
    def __init__(
        self,
        layer: Any,
        plan: ScenePlan,
        model: Any,
        options: ConversionOptions,
        units: UnitResolver,
        styles: StyleLibrary,
        *,
        sidecar: list[dict[str, Any]] | None = None,
    ) -> None:
        self.layer = layer
        self.writer = LayerWriter(layer)
        self.plan = plan
        self.model = model
        self.options = options
        self.units = units
        self.styles = styles
        self.scale = options.output_scale
        self.stats = WriteStats()
        self.extractor = PropertyExtractor(
            units,
            include_properties=options.include_properties and options.include_metadata,
            include_quantities=options.include_quantities and options.include_metadata,
        )
        self.root_path = plan.root.path
        self.looks_path = f"{self.root_path}/{LOOKS_SCOPE}"
        self.prototypes_path = f"{self.root_path}/{PROTOTYPES_SCOPE}"
        self._scratch = Sdf.Layer.CreateAnonymous("ifcusd_geometry")
        self._scratch_writer = LayerWriter(self._scratch)
        self._world: dict[int, np.ndarray] = {}
        self._geometry_children: dict[str, UniqueNames] = {}
        self._geometry_paths: list[str] = []
        self._geometry_first: dict[tuple[str, str], _GeometryRecord] = {}
        self._prototypes: list[str] = []
        self._curve_paths: dict[str, int] = {}
        self._products_with_mesh: set[int] = set()
        self._used_materials: dict[str, MaterialSpec] = {}
        self.sidecar = sidecar

    # ================================================================== phase 1

    def write_stage(
        self, *, file_info: dict[str, Any], georeference: dict[str, Any], layer_data: dict[str, Any]
    ) -> None:
        layer = self.layer
        layer.defaultPrim = self.plan.root.name
        root_layer = layer.pseudoRoot
        root_layer.SetInfo("upAxis", self.options.up_axis)
        root_layer.SetInfo("metersPerUnit", float(self.options.output_meters_per_unit))
        layer.customLayerData = {"ifcusd": usd_dictionary(layer_data)}
        layer.documentation = (
            f"Converted from {file_info.get('sourceName', 'IFC')} "
            f"({file_info.get('schema', '')}) by IfcUSD {layer_data.get('converterVersion', '')}"
        )

        apis = [schema.FILE_API] if self.options.include_metadata else []
        if georeference and self.options.include_metadata:
            apis.append(schema.GEOREFERENCE_API)
        root = self.writer.prim(self.root_path, "Xform", kind="assembly", api_schemas=apis)
        self.stats.prims += 1
        if self.options.up_axis == "Y":
            self.writer.rotate_x(root, -90.0)  # IFC is Z-up
        if not self.options.include_metadata:
            return
        w = self.writer
        w.attribute(root, "ifc:file:schema", "token", file_info["schema"], display_group="IFC File")
        for key in (
            "schemaIdentifier",
            "compatibilitySchema",
            "name",
            "timeStamp",
            "preprocessorVersion",
            "originatingSystem",
            "authorization",
            "implementationLevel",
            "sha256",
        ):
            value = file_info.get(key)
            if value:
                w.attribute(root, f"ifc:file:{key}", "string", str(value), display_group="IFC File")
        for key in ("author", "organization", "description"):
            values = [v for v in file_info.get(key, []) if v]
            if values:
                w.attribute(root, f"ifc:file:{key}", "string[]", values, display_group="IFC File")
        w.attribute(root, "ifc:units:lengthScale", "double", float(self.units.length_scale), display_group="IFC Units")
        for unit_type, label in self.units.project_units().items():
            w.attribute(
                root, f"ifc:units:{safe_identifier(unit_type)}", "string", label, custom=True, display_group="IFC Units"
            )
        for key, value in sorted(georeference.items()):
            name = f"ifc:geo:{key}"
            if key == "trueNorth":
                w.attribute(root, name, "double2", Gf.Vec2d(*value), display_group="IFC Georeference")
            elif key == "conversionClass":
                w.attribute(root, name, "token", str(value), display_group="IFC Georeference")
            elif isinstance(value, float):
                w.attribute(root, name, "double", value, display_group="IFC Georeference")
            else:
                w.attribute(root, name, "string", str(value), display_group="IFC Georeference")

    def write_semantics(self) -> None:
        for node in self.plan.root.children:
            self._write_node(node, parent_world=np.eye(4))

    def _write_node(self, node: Node, parent_world: np.ndarray) -> None:
        apis: list[str] = []
        entity = node.entity
        if entity is not None and self.options.include_metadata:
            apis.append(schema.ENTITY_API)
        spec = self.writer.prim(node.path, node.prim_type, kind=node.usd_kind, api_schemas=apis)
        self.stats.prims += 1
        if node.display_name and node.display_name != node.name:
            set_display_name(spec, node.display_name)

        world = parent_world
        if entity is not None and node.kind in PRODUCT_KINDS:
            world = self._product_world(entity, parent_world)
            local = np.linalg.inv(parent_world) @ world
            if not np.allclose(local, np.eye(4), atol=1e-12):
                self.writer.transform(spec, _gf_matrix(local))
        if entity is not None:
            self._world[entity.id()] = world

        if entity is not None and self.options.include_metadata:
            self.stats.entity_prims += 1
            self._write_entity(spec, node)
        elif entity is not None and node.kind in PRODUCT_KINDS and node.kind != "spatial":
            self._write_semantic_labels(spec, node)

        for child in node.children:
            self._write_node(child, world)

    def _product_world(self, entity: Any, parent_world: np.ndarray) -> np.ndarray:
        if self.options.use_world_coords:
            return np.eye(4)
        placement = util.attr(entity, "ObjectPlacement")
        if placement is None:
            return parent_world
        try:
            from ifcusd.ifc.geometry import native_stdout_silenced

            with native_stdout_silenced():
                matrix = np.array(ifcopenshell.util.placement.get_local_placement(placement), dtype=np.float64)
        except Exception as exc:
            log.debug("placement of #%s not resolved: %s", entity.id(), exc)
            return parent_world
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            return parent_world
        matrix[:3, 3] *= self.units.length_scale * self.scale
        return matrix

    # ------------------------------------------------------------------ entity data

    def _write_entity(self, spec: Any, node: Node) -> None:
        entity = node.entity
        w = self.writer
        cls = util.ifc_class(entity)
        w.attribute(spec, "ifc:class", "token", cls, display_group="IFC", display_name="Class")
        step = util.step_id(entity)
        if step is not None:
            w.attribute(spec, "ifc:stepId", "int", step, display_group="IFC", display_name="STEP id")
        if node.parent_relationship:
            w.attribute(spec, "ifc:parentRelationship", "token", node.parent_relationship, display_group="IFC")
        self._write_explicit_attributes(spec, entity)

        if node.kind in OBJECT_KINDS:
            self._write_object_relationships(spec, node)
            if self.extractor.include_properties or self.extractor.include_quantities:
                sets = self.extractor.for_object(entity, inherit_from_type=self.options.inherit_type_properties)
                self._write_property_sets(spec, sets)
                if self.sidecar is not None:
                    self._sidecar_entry(node, sets)
            elif self.sidecar is not None:
                self._sidecar_entry(node, [])
        elif node.kind == "material":
            self._write_material_extras(spec, entity)
        elif node.kind == "pset":
            converted = self.extractor.convert(entity)
            if converted is not None:
                self._write_property_sets(spec, [converted])
        elif node.kind == "layer":
            self._write_layer_members(spec, entity)
        elif node.kind == "relationship" and util.is_a(entity, "IfcRelDefinesByProperties"):
            # Property sets are attributes, not prims: identify them by GlobalId.
            relating = util.attr(entity, "RelatingPropertyDefinition")
            definitions = relating if isinstance(relating, (list, tuple)) else (relating,)
            ids = [str(util.attr(d, "GlobalId")) for d in definitions if d is not None and util.attr(d, "GlobalId")]
            if ids:
                w.attribute(
                    spec,
                    "ifc:relatingPropertyDefinition",
                    "string[]",
                    ids,
                    custom=True,
                    display_name="RelatingPropertyDefinition",
                    display_group="IFC",
                    custom_data={"ifcEncoding": "globalId"},
                )
        if node.kind in _SEMANTIC_KINDS:
            self._write_semantic_labels(spec, node)

    def _write_explicit_attributes(self, spec: Any, entity: Any) -> None:
        w = self.writer
        cls = util.ifc_class(entity)
        skipped = _SKIPPED_BY_CLASS.get(cls, set())
        schema_name = util.entity_schema(entity)
        for name, kind, value in util.explicit_attributes(entity):
            if value is None or name in _SKIPPED_ATTRIBUTES or name in skipped:
                continue
            prop = f"ifc:{lower_first(name)}"
            custom = prop not in schema.ENTITY_ATTRIBUTES and prop not in schema.ENTITY_RELATIONSHIPS
            if util.is_entity(value) or (
                isinstance(value, (list, tuple)) and value and all(util.is_entity(v) for v in value)
            ):
                targets = list(value) if isinstance(value, (list, tuple)) else [value]
                paths = [self.plan.path_for(t) for t in targets]
                if all(paths):
                    w.relationship(spec, prop, paths, custom=custom, display_name=name, display_group="IFC")
                    self.stats.relationships += 1
                    continue
                w.attribute(
                    spec,
                    f"ifc:{lower_first(name)}",
                    "string",
                    to_json(jsonable(value)),
                    custom=True,
                    display_name=name,
                    display_group="IFC",
                    custom_data={"ifcEncoding": "json"},
                )
                continue
            usd_value = to_usd_value(value, enum=(kind == "enum"))
            if usd_value is None:
                continue
            type_name = usd_value.type_name
            expected = schema.ENTITY_ATTRIBUTES.get(prop)
            if expected and expected != type_name:
                # Keep the schema's declared type; anything else goes in a sibling attribute.
                if expected == "string" and type_name in ("token",):
                    type_name = "string"
                elif expected == "token" and type_name == "string":
                    type_name = "token"
                else:
                    prop = f"{prop}Value"
                    custom = True
            data: dict[str, Any] = {}
            defined = usd_value.value_type or util.declared_type(schema_name, cls, name)
            if defined:
                data["ifcValueType"] = defined
                unit = self.units.label_for_measure(defined)
                if unit:
                    data["ifcUnit"] = unit
            if usd_value.encoding:
                data["ifcEncoding"] = usd_value.encoding
            allowed = util.enum_items(schema_name, cls, name) if kind == "enum" else ()
            w.attribute(
                spec,
                prop,
                type_name,
                usd_value.value,
                custom=custom,
                display_name=name,
                display_group="IFC",
                custom_data=data or None,
                allowed_tokens=allowed or None,
            )

    def _write_object_relationships(self, spec: Any, node: Node) -> None:
        entity = node.entity
        consumed = self.plan.consumed_relationships
        targets: dict[str, list[str]] = {}
        type_object = object_type(entity)
        if type_object is not None:
            path = self.plan.path_for(type_object)
            if path:
                targets.setdefault("ifc:isTypedBy", []).append(path)
        for rel in util.inverse(entity, "HasAssociations"):
            cls = util.ifc_class(rel)
            if cls not in ASSOCIATIONS or rel.id() not in consumed:
                continue
            relating = util.attr(rel, ASSOCIATIONS[cls][0])
            path = self.plan.path_for(relating) if relating is not None else None
            if path:
                targets.setdefault(ASSOCIATIONS[cls][1], []).append(path)
        for rel in util.inverse(entity, "FillsVoids"):
            if rel.id() in consumed:
                path = self.plan.path_for(util.attr(rel, "RelatingOpeningElement"))
                if path:
                    targets.setdefault("ifc:fills", []).append(path)
        for name in sorted(targets):
            self.writer.relationship(spec, name, targets[name], display_group="IFC Relationships")
            self.stats.relationships += 1
        if node.kind == "group":
            members: list[str] = []
            for rel in util.inverse(entity, "IsGroupedBy"):
                if rel.id() not in consumed:
                    continue
                for member in util.attr(rel, "RelatedObjects", ()) or ():
                    path = self.plan.path_for(member)
                    if path:
                        members.append(path)
            self._collection(spec, "members", members)

    def _collection(self, spec: Any, name: str, members: list[str]) -> None:
        self.writer.add_api_schemas(spec, [f"CollectionAPI:{name}"])
        self.writer.attribute(spec, f"collection:{name}:expansionRule", "token", "expandPrims")
        self.writer.relationship(spec, f"collection:{name}:includes", members)
        self.stats.relationships += 1

    def _write_layer_members(self, spec: Any, layer: Any) -> None:
        members: list[str] = []
        for item in util.attr(layer, "AssignedItems", ()) or ():
            for product in _products_of_representation_item(self.model, item):
                path = self.plan.path_for(product)
                if path and path not in members:
                    members.append(path)
        self._collection(spec, "members", members)

    def _write_material_extras(self, spec: Any, material: Any) -> None:
        if self.extractor.include_properties:
            self._write_property_sets(spec, self.extractor.for_material(material))
        looks: list[str] = []
        for representation in util.inverse(material, "HasRepresentation"):
            for item in util.attr(representation, "Representations", ()) or ():
                for styled in util.attr(item, "Items", ()) or ():
                    for style in _surface_styles(styled):
                        material_spec = self.styles.spec_for_style_entity(style)
                        if material_spec is not None:
                            self._used_materials[material_spec.name] = material_spec
                            looks.append(f"{self.looks_path}/{material_spec.name}")
        if looks:
            self.writer.relationship(spec, "ifc:surfaceStyle", looks, custom=True, display_group="IFC")

    def _write_semantic_labels(self, spec: Any, node: Node) -> None:
        if not self.options.semantic_labels or node.entity is None:
            return
        cls = util.ifc_class(node.entity)
        self.writer.add_api_schemas(spec, ["SemanticsLabelsAPI:class"])
        self.writer.attribute(spec, "semantics:labels:class", "token[]", [cls])
        if self.options.legacy_semantics:
            self.writer.add_api_schemas(spec, ["SemanticsAPI:Semantics"])
            self.writer.attribute(spec, "semantic:Semantics:params:semanticType", "string", "class", custom=True)
            self.writer.attribute(spec, "semantic:Semantics:params:semanticData", "string", cls, custom=True)

    # ------------------------------------------------------------------ property sets

    def _write_property_sets(self, spec: Any, sets: list[PropertySet]) -> None:
        if not sets:
            return
        names = {schema.PSET_NAMESPACE: UniqueNames(), schema.QTO_NAMESPACE: UniqueNames()}
        apis: list[str] = []
        info: dict[str, Any] = {}
        w = self.writer
        for pset in sets:
            namespace = schema.QTO_NAMESPACE if pset.is_quantity else schema.PSET_NAMESPACE
            ident = names[namespace].claim(safe_identifier(pset.name, "PropertySet"))
            api = schema.QUANTITY_SET_API if pset.is_quantity else schema.PROPERTY_SET_API
            apis.append(f"{api}:{ident}")
            entry: dict[str, Any] = {"name": pset.name, "class": pset.ifc_class, "source": pset.source}
            if pset.global_id:
                entry["globalId"] = pset.global_id
            if pset.description:
                entry["description"] = str(pset.description)
            entry.update({k: v for k, v in pset.extra.items() if v is not None})
            info.setdefault("quantitySets" if pset.is_quantity else "propertySets", {})[ident] = entry
            self.stats.property_sets += 1
            for value in pset.values:
                name = f"{namespace}:{ident}:{':'.join(value.path)}"
                if value.targets:
                    paths = [self.plan.path_for(t) for t in value.targets]
                    if all(paths):
                        w.relationship(
                            spec,
                            name,
                            paths,
                            custom=True,
                            display_name=value.display_name,
                            display_group=pset.name,
                            custom_data=value.metadata or None,
                        )
                        self._count_value(pset)
                        continue
                type_name = value.value.type_name if value.value is not None else "string"
                w.attribute(
                    spec,
                    name,
                    type_name,
                    value.value.value if value.value is not None else None,
                    custom=True,
                    display_name=value.display_name,
                    display_group=pset.name,
                    custom_data=value.metadata or None,
                    allowed_tokens=value.allowed_tokens or None,
                )
                self._count_value(pset)
        w.add_api_schemas(spec, apis)
        existing = dict(spec.customData) if spec.customData else {}
        ifc_data = dict(existing.get("ifc", {}))
        ifc_data.update(usd_dictionary(info))
        existing["ifc"] = ifc_data
        spec.customData = existing

    def _count_value(self, pset: PropertySet) -> None:
        if pset.is_quantity:
            self.stats.quantities += 1
        else:
            self.stats.properties += 1

    def _sidecar_entry(self, node: Node, sets: list[PropertySet]) -> None:
        entity = node.entity
        properties = []
        for pset in sets:
            for value in pset.values:
                rendered = ""
                if value.value is not None:
                    raw = value.value.value
                    rendered = ", ".join(str(v) for v in raw) if isinstance(raw, list) else str(raw)
                properties.append(
                    {
                        "set": pset.name,
                        "name": value.display_name,
                        "value": rendered,
                        "type": str(value.metadata.get("ifcValueType", "")),
                        "unit": str(value.metadata.get("ifcUnit", "")),
                        "quantity": pset.is_quantity,
                    }
                )
        self.sidecar.append(  # type: ignore[union-attr]
            {
                "path": node.path,
                "globalId": str(util.attr(entity, "GlobalId") or ""),
                "ifcClass": util.ifc_class(entity),
                "name": str(util.attr(entity, "Name") or ""),
                "properties": properties,
            }
        )

    # ================================================================== phase 2

    def add_shape(self, shape: ShapeData) -> None:
        node = self.plan.by_id.get(shape.product_id)
        if node is None:
            self.stats.shapes_without_prim += 1
            return
        entity = node.entity
        element_world = self._world.get(shape.product_id, np.eye(4))
        shape_world = np.array(shape.matrix, dtype=np.float64).reshape(4, 4).T
        shape_world[:3, 3] *= self.scale
        body_local = np.linalg.inv(element_world) @ shape_world
        instance_xform = None if np.allclose(body_local, np.eye(4), atol=1e-9) else _gf_matrix(body_local)
        hidden = self._is_hidden(entity)

        names = self._geometry_children.get(node.path)
        if names is None:
            names = UniqueNames()
            for child in node.children:
                names.reserve(child.name)
            self._geometry_children[node.path] = names
        child_name = names.claim(safe_identifier(shape.context, "Geometry"))
        body_path = f"{node.path}/{child_name}"

        try:
            if shape.is_mesh:
                self._add_mesh(shape, body_path, instance_xform, hidden)
                self._products_with_mesh.add(shape.product_id)
            elif shape.is_curve and self.options.include_curves:
                count = self._add_curves(shape, body_path, instance_xform)
                if not count:
                    return
                self._curve_paths[body_path] = shape.product_id
            else:
                return
        except Exception as exc:
            self.stats.geometry_errors.append(_error_record(entity, str(exc)))
            self._remove_scratch(body_path)
            if not self.options.ignore_geometry_errors:
                raise
            return
        self._geometry_paths.append(body_path)
        self.stats.products_with_geometry.add(shape.product_id)

    def _is_hidden(self, entity: Any) -> bool:
        if not util.is_any(entity, _HIDDEN_GEOMETRY):
            return False
        if (
            util.is_a(entity, "IfcSpace")
            or util.is_a(entity, "IfcSpatialZone")
            or util.is_a(entity, "IfcExternalSpatialElement")
        ):
            return self.options.spaces == "hidden"
        return True

    def _material_bindings(self, shape: ShapeData) -> tuple[list[str], list[tuple[tuple[float, float, float], float]]]:
        paths: list[str] = []
        colors: list[tuple[tuple[float, float, float], float]] = []
        for material in shape.materials:
            spec = self.styles.spec_for(material)
            self._used_materials[spec.name] = spec
            paths.append(f"{self.looks_path}/{spec.name}")
            colors.append((spec.base_color, spec.opacity))
        return paths, colors

    def _add_mesh(self, shape: ShapeData, body_path: str, instance_xform: Any, hidden: bool) -> None:
        paths, colors = self._material_bindings(shape)
        sw = self._scratch_writer
        key = (shape.geometry_id, shape.context)
        record = self._geometry_first.get(key) if self.options.instancing else None
        if record is None:
            stats = write_mesh(
                sw,
                body_path,
                shape,
                scale=self.scale,
                material_paths=paths,
                display_colors=colors,
                double_sided=self.options.double_sided,
            )
            self._finish_instance(body_path, instance_xform, hidden)
            self.stats.meshes += 1
            self.stats.points += stats.points
            self.stats.triangles += stats.triangles
            if self.options.instancing:
                self._geometry_first[key] = _GeometryRecord(body_path, instance_xform, hidden)
            return

        if record.promoted is None:
            proto_name = safe_identifier(f"Geometry_{shape.geometry_id}_{shape.context}", "Geometry")
            proto_path = f"{self.prototypes_path}/{proto_name}"
            if proto_path in self._prototypes:
                proto_path = f"{proto_path}_{_crc(repr(key))}"
            Sdf.CreatePrimInLayer(self._scratch, Sdf.Path(self.prototypes_path))
            if not Sdf.CopySpec(self._scratch, Sdf.Path(record.body_path), self._scratch, Sdf.Path(proto_path)):
                raise RuntimeError(f"could not create prototype {proto_path}")
            proto = self._scratch.GetPrimAtPath(proto_path)
            proto.specifier = Sdf.SpecifierClass
            for attr_name in ("xformOp:transform", "xformOpOrder", "visibility"):
                if attr_name in proto.attributes:
                    proto.RemoveProperty(proto.attributes[attr_name])
            self._remove_scratch(record.body_path)
            self._instance(record.body_path, proto_path, record.instance_xform, record.hidden)
            record.promoted = proto_path
            self._prototypes.append(proto_path)
            self.stats.prototypes += 1
            self.stats.instances += 1
            self.stats.meshes -= 1
        self._instance(body_path, record.promoted, instance_xform, hidden)
        self.stats.instances += 1

    def _instance(self, body_path: str, proto_path: str, instance_xform: Any, hidden: bool) -> None:
        spec = self._scratch_writer.prim(body_path, "")
        spec.referenceList.prependedItems.append(Sdf.Reference("", Sdf.Path(proto_path)))
        spec.instanceable = True
        self._finish_instance(body_path, instance_xform, hidden)

    def _finish_instance(self, body_path: str, instance_xform: Any, hidden: bool) -> None:
        spec = self._scratch.GetPrimAtPath(body_path)
        if instance_xform is not None:
            self._scratch_writer.transform(spec, instance_xform)
        if hidden:
            self._scratch_writer.attribute(spec, "visibility", "token", "invisible", uniform=False)

    def _add_curves(self, shape: ShapeData, body_path: str, instance_xform: Any) -> int:
        color = None
        if shape.materials:
            spec = self.styles.spec_for(shape.materials[0])
            color = spec.base_color
        count = write_curves(self._scratch_writer, body_path, shape, scale=self.scale, guide=False, color=color)
        if count:
            spec = self._scratch.GetPrimAtPath(body_path)
            if instance_xform is not None:
                self._scratch_writer.transform(spec, instance_xform)
            self.stats.curves += 1
        return count

    def _remove_scratch(self, path: str) -> None:
        spec = self._scratch.GetPrimAtPath(path)
        if spec is None:
            return
        parent = spec.nameParent
        if parent is not None:
            del parent.nameChildren[spec.name]

    # ================================================================== phase 3

    def finalize(self) -> None:
        # Curves of products that also have a mesh are reference geometry (axes, footprints).
        for path, product_id in self._curve_paths.items():
            if product_id in self._products_with_mesh:
                spec = self._scratch.GetPrimAtPath(path)
                if spec is not None:
                    self._scratch_writer.attribute(spec, "purpose", "token", "guide")

        for path in sorted(self._geometry_paths, key=_path_sort_key):
            if self._scratch.GetPrimAtPath(path) is None:
                continue
            Sdf.CopySpec(self._scratch, Sdf.Path(path), self.layer, Sdf.Path(path))
            self.stats.prims += 1

        if self._used_materials:
            self.writer.prim(self.looks_path, "Scope")
            self.stats.prims += 1
            for name in sorted(self._used_materials):
                write_material(
                    self.writer, f"{self.looks_path}/{name}", self._used_materials[name], mdl=self.options.write_mdl
                )
                self.stats.materials += 1
                self.stats.prims += 1

        if self._prototypes:
            self.writer.prim(self.prototypes_path, "Scope")
            self.stats.prims += 1
            for path in sorted(self._prototypes):
                Sdf.CopySpec(self._scratch, Sdf.Path(path), self.layer, Sdf.Path(path))
                self.stats.prims += 1
        self._scratch = None  # release geometry memory


# ---------------------------------------------------------------------- helpers


def _gf_matrix(column_major: np.ndarray) -> Any:
    """numpy column-vector matrix -> Gf.Matrix4d (row-vector convention)."""
    return matrix_from_rows(column_major.T.reshape(-1).tolist())


def _path_sort_key(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def _crc(text: str) -> str:
    import zlib

    return format(zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF, "08x")


def _error_record(entity: Any, reason: str) -> dict[str, Any]:
    return {
        "stepId": util.step_id(entity),
        "globalId": util.attr(entity, "GlobalId"),
        "ifcClass": util.ifc_class(entity),
        "name": util.attr(entity, "Name"),
        "reason": reason,
    }


def _surface_styles(styled_item: Any) -> list[Any]:
    styles: list[Any] = []
    for style in util.attr(styled_item, "Styles", ()) or ():
        if util.is_a(style, "IfcSurfaceStyle"):
            styles.append(style)
        elif util.is_a(style, "IfcPresentationStyleAssignment"):
            styles.extend(s for s in util.attr(style, "Styles", ()) or () if util.is_a(s, "IfcSurfaceStyle"))
    return styles


def _products_of_representation_item(model: Any, item: Any) -> list[Any]:
    representations = []
    if util.is_a(item, "IfcRepresentation"):
        representations.append(item)
    else:
        try:
            representations.extend(inv for inv in model.get_inverse(item) if util.is_a(inv, "IfcRepresentation"))
        except Exception:
            return []
    products: list[Any] = []
    for representation in representations:
        for definition in util.inverse(representation, "OfProductRepresentation"):
            products.extend(util.inverse(definition, "ShapeOfProduct"))
        for representation_map in util.inverse(representation, "RepresentationMap"):
            try:
                products.extend(
                    inv for inv in model.get_inverse(representation_map) if util.is_a(inv, "IfcTypeProduct")
                )
            except Exception:
                continue
    return products
