"""Read-only views of IFC models, USD stages and the IfcUSD schema, for IfcUSD Studio.

Every function returns plain, JSON-ready data. Trees are served one level at a
time (``children``) so that large models stay responsive in the browser.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from ifcusd.extension import (
    COLLISION_APPROXIMATIONS,
    PHYSICS_SET,
    PROPERTY_DESCRIPTIONS,
    SET_DESCRIPTIONS,
    SIMULATION_SET,
)

USD_SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")
IFC_SUFFIXES = (".ifc", ".ifczip")
ARRAY_PREVIEW = 6  # items shown of an array value
SEARCH_LIMIT = 200


def file_kind(path: str | Path) -> str | None:
    """``"ifc"``, ``"usd"`` or ``None`` from a file name."""
    suffix = Path(path).suffix.lower()
    if suffix in IFC_SUFFIXES or suffix == ".ifcx":
        return "ifc"
    if suffix in USD_SUFFIXES:
        return "usd"
    return None


def open_view(path: Path) -> UsdView | IfcView:
    kind = file_kind(path)
    if kind == "usd":
        return UsdView(path)
    if path.suffix.lower() == ".ifcx":
        raise ValueError("IFCX files can be converted, but not inspected: inspect the converted USD instead.")
    if kind == "ifc":
        return IfcView(path)
    raise ValueError(f"{path.name} is neither an IFC nor a USD file")


# ---------------------------------------------------------------------- values


def display_value(value: Any, single: bool = False) -> tuple[str, int | None]:
    """A readable rendering of a USD or IFC value and, for arrays, the item count."""
    if value is None:
        return "", None
    if isinstance(value, bool):
        return ("true" if value else "false"), None
    if isinstance(value, int):
        return str(value), None
    if isinstance(value, float):
        return _number(value, single), None
    if isinstance(value, str):
        return value, None
    if type(value).__name__ == "AssetPath":
        return f"@{value.path}@", None
    if hasattr(value, "is_a") and callable(value.is_a):  # an IfcOpenShell entity reference
        return _entity_label(value), None
    try:
        items = list(value)
    except TypeError:
        return str(value), None
    nested = bool(items) and not isinstance(items[0], (str, bytes)) and _iterable(items[0])
    if nested and len(items) <= 4 and all(len(list(i)) <= 4 for i in items):  # vectors, matrices
        return "(" + ", ".join(display_value(i, single)[0] for i in items) + ")", None
    shown = [display_value(i, single)[0] for i in items[:ARRAY_PREVIEW]]
    if not nested and len(items) <= 4 and not _is_vt_array(value):  # Gf vectors
        return "(" + ", ".join(shown) + ")", None
    text = "[" + ", ".join(shown) + (", …" if len(items) > ARRAY_PREVIEW else "") + "]"
    return text, len(items)


def _number(value: float, single: bool) -> str:
    if not math.isfinite(value):
        return str(value)
    if single:  # 32-bit storage: shortest decimal that reads back the same
        return format(value, ".7g")
    return repr(value)


def _iterable(value: Any) -> bool:
    if isinstance(value, (str, bytes)):
        return False
    try:
        iter(value)
    except TypeError:
        return False
    return True


def _is_vt_array(value: Any) -> bool:
    return type(value).__module__.startswith("pxr") and type(value).__name__.endswith("Array")


def _entity_label(entity: Any) -> str:
    try:
        name = getattr(entity, "Name", None)
    except Exception:
        name = None
    label = f"#{entity.id()} {entity.is_a()}"
    return f"{label} {name!r}" if name else label


# ---------------------------------------------------------------------- USD


_GEOMETRY_PREFIXES = ("xformOp", "primvars:", "points", "normals", "faceVertex", "extent", "doubleSided")
_GEOMETRY_NAMES = {"purpose", "visibility", "orientation", "subdivisionScheme", "curveVertexCounts", "widths", "type"}
_SINGLE_TYPES = ("float", "half", "point3f", "color3f", "normal3f", "vector3f", "texCoord2f", "quatf")


class UsdView:
    """A USD stage opened for browsing."""

    kind = "usd"

    def __init__(self, path: Path) -> None:
        from pxr import Usd

        self.path = path
        self.stage = Usd.Stage.Open(str(path))
        if self.stage is None:
            raise ValueError(f"Could not open USD file {path.name}")
        self._predicate = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)

    # ------------------------------------------------------------------ summary

    def summary(self) -> dict[str, Any]:
        from pxr import UsdGeom, UsdPhysics

        stage = self.stage
        layer = stage.GetRootLayer()
        provenance = dict(layer.customLayerData.get("ifcusd", {}) or {})
        types: Counter[str] = Counter()
        classes: Counter[str] = Counter()
        sets: Counter[str] = Counter()
        physics: Counter[str] = Counter()
        prims = instances = 0
        for prim in stage.Traverse():
            prims += 1
            types[prim.GetTypeName() or "(no type)"] += 1
            instances += prim.IsInstance()
            ifc_class = prim.GetAttribute("ifc:class")
            if ifc_class and ifc_class.HasAuthoredValue():
                classes[str(ifc_class.Get())] += 1
            for entry in (prim.GetCustomDataByKey("ifc:propertySets") or {}).values():
                if isinstance(entry, dict) and entry.get("name"):
                    sets[str(entry["name"])] += 1
            for schema in applied_schemas(prim):
                if schema.startswith("Physics"):
                    physics[schema.split(":")[0]] += 1
        options = provenance.get("options", {}) or {}
        return {
            "kind": "usd",
            "name": self.path.name,
            "facts": _facts(
                [
                    ("Written by", _written_by(provenance)),
                    ("Source IFC", provenance.get("sourceName")),
                    ("IFC schema", provenance.get("ifcSchema")),
                    ("Mapping version", provenance.get("schemaVersion")),
                    ("Profile", provenance.get("profile") or options.get("profile")),
                    ("Up axis", UsdGeom.GetStageUpAxis(stage)),
                    ("Metres per unit", UsdGeom.GetStageMetersPerUnit(stage)),
                    ("Kilograms per unit", UsdPhysics.GetStageKilogramsPerUnit(stage)),
                    ("Default prim", stage.GetDefaultPrim().GetPath().pathString if stage.GetDefaultPrim() else None),
                    ("Prims", prims),
                    ("Instances", instances or None),
                ]
            ),
            "classes": _ranked(classes),
            "types": _ranked(types),
            "propertySets": _ranked(sets),
            "physics": _ranked(physics),
            "root": "/",
        }

    # ------------------------------------------------------------------ tree

    def children(self, path: str, offset: int = 0, limit: int = 500) -> dict[str, Any]:
        prim = self.stage.GetPseudoRoot() if path in ("", "/") else self.stage.GetPrimAtPath(path)
        if not prim:
            raise KeyError(path)
        children = list(prim.GetFilteredChildren(self._predicate))
        return {
            "total": len(children),
            "offset": offset,
            "nodes": [self._node(child) for child in children[offset : offset + limit]],
        }

    def _node(self, prim: Any) -> dict[str, Any]:
        ifc_class = prim.GetAttribute("ifc:class")
        display = prim.GetMetadata("displayName")
        tags = []
        if any(s.startswith("Physics") for s in applied_schemas(prim)):
            tags.append("physics")
        if prim.IsInstance():
            tags.append("instance")
        return {
            "id": prim.GetPath().pathString,
            "name": str(display) if display else prim.GetName(),
            "type": prim.GetTypeName(),
            "ifcClass": str(ifc_class.Get()) if ifc_class and ifc_class.HasAuthoredValue() else None,
            "hasChildren": any(True for _ in prim.GetFilteredChildren(self._predicate)),
            "tags": tags,
        }

    def search(self, query: str) -> list[dict[str, Any]]:
        from pxr import Usd

        needle = query.casefold().strip()
        found: list[dict[str, Any]] = []
        if not needle:
            return found
        for prim in Usd.PrimRange.Stage(self.stage, self._predicate):
            ifc_name = prim.GetAttribute("ifc:name")
            ifc_class = prim.GetAttribute("ifc:class")
            texts = [prim.GetName(), str(prim.GetMetadata("displayName") or "")]
            for attribute in (ifc_name, ifc_class, prim.GetAttribute("ifc:globalId")):
                if attribute and attribute.HasAuthoredValue():
                    texts.append(str(attribute.Get()))
            if any(needle in text.casefold() for text in texts):
                found.append(self._node(prim))
                if len(found) >= SEARCH_LIMIT:
                    break
        return found

    # ------------------------------------------------------------------ details

    def node(self, path: str) -> dict[str, Any]:
        from pxr import Usd, UsdGeom, UsdShade

        prim = self.stage.GetPrimAtPath(path)
        if not prim:
            raise KeyError(path)
        groups: dict[str, dict[str, Any]] = {}

        def group(key: str, title: str, tone: str, description: str | None = None) -> dict[str, Any]:
            if key not in groups:
                groups[key] = {"title": title, "tone": tone, "description": description, "rows": []}
            return groups[key]

        set_names = {
            ident: str(entry.get("name") or ident)
            for key in ("propertySets", "quantitySets")
            for ident, entry in (prim.GetCustomDataByKey(f"ifc:{key}") or {}).items()
            if isinstance(entry, dict)
        }
        for attribute in prim.GetAttributes():
            if not attribute.IsAuthored():  # schema fallbacks (velocities, empty IFC attributes) are noise here
                continue
            name = attribute.GetName()
            parts = name.split(":")
            if name.startswith(("ifc:pset:", "ifc:qto:")) and len(parts) >= 4:
                quantity = parts[1] == "qto"
                set_name = set_names.get(parts[2], parts[2])
                target = group(
                    f"{parts[1]}:{parts[2]}",
                    set_name,
                    "physics" if set_name == PHYSICS_SET else "ifc",
                    "Quantity set" if quantity else SET_DESCRIPTIONS.get(set_name, "Property set"),
                )
            elif name.startswith("ifc:"):
                target = group("ifc", "IFC attributes", "ifc")
            elif name.startswith("physics:"):
                target = group("physics", "Physics (UsdPhysics)", "physics")
            elif name.startswith(("semantics:", "semantic:")):
                target = group("semantics", "Semantic labels", "usd")
            elif name.startswith(("robot:", "sim:")):
                target = group("extension", "Robot and simulation data", "usd")
            elif name.startswith(_GEOMETRY_PREFIXES) or name in _GEOMETRY_NAMES:
                target = group("geometry", "Geometry and transform", "usd")
            else:
                target = group("other", "Other attributes", "usd")
            target["rows"].append(_attribute_row(attribute, Usd.TimeCode.EarliestTime()))
        relationships = []
        for relationship in prim.GetRelationships():
            targets = [str(t) for t in relationship.GetTargets()]
            if targets:
                relationships.append(
                    {"name": relationship.GetName(), "label": _label(relationship), "targets": targets}
                )
        materials = {}
        if prim.HasAPI(UsdShade.MaterialBindingAPI) or prim.IsA(UsdGeom.Gprim):
            for purpose, label in (("", "render"), ("physics", "physics")):
                bound, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)
                if bound:
                    materials[label] = bound.GetPath().pathString
        ordered = sorted(groups.items(), key=lambda kv: (_group_rank(kv[0]), kv[1]["title"].casefold()))
        display = prim.GetMetadata("displayName")
        return {
            "id": path,
            "name": str(display) if display else prim.GetName(),
            "path": path,
            "type": prim.GetTypeName(),
            "kind": str(prim.GetMetadata("kind") or "") or None,
            "schemas": applied_schemas(prim),
            "instance": prim.IsInstance() or prim.IsInstanceProxy(),
            "groups": [g for _key, g in ordered],
            "relationships": relationships,
            "materials": materials,
        }


_GROUP_ORDER = ("ifc", f"pset:{PHYSICS_SET}", "physics", "pset:", "qto:", "extension", "semantics", "geometry")


def applied_schemas(prim: Any) -> list[str]:
    """API schemas of a prim, including authored ones whose plugin is not registered here."""
    names = list(prim.GetAppliedSchemas())
    authored = prim.GetMetadata("apiSchemas")
    for name in authored.GetAddedOrExplicitItems() if authored else ():
        if name not in names and name not in authored.deletedItems:
            names.append(name)
    return names


def _group_rank(key: str) -> int:
    """IFC data first, physics side by side in both forms, then the rest."""
    for rank, prefix in enumerate(_GROUP_ORDER):
        if key == prefix or (prefix.endswith(":") and key.startswith(prefix)):
            return rank
    return len(_GROUP_ORDER)


def _attribute_row(attribute: Any, time: Any) -> dict[str, Any]:
    type_name = str(attribute.GetTypeName())
    samples = attribute.GetNumTimeSamples()
    value = attribute.Get(time) if samples else attribute.Get()
    text, count = display_value(value, single=type_name.startswith(_SINGLE_TYPES))
    data = attribute.GetCustomData() or {}
    return {
        "name": attribute.GetName(),
        "label": _label(attribute),
        "type": type_name,
        "value": text,
        "count": count,
        "samples": samples or None,
        "unit": data.get("ifcUnit"),
        "valueType": data.get("ifcValueType"),
        "authored": attribute.HasAuthoredValue(),
    }


def _label(prop: Any) -> str:
    display = prop.GetDisplayName()
    return str(display) if display else prop.GetName().split(":")[-1]


def _written_by(provenance: dict[str, Any]) -> str | None:
    if not provenance:
        return None
    return f"{provenance.get('converter', 'IfcUSD')} {provenance.get('converterVersion', '')}".strip()


# ---------------------------------------------------------------------- IFC


class IfcView:
    """An IFC model opened for browsing its spatial structure."""

    kind = "ifc"

    def __init__(self, path: Path) -> None:
        from ifcusd.ifc.reader import open_ifc

        self.path = path
        self.opened = open_ifc(path)
        self.model = self.opened.model
        self._unplaced: list[Any] | None = None

    def summary(self) -> dict[str, Any]:
        from ifcusd.ifc import util
        from ifcusd.ifc.reader import header_info
        from ifcusd.ifc.units import UnitResolver

        model = self.model
        header = header_info(model)
        products = Counter(util.ifc_class(e) for e in util.by_type(model, "IfcProduct"))
        sets: Counter[str] = Counter()
        for rel in util.by_type(model, "IfcRelDefinesByProperties"):
            definition = util.attr(rel, "RelatingPropertyDefinition")
            for item in definition if isinstance(definition, (list, tuple)) else (definition,):
                name = util.attr(item, "Name") if item is not None else None
                if name:
                    sets[str(name)] += len(util.attr(rel, "RelatedObjects", ()) or ())
        units = UnitResolver(model).project_units()
        facts = [("Schema", self.opened.schema)]
        if self.opened.declared_schema != self.opened.schema:
            facts.append(("Declared schema", self.opened.declared_schema))
        facts += [
            ("Originating system", header.get("originatingSystem")),
            ("Time stamp", header.get("timeStamp")),
            ("Length unit", units.get("LENGTHUNIT")),
            ("Mass unit", units.get("MASSUNIT")),
            ("Products", sum(products.values())),
        ]
        return {
            "kind": "ifc",
            "name": self.path.name,
            "facts": _facts(facts),
            "classes": _ranked(products),
            "propertySets": _ranked(sets),
            "units": units,
            "root": "",
        }

    def children(self, node_id: str, offset: int = 0, limit: int = 500) -> dict[str, Any]:
        from ifcusd.ifc import util

        if node_id in ("", "/"):
            items: list[Any] = util.by_type(self.model, "IfcProject")
            nodes = [self._node(e) for e in items]
            unplaced = self._unplaced_products()
            if unplaced:
                nodes.append(
                    {
                        "id": "unplaced",
                        "name": "Not in the spatial structure",
                        "type": "",
                        "ifcClass": None,
                        "hasChildren": True,
                        "tags": ["virtual"],
                        "count": len(unplaced),
                    }
                )
            return {"total": len(nodes), "offset": 0, "nodes": nodes}
        items = self._unplaced_products() if node_id == "unplaced" else _ifc_children(self._entity(node_id))
        return {
            "total": len(items),
            "offset": offset,
            "nodes": [self._node(e) for e in items[offset : offset + limit]],
        }

    def search(self, query: str) -> list[dict[str, Any]]:
        from ifcusd.ifc import util

        needle = query.casefold().strip()
        found: list[dict[str, Any]] = []
        if not needle:
            return found
        for entity in util.by_type(self.model, "IfcProduct") + util.by_type(self.model, "IfcProject"):
            texts = [util.ifc_class(entity), str(util.attr(entity, "Name") or ""), str(util.attr(entity, "GlobalId"))]
            if any(needle in text.casefold() for text in texts):
                found.append(self._node(entity))
                if len(found) >= SEARCH_LIMIT:
                    break
        return found

    def node(self, node_id: str) -> dict[str, Any]:
        import ifcopenshell.util.element as element

        from ifcusd.ifc import util

        entity = self._entity(node_id)
        attributes = []
        for name, value in entity.get_info(recursive=False, include_identifier=False).items():
            if name == "type" or value is None:
                continue
            text, count = display_value(value)
            attributes.append({"name": name, "label": name, "type": "", "value": text, "count": count})
        groups = [{"title": "IFC attributes", "tone": "ifc", "description": None, "rows": attributes}]
        try:
            sets = element.get_psets(entity)
        except Exception:
            sets = {}
        for set_name in sorted(sets, key=lambda n: (n != PHYSICS_SET, n.casefold())):
            values = sets[set_name]
            rows = []
            for prop, value in values.items():
                if prop == "id":
                    continue
                text, count = display_value(value)
                description = PROPERTY_DESCRIPTIONS.get(set_name, {}).get(prop)
                rows.append(
                    {"name": prop, "label": prop, "type": "", "value": text, "count": count, "note": description}
                )
            groups.append(
                {
                    "title": set_name,
                    "tone": "physics" if set_name == PHYSICS_SET else "ifc",
                    "description": SET_DESCRIPTIONS.get(set_name),
                    "rows": rows,
                }
            )
        related = []
        type_object = _safe(lambda: element.get_type(entity))
        if type_object is not None and type_object != entity:
            related.append({"name": "Type", "targets": [_entity_label(type_object)]})
        material = _safe(lambda: element.get_material(entity))
        if material is not None:
            related.append({"name": "Material", "targets": [_entity_label(material)]})
        container = _safe(lambda: element.get_container(entity))
        if container is not None:
            related.append({"name": "Contained in", "targets": [_entity_label(container)]})
        return {
            "id": node_id,
            "name": str(util.attr(entity, "Name") or util.ifc_class(entity)),
            "path": f"#{entity.id()}",
            "type": util.ifc_class(entity),
            "kind": None,
            "schemas": [],
            "instance": False,
            "groups": groups,
            "relationships": related,
            "materials": {},
        }

    def _entity(self, node_id: str) -> Any:
        try:
            return self.model.by_id(int(node_id.lstrip("#")))
        except (ValueError, RuntimeError) as exc:
            raise KeyError(node_id) from exc

    def _node(self, entity: Any) -> dict[str, Any]:
        from ifcusd.ifc import util

        name = util.attr(entity, "Name")
        tags = []
        if any(
            util.attr(util.attr(rel, "RelatingPropertyDefinition"), "Name") == PHYSICS_SET
            for rel in util.inverse(entity, "IsDefinedBy")
            if util.is_a(rel, "IfcRelDefinesByProperties")
        ):
            tags.append("physics")
        return {
            "id": f"#{entity.id()}",
            "name": str(name) if name else util.ifc_class(entity),
            "type": "",
            "ifcClass": util.ifc_class(entity),
            "hasChildren": bool(_ifc_children(entity)),
            "tags": tags,
        }

    def _unplaced_products(self) -> list[Any]:
        """Elements that no spatial structure or decomposition reaches."""
        from ifcusd.ifc import util

        if self._unplaced is None:
            reached: set[int] = set()
            stack = list(util.by_type(self.model, "IfcProject"))
            while stack:
                entity = stack.pop()
                if entity.id() in reached:
                    continue
                reached.add(entity.id())
                stack.extend(_ifc_children(entity))
            self._unplaced = [
                e
                for e in util.by_type(self.model, "IfcElement")
                if e.id() not in reached and not util.is_a(e, "IfcFeatureElementSubtraction")
            ]
        return self._unplaced


def _ifc_children(entity: Any) -> list[Any]:
    """Decomposition, containment, nesting, openings and fillings of an IFC object."""
    from ifcusd.ifc import util

    children: list[Any] = []
    for rel in util.inverse(entity, "IsDecomposedBy") + util.inverse(entity, "IsNestedBy"):
        children.extend(util.attr(rel, "RelatedObjects", ()) or ())
    for rel in util.inverse(entity, "ContainsElements"):
        children.extend(util.attr(rel, "RelatedElements", ()) or ())
    for rel in util.inverse(entity, "HasOpenings"):
        children.append(util.attr(rel, "RelatedOpeningElement"))
    for rel in util.inverse(entity, "HasFillings"):
        children.append(util.attr(rel, "RelatedBuildingElement"))
    seen: set[int] = set()
    unique = []
    for child in children:
        if child is not None and child.id() not in seen:
            seen.add(child.id())
            unique.append(child)
    return unique


def _safe(getter: Any) -> Any:
    try:
        return getter()
    except Exception:
        return None


# ---------------------------------------------------------------------- shared


def _facts(pairs: list[tuple[str, Any]]) -> list[dict[str, str]]:
    facts = []
    for label, value in pairs:
        if label and value not in (None, "", []):
            text, _count = display_value(value)
            facts.append({"label": label, "value": text})
    return facts


def _ranked(counter: Counter[str], limit: int = 40) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


# ---------------------------------------------------------------------- schema


# How the main IFC concepts land in USD (the full rules are in the schema mapping reference).
MAPPING = (
    ("IfcProject and the file header", "The root prim /World with IfcFileAPI, ifc:units:* and IfcGeoreferenceAPI"),
    ("Spatial structure (site, building, storey, space)", "Xform prims, parented by aggregation and containment"),
    ("Elements (walls, doors, furniture, robots…)", "Xform prims with IfcEntityAPI; the IFC class is ifc:class"),
    ("Explicit attributes (GlobalId, Name, PredefinedType…)", "Typed attributes ifc:globalId, ifc:name, …"),
    ("Property sets", "ifc:pset:<set>:<property>, with IfcPropertySetAPI:<set> and the unit in customData"),
    ("Quantity sets", "ifc:qto:<set>:<quantity>, with IfcQuantitySetAPI:<set>"),
    ("ObjectPlacement", "xformOp:transform, double precision, in stage units"),
    ("Body and Axis representations", "Mesh and BasisCurves child prims named after the representation"),
    ("Geometry shared by types", "Instanceable prims referencing /World/Prototypes"),
    ("Surface styles", "UsdPreviewSurface materials in /World/Looks (plus OmniPBR for Omniverse)"),
    ("IfcUSD_Physics", "UsdPhysics schemas, with physics materials in /World/PhysicsMaterials"),
    ("IFC class", "semantics:labels:class (UsdSemantics), for synthetic data and segmentation"),
)

PHYSICS_MAPPING = (
    ("Mass, Density, CenterOfMass", "PhysicsMassAPI on the element prim"),
    ("RigidBodyEnabled, KinematicEnabled", "PhysicsRigidBodyAPI on the element prim"),
    ("CollisionEnabled", "PhysicsCollisionAPI on each of the element's meshes"),
    ("CollisionApproximation", "PhysicsMeshCollisionAPI on each mesh (" + ", ".join(COLLISION_APPROXIMATIONS) + ")"),
    (
        "StaticFriction, DynamicFriction, Restitution",
        "A physics material in /World/PhysicsMaterials, bound to the meshes",
    ),
    ("Articulated, JointCount", "Kept as IFC data: a robot's links were merged into one element"),
)


def schema_description() -> dict[str, Any]:
    """The IfcUSD USD schema plugin, the IFC extension property sets and the mapping overview."""
    from pxr import Sdf

    from ifcusd._version import SCHEMA_VERSION, __version__
    from ifcusd.schema import plugin_path

    layer = Sdf.Layer.FindOrOpen(str(plugin_path() / "generatedSchema.usda"))
    kinds = {}
    try:
        info = json.loads((plugin_path() / "plugInfo.json").read_text(encoding="utf-8"))
        for plugin in info.get("Plugins", []):
            for entry in plugin.get("Info", {}).get("Types", {}).values():
                kinds[entry.get("schemaIdentifier")] = entry.get("schemaKind", "")
    except (OSError, ValueError, AttributeError):
        pass
    schemas = []
    for spec in layer.rootPrims if layer is not None else ():
        properties = []
        for prop in spec.properties:
            type_name = str(prop.typeName) if hasattr(prop, "typeName") else "rel"
            tokens = list(prop.GetInfo("allowedTokens")) if prop.HasInfo("allowedTokens") else []
            properties.append({"name": prop.name, "type": type_name, "doc": prop.documentation or "", "tokens": tokens})
        schemas.append(
            {
                "name": spec.name,
                "doc": (spec.documentation or "").strip(),
                "apply": "multiple" if kinds.get(spec.name) == "multipleApplyAPI" else "single",
                "properties": properties,
            }
        )
    extension = [
        {
            "name": name,
            "description": SET_DESCRIPTIONS[name],
            "properties": [{"name": prop, "doc": doc} for prop, doc in PROPERTY_DESCRIPTIONS[name].items()],
        }
        for name in (PHYSICS_SET, SIMULATION_SET)
    ]
    return {
        "version": __version__,
        "mappingVersion": SCHEMA_VERSION,
        "mapping": [{"ifc": ifc, "usd": usd} for ifc, usd in MAPPING],
        "physicsMapping": [{"ifc": ifc, "usd": usd} for ifc, usd in PHYSICS_MAPPING],
        "schemas": schemas,
        "extension": extension,
    }
