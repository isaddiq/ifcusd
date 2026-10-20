"""IFC5 (IFCX) to OpenUSD.

IFCX is already modelled on USD (paths, ``children``/``inherits`` arcs and
``usd::*`` attributes), so the mapping is direct:

======================================  =========================================
IFCX                                    USD
======================================  =========================================
composed node                           prim (child name -> prim name)
``usd::xformop.transform``              ``xformOp:transform`` (matrix4d)
``usd::usdgeom::mesh``                  ``UsdGeom.Mesh`` (+ flat normals, extent)
``usd::usdgeom::basiscurves``           ``UsdGeom.BasisCurves`` (linear)
``usd::usdgeom::visibility``            ``visibility``
``bsi::ifc::class`` {code, uri}         ``ifc:class`` + ``ifc:classUri`` (IfcEntityAPI)
``bsi::ifc::presentation::*``           UsdPreviewSurface material + displayColor
``{"ref": path}`` values                USD relationship to the referenced prim(s)
any other ``ns::a::b`` attribute         attribute ``ns:a:b`` (``-`` -> ``_``), typed;
                                        objects become nested namespaces
======================================  =========================================

The exact IFCX key of every attribute is kept in ``customData.ifcxKey`` so the
mapping is reversible.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import numpy as np

from ifcusd import schema
from ifcusd._version import SCHEMA_VERSION, __version__
from ifcusd.dependencies import import_usd, usd_version
from ifcusd.errors import InputError
from ifcusd.ifc.styles import MaterialSpec
from ifcusd.ifc.values import to_json
from ifcusd.ifcx.compose import ComposedNode, Composer, IfcxLayer, find_roots, flatten, load_layer
from ifcusd.naming import UniqueNames, safe_identifier
from ifcusd.options import ConversionOptions
from ifcusd.report import ConversionReport

_SPATIAL = {
    "IfcProject",
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
    "IfcSpace",
    "IfcFacility",
    "IfcFacilityPart",
    "IfcBridge",
    "IfcBridgePart",
    "IfcRoad",
    "IfcRoadPart",
    "IfcRailway",
    "IfcRailwayPart",
    "IfcMarineFacility",
    "IfcTunnel",
    "IfcSpatialZone",
    "IfcExternalSpatialElement",
}
_CLASS_KEY = "bsi::ifc::class"
_COLOR_KEY = "bsi::ifc::presentation::diffuseColor"
_OPACITY_KEY = "bsi::ifc::presentation::opacity"


def convert_ifcx(inputs: list[Path], target: Path, options: ConversionOptions, metadata_json_path: Any = None) -> Any:
    from ifcusd.converter import ConversionResult

    usd = import_usd()
    started = time.perf_counter()
    layers = [load_layer(path) for path in inputs]
    nodes = flatten(layers)
    roots = find_roots(nodes)
    if not roots:
        raise InputError("IFCX data has no composition root")
    composer = Composer(nodes)
    composed = {root: composer.compose(root) for root in roots}

    target.parent.mkdir(parents=True, exist_ok=True)
    layer = usd.Sdf.Layer.CreateAnonymous(target.stem + ".usda")
    writer = _IfcxWriter(layer, layers, options, sidecar=[] if metadata_json_path else None)
    writer.write(composed)

    from ifcusd.converter import IfcToUsdConverter

    IfcToUsdConverter._save(layer, target)
    elapsed = time.perf_counter() - started
    report = ConversionReport(
        input_path=str(inputs[0]),
        output_path=str(target),
        input_format="IFCX",
        ifc_schema="IFC5",
        declared_schema=str(layers[0].header.get("ifcxVersion", "")),
        source_sha256=layers[0].sha256,
        converter_version=__version__,
        schema_version=SCHEMA_VERSION,
        ifcopenshell_version="n/a",
        usd_version=usd_version(),
        profile=options.profile,
        options=options.to_dict(),
        counts=dict(writer.counts),
        warnings=writer.warnings,
        timings={"total": round(elapsed, 4)},
    )
    report.coverage = {"IfcxNode": _coverage(nodes, writer.sources_written)}
    if report.missing_count:
        report.warnings.append(f"{report.missing_count} IFCX node(s) are not represented")
    if metadata_json_path and writer.sidecar is not None:
        from ifcusd.sidecar import write_sidecar

        write_sidecar(metadata_json_path, writer.sidecar, report)
    return ConversionResult(
        input_path=inputs[0],
        output_path=target,
        elements_seen=writer.counts.get("classified", 0),
        meshes_written=writer.counts.get("meshes", 0),
        spatial_prims_written=writer.counts.get("spatial", 0),
        report=report,
    )


class _IfcxWriter:
    def __init__(
        self,
        layer: Any,
        layers: list[IfcxLayer],
        options: ConversionOptions,
        *,
        sidecar: list[dict[str, Any]] | None = None,
    ) -> None:
        from ifcusd.usd.sdf import LayerWriter

        self.sidecar = sidecar
        self.layer = layer
        self.layers = layers
        self.options = options
        self.writer = LayerWriter(layer)
        self.scale = options.output_scale
        self.root = f"/{options.root_prim_name}"
        self.counts: dict[str, int] = {
            "prims": 0,
            "meshes": 0,
            "curves": 0,
            "classified": 0,
            "spatial": 0,
            "materials": 0,
        }
        self.warnings: list[str] = []
        self.materials: dict[str, MaterialSpec] = {}
        self.prims_by_source: dict[str, list[str]] = {}
        self.pending_refs: list[tuple[str, str, list[str], dict[str, Any]]] = []
        self.sources_written: set[str] = set()
        self.schemas: dict[str, Any] = {}
        for item in layers:
            self.schemas.update(item.schemas)

    # ------------------------------------------------------------------ stage

    def write(self, composed: dict[str, ComposedNode]) -> None:
        from ifcusd.usd.materials import write_material

        w = self.writer
        header = self.layers[0].header
        self.layer.defaultPrim = self.options.root_prim_name
        self.layer.pseudoRoot.SetInfo("upAxis", self.options.up_axis)
        self.layer.pseudoRoot.SetInfo("metersPerUnit", float(self.options.output_meters_per_unit))
        self.layer.documentation = f"Converted from {self.layers[0].path.name} (IFC5/IFCX) by IfcUSD {__version__}"
        from ifcusd.usd.sdf import usd_dictionary

        self.layer.customLayerData = {
            "ifcusd": usd_dictionary(
                {
                    "converter": "IfcUSD",
                    "converterVersion": __version__,
                    "schemaVersion": SCHEMA_VERSION,
                    "usdVersion": usd_version(),
                    "ifcSchema": "IFC5",
                    "sourceName": self.layers[0].path.name,
                    "sourceSha256": self.layers[0].sha256,
                    "ifcxLayers": [f"{item.path.name}:{item.sha256}" for item in self.layers],
                    "profile": self.options.profile,
                }
            )
        }
        root = w.prim(
            self.root, "Xform", kind="assembly", api_schemas=[schema.FILE_API] if self.options.include_metadata else ()
        )
        if self.options.up_axis == "Y":
            w.rotate_x(root, -90.0)
        if self.options.include_metadata:
            w.attribute(root, "ifc:file:schema", "token", "IFC5", display_group="IFC File")
            for key, name in (("ifcxVersion", "schemaIdentifier"), ("id", "name"), ("timestamp", "timeStamp")):
                if header.get(key):
                    w.attribute(root, f"ifc:file:{name}", "string", str(header[key]), display_group="IFC File")
            if header.get("author"):
                w.attribute(root, "ifc:file:author", "string[]", [str(header["author"])], display_group="IFC File")
            w.attribute(root, "ifc:file:sha256", "string", self.layers[0].sha256, display_group="IFC File")
            if header.get("dataVersion"):
                w.attribute(
                    root,
                    "ifcx:dataVersion",
                    "string",
                    str(header["dataVersion"]),
                    custom=True,
                    display_group="IFC File",
                )
            imports = sorted({str(i.get("uri")) for item in self.layers for i in item.imports if i.get("uri")})
            if imports:
                w.attribute(root, "ifcx:imports", "string[]", imports, custom=True, display_group="IFC File")
        self.counts["prims"] += 1

        names = UniqueNames()
        for root_path in sorted(composed):
            name = names.claim(safe_identifier(root_path, "Root"))
            self._write_node(f"{self.root}/{name}", composed[root_path], inherited_color=None, model_parent=True)
        self._resolve_references()

        if self.materials:
            looks = f"{self.root}/Looks"
            w.prim(looks, "Scope")
            for key in sorted(self.materials):
                spec = self.materials[key]
                write_material(w, f"{looks}/{spec.name}", spec, mdl=self.options.write_mdl)
                self.counts["materials"] += 1
        self.counts["sourceNodesWritten"] = len(self.sources_written)

    # ------------------------------------------------------------------ nodes

    def _write_node(
        self,
        path: str,
        node: ComposedNode,
        *,
        inherited_color: tuple[tuple[float, float, float], float] | None,
        model_parent: bool,
    ) -> None:
        w = self.writer
        attributes = {k: v for k, v in node.attributes.items() if v is not None}
        mesh = attributes.get("usd::usdgeom::mesh")
        curves = attributes.get("usd::usdgeom::basiscurves")
        ifc_class = _class_code(attributes.get(_CLASS_KEY))
        cloud = None
        if mesh is None and curves is None:
            try:
                cloud = _decode_points(attributes)
            except Exception as exc:  # malformed point payloads stay as plain attributes
                self.warnings.append(f"Point cloud at {path} could not be decoded: {exc}")
        has_geometry = mesh is not None or curves is not None or cloud is not None
        geometry_inline = has_geometry and not node.children

        prim_type = "Xform"
        if geometry_inline:
            prim_type = "Mesh" if mesh is not None else ("BasisCurves" if curves is not None else "Points")
        kind = None
        is_spatial = ifc_class in _SPATIAL
        if model_parent and (is_spatial or path.count("/") == 2):
            kind = "assembly" if ifc_class == "IfcProject" else "group"
        apis = [schema.ENTITY_API] if ifc_class and self.options.include_metadata else []
        spec = w.prim(path, prim_type, kind=kind, api_schemas=apis)
        self.counts["prims"] += 1
        self.sources_written.add(node.source)
        self.prims_by_source.setdefault(node.source, []).append(path)

        color = inherited_color
        if _COLOR_KEY in attributes or _OPACITY_KEY in attributes:
            color = _color(attributes.get(_COLOR_KEY), attributes.get(_OPACITY_KEY), inherited_color)
            if not geometry_inline or mesh is not None:
                material_path = self._material(color)
                w.add_api_schemas(spec, ["MaterialBindingAPI"])
                w.relationship(spec, "material:binding", [material_path])

        transform = attributes.get("usd::xformop")
        if isinstance(transform, dict) and transform.get("transform"):
            rows = np.array(transform["transform"], dtype=np.float64).reshape(4, 4)
            rows[3, :3] *= self.scale
            from ifcusd.usd.sdf import matrix_from_rows

            w.transform(spec, matrix_from_rows(rows.reshape(-1).tolist()))
        visibility = attributes.get("usd::usdgeom::visibility")
        if isinstance(visibility, dict) and visibility.get("visibility"):
            w.attribute(spec, "visibility", "token", str(visibility["visibility"]), uniform=False)

        if self.options.include_metadata:
            if ifc_class:
                self.counts["classified"] += 1
                if is_spatial:
                    self.counts["spatial"] += 1
                w.attribute(spec, "ifc:class", "token", ifc_class, display_group="IFC", display_name="Class")
                uri = (
                    attributes.get(_CLASS_KEY, {}).get("uri") if isinstance(attributes.get(_CLASS_KEY), dict) else None
                )
                if uri:
                    w.attribute(spec, "ifc:classUri", "string", str(uri), custom=True, display_group="IFC")
                if self.options.semantic_labels and not is_spatial:
                    w.add_api_schemas(spec, ["SemanticsLabelsAPI:class"])
                    w.attribute(spec, "semantics:labels:class", "token[]", [ifc_class])
            w.attribute(spec, "ifcx:node", "string", node.source, custom=True, display_group="IFCX")
            if self.sidecar is not None and ifc_class:
                self.sidecar.append(_sidecar_record(path, node, ifc_class, attributes))
            for key in sorted(attributes):
                if key.startswith("usd::") or (cloud is not None and key == cloud[2]):
                    continue
                self._write_attribute(spec, path, key, attributes[key])

        target = path
        if mesh is not None:
            target = path if geometry_inline else f"{path}/Geometry"
            self._write_mesh(target, mesh, color)
        elif curves is not None and (self.options.include_curves or geometry_inline):
            target = path if geometry_inline else f"{path}/Geometry"
            self._write_curves(target, curves, color)
        elif cloud is not None:
            target = path if geometry_inline else f"{path}/Geometry"
            self._write_points(target, cloud, color)

        names = UniqueNames()
        if not geometry_inline and has_geometry:
            names.reserve("Geometry")
        for child_name in sorted(node.children):
            name = names.claim(safe_identifier(child_name, "Node"))
            self._write_node(
                f"{path}/{name}",
                node.children[child_name],
                inherited_color=color,
                model_parent=model_parent and kind is not None,
            )

    # ------------------------------------------------------------------ attributes

    def _write_attribute(
        self, spec: Any, prim_path: str, key: str, value: Any, prefix: str | None = None, depth: int = 0
    ) -> None:
        name = prefix or ":".join(safe_identifier(part.replace("-", "_"), "attr") for part in key.split("::"))
        data = {"ifcxKey": key}
        schema_entry = None if prefix else self.schemas.get(key)
        w = self.writer
        if isinstance(value, dict):
            if set(value) == {"ref"} and isinstance(value["ref"], str):
                self.pending_refs.append((prim_path, name, [value["ref"]], data))
                return
            if depth < 4 and 0 < len(value) <= 64:
                # Objects become nested namespaces: {"code": .., "uri": ..} -> <name>:code, <name>:uri
                names = UniqueNames()
                for sub_key in sorted(value):
                    sub_name = f"{name}:{names.claim(safe_identifier(str(sub_key).replace('-', '_'), 'value'))}"
                    self._write_attribute(spec, prim_path, f"{key}.{sub_key}", value[sub_key], sub_name, depth + 1)
                return
            w.attribute(spec, name, "string", to_json(value), custom=True, custom_data={**data, "ifcEncoding": "json"})
            return
        typed = _typed(value)
        if typed is None:
            w.attribute(spec, name, "string", to_json(value), custom=True, custom_data={**data, "ifcEncoding": "json"})
            return
        type_name, converted = typed
        allowed = None
        if isinstance(schema_entry, dict):
            description = schema_entry.get("value", {})
            if description.get("dataType") == "Enum" and type_name == "string":
                type_name = "token"
                allowed = description.get("enumRestrictions", {}).get("options")
            quantity_kind = description.get("quantityKind")
            if quantity_kind:
                data["ifcxQuantityKind"] = str(quantity_kind)
        w.attribute(
            spec,
            name,
            type_name,
            converted,
            custom=True,
            display_name=key.split("::")[-1],
            display_group=key.rsplit("::", 1)[0] if "::" in key else None,
            custom_data=data,
            allowed_tokens=allowed,
        )

    def _resolve_references(self) -> None:
        for prim_path, name, references, data in self.pending_refs:
            spec = self.layer.GetPrimAtPath(prim_path)
            targets = [p for ref in references for p in self.prims_by_source.get(ref, [])]
            if targets:
                self.writer.relationship(spec, name, targets, custom=True, custom_data=data)
            else:
                self.writer.attribute(
                    spec,
                    name,
                    "string",
                    to_json({"ref": references[0]}),
                    custom=True,
                    custom_data={**data, "ifcEncoding": "json"},
                )
                self.warnings.append(f"Unresolved IFCX reference {references[0]} on {prim_path}")

    # ------------------------------------------------------------------ geometry

    def _write_mesh(self, path: str, mesh: dict[str, Any], color: Any) -> None:
        from ifcusd.usd.sdf import Gf, Vt

        points = np.asarray(mesh.get("points", []), dtype=np.float64).reshape(-1, 3) * self.scale
        indices = np.asarray(mesh.get("faceVertexIndices", []), dtype=np.int32)
        counts = mesh.get("faceVertexCounts")
        counts = (
            np.asarray(counts, dtype=np.int32) if counts is not None else np.full(indices.size // 3, 3, dtype=np.int32)
        )
        if counts.sum() != indices.size or (indices.size and indices.max() >= len(points)):
            self.warnings.append(f"Invalid mesh topology skipped at {path}")
            return
        w = self.writer
        spec = w.prim(path, "Mesh")
        w.attribute(spec, "subdivisionScheme", "token", "none")
        w.attribute(spec, "doubleSided", "bool", bool(self.options.double_sided))
        w.attribute(spec, "points", "point3f[]", Vt.Vec3fArray.FromNumpy(points.astype(np.float32)), uniform=False)
        w.attribute(spec, "faceVertexCounts", "int[]", Vt.IntArray.FromNumpy(counts), uniform=False)
        w.attribute(spec, "faceVertexIndices", "int[]", Vt.IntArray.FromNumpy(indices), uniform=False)
        normals = _flat_normals(points, counts, indices)
        if normals is not None:
            w.attribute(
                spec,
                "normals",
                "normal3f[]",
                Vt.Vec3fArray.FromNumpy(normals),
                uniform=False,
                interpolation="faceVarying",
            )
        if len(points):
            low, high = points.min(axis=0), points.max(axis=0)
            w.attribute(
                spec,
                "extent",
                "float3[]",
                Vt.Vec3fArray([Gf.Vec3f(*low.tolist()), Gf.Vec3f(*high.tolist())]),
                uniform=False,
            )
        if color is not None:
            w.attribute(
                spec,
                "primvars:displayColor",
                "color3f[]",
                Vt.Vec3fArray([Gf.Vec3f(*color[0])]),
                uniform=False,
                interpolation="constant",
            )
            if color[1] < 1.0:
                w.attribute(
                    spec,
                    "primvars:displayOpacity",
                    "float[]",
                    Vt.FloatArray([float(color[1])]),
                    uniform=False,
                    interpolation="constant",
                )
        self.counts["meshes"] += 1

    def _write_curves(self, path: str, curves: dict[str, Any], color: Any) -> None:
        from ifcusd.usd.geometry import CURVE_WIDTH_METRES
        from ifcusd.usd.sdf import Gf, Vt

        points = np.asarray(curves.get("points", []), dtype=np.float64).reshape(-1, 3) * self.scale
        if len(points) < 2:
            return
        counts = curves.get("curveVertexCounts") or [len(points)]
        w = self.writer
        spec = w.prim(path, "BasisCurves")
        w.attribute(spec, "type", "token", "linear")
        w.attribute(spec, "curveVertexCounts", "int[]", Vt.IntArray([int(c) for c in counts]), uniform=False)
        w.attribute(spec, "points", "point3f[]", Vt.Vec3fArray.FromNumpy(points.astype(np.float32)), uniform=False)
        w.attribute(
            spec,
            "widths",
            "float[]",
            Vt.FloatArray([CURVE_WIDTH_METRES * self.scale]),
            uniform=False,
            interpolation="constant",
        )
        low, high = points.min(axis=0), points.max(axis=0)
        w.attribute(
            spec,
            "extent",
            "float3[]",
            Vt.Vec3fArray([Gf.Vec3f(*low.tolist()), Gf.Vec3f(*high.tolist())]),
            uniform=False,
        )
        if color is not None:
            w.attribute(
                spec,
                "primvars:displayColor",
                "color3f[]",
                Vt.Vec3fArray([Gf.Vec3f(*color[0])]),
                uniform=False,
                interpolation="constant",
            )
        self.counts["curves"] += 1

    def _write_points(self, path: str, cloud: tuple[np.ndarray, np.ndarray | None, str], color: Any) -> None:
        from ifcusd.usd.geometry import CURVE_WIDTH_METRES
        from ifcusd.usd.sdf import Gf, Vt

        positions, colors, key = cloud
        points = (positions * self.scale).astype(np.float32)
        w = self.writer
        spec = w.prim(path, "Points")
        w.attribute(spec, "points", "point3f[]", Vt.Vec3fArray.FromNumpy(points), uniform=False)
        w.attribute(
            spec,
            "widths",
            "float[]",
            Vt.FloatArray([CURVE_WIDTH_METRES * self.scale]),
            uniform=False,
            interpolation="constant",
        )
        low, high = points.min(axis=0), points.max(axis=0)
        w.attribute(
            spec,
            "extent",
            "float3[]",
            Vt.Vec3fArray([Gf.Vec3f(*low.tolist()), Gf.Vec3f(*high.tolist())]),
            uniform=False,
        )
        if colors is not None and len(colors) == len(points):
            w.attribute(
                spec,
                "primvars:displayColor",
                "color3f[]",
                Vt.Vec3fArray.FromNumpy(colors.astype(np.float32)),
                uniform=False,
                interpolation="vertex",
            )
        elif color is not None:
            w.attribute(
                spec,
                "primvars:displayColor",
                "color3f[]",
                Vt.Vec3fArray([Gf.Vec3f(*color[0])]),
                uniform=False,
                interpolation="constant",
            )
        spec.customData = {"ifcxKey": key}
        self.counts["points"] = self.counts.get("points", 0) + 1

    def _material(self, color: tuple[tuple[float, float, float], float]) -> str:
        rgb, opacity = color
        key = "rgba:" + ",".join(f"{c:.4f}" for c in (*rgb, opacity))
        spec = self.materials.get(key)
        if spec is None:
            digest = hashlib.sha1(key.encode("ascii")).hexdigest()[:8]
            spec = MaterialSpec(key=key, name=f"Color_{digest}", display_name=key, base_color=rgb, opacity=opacity)
            self.materials[key] = spec
        return f"{self.root}/Looks/{spec.name}"


# ---------------------------------------------------------------------- helpers


def _coverage(nodes: dict[str, Any], written: set[str]) -> dict[str, int]:
    """Prims for composed nodes; inherited-only nodes live on in the prims that inherit them."""
    inherited = {target.split("/")[0] for node in nodes.values() for target in node.inherits.values() if target}
    counts = {"inFile": len(nodes), "prim": 0, "native": 0, "attributes": 0, "missing": 0}
    for path in nodes:
        if path in written:
            counts["prim"] += 1
        elif "/" in path:
            counts["native"] += 1  # override applied to a nested composed node
        elif path in inherited:
            counts["attributes"] += 1
        else:
            counts["missing"] += 1
    return counts


def _sidecar_record(path: str, node: ComposedNode, ifc_class: str, attributes: dict[str, Any]) -> dict[str, Any]:
    properties = []
    for key in sorted(attributes):
        if key.startswith("usd::") or key == _CLASS_KEY:
            continue
        value = attributes[key]
        properties.append(
            {
                "set": key.rsplit("::", 1)[0] if "::" in key else "",
                "name": key.rsplit("::", 1)[-1],
                "value": value if isinstance(value, str) else to_json(value),
                "type": type(value).__name__,
                "unit": "",
                "quantity": False,
            }
        )
    name = attributes.get("bsi::ifc::prop::Name")
    return {
        "path": path,
        "globalId": node.source,
        "ifcClass": ifc_class,
        "name": str(name) if name else path.rsplit("/", 1)[-1],
        "properties": properties,
    }


def _class_code(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("code"):
        return str(value["code"])
    if isinstance(value, str):
        return value
    return None


def _color(rgb: Any, opacity: Any, inherited: Any) -> tuple[tuple[float, float, float], float]:
    base_rgb, base_opacity = inherited if inherited is not None else ((0.8, 0.8, 0.8), 1.0)
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        base_rgb = tuple(max(0.0, min(1.0, float(c))) for c in rgb[:3])  # type: ignore[assignment]
    if isinstance(opacity, (int, float)):
        base_opacity = max(0.0, min(1.0, float(opacity)))
    return base_rgb, base_opacity  # type: ignore[return-value]


def _decode_points(attributes: dict[str, Any]) -> tuple[np.ndarray, np.ndarray | None, str] | None:
    """Point clouds from ``points::array``, ``points::base64`` (float32 LE) or ``pcd::base64``."""
    import base64

    value = attributes.get("points::array")
    if isinstance(value, dict) and value.get("positions"):
        positions = np.asarray(value["positions"], dtype=np.float64).reshape(-1, 3)
        colors = np.asarray(value["colors"], dtype=np.float64).reshape(-1, 3) if value.get("colors") else None
        return positions, colors, "points::array"
    value = attributes.get("points::base64")
    if isinstance(value, dict) and value.get("positions"):
        positions = np.frombuffer(base64.b64decode(value["positions"]), dtype="<f4").astype(np.float64).reshape(-1, 3)
        colors = None
        if value.get("colors"):
            colors = np.frombuffer(base64.b64decode(value["colors"]), dtype="<f4").astype(np.float64).reshape(-1, 3)
        return positions, colors, "points::base64"
    value = attributes.get("pcd::base64")
    if isinstance(value, str) and value:
        positions, colors = _parse_pcd(base64.b64decode(value))
        return positions, colors, "pcd::base64"
    return None


def _parse_pcd(data: bytes) -> tuple[np.ndarray, np.ndarray | None]:
    """Minimal PCD v0.7 reader (``DATA ascii`` and ``DATA binary``)."""
    header: dict[str, list[str]] = {}
    offset = 0
    while True:
        end = data.index(b"\n", offset)
        line = data[offset:end].decode("ascii", errors="replace").strip()
        offset = end + 1
        if not line or line.startswith("#"):
            continue
        key, *values = line.split()
        header[key.upper()] = values
        if key.upper() == "DATA":
            break
    fields = header["FIELDS"]
    sizes = [int(v) for v in header.get("SIZE", ["4"] * len(fields))]
    types = header.get("TYPE", ["F"] * len(fields))
    counts = [int(v) for v in header.get("COUNT", ["1"] * len(fields))]
    total = int(header.get("POINTS", [str(int(header["WIDTH"][0]) * int(header.get("HEIGHT", ["1"])[0]))])[0])
    kinds = {"F": "f", "U": "u", "I": "i"}
    dtype = np.dtype(
        [
            (name, f"<{kinds[t]}{s}", (c,) if c > 1 else ())
            for name, s, t, c in zip(fields, sizes, types, counts, strict=True)
        ]
    )
    mode = header["DATA"][0].lower()
    if mode == "ascii":
        rows = np.loadtxt(data[offset:].decode("ascii").splitlines(), dtype=np.float64, ndmin=2)[:total]
        records = {name: rows[:, i] for i, name in enumerate(fields)}
    elif mode == "binary":
        array = np.frombuffer(data, dtype=dtype, count=total, offset=offset)
        records = {name: array[name] for name in fields}
    else:
        raise ValueError(f"unsupported PCD DATA mode {mode!r}")
    positions = np.stack([np.asarray(records[a], dtype=np.float64) for a in ("x", "y", "z")], axis=1)
    colors = None
    for name in ("rgb", "rgba"):
        if name in records:
            packed = np.asarray(records[name], dtype=np.float32).view(np.uint32)
            colors = np.stack([(packed >> 16) & 255, (packed >> 8) & 255, packed & 255], axis=1) / 255.0
            break
    return positions, colors


def _typed(value: Any) -> tuple[str, Any] | None:
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, int):
        return ("int", value) if -(2**31) <= value < 2**31 else ("int64", value)
    if isinstance(value, float):
        return "double", value
    if isinstance(value, str):
        return "string", value
    if isinstance(value, list) and value:
        if all(isinstance(v, bool) for v in value):
            return "bool[]", value
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            if all(isinstance(v, int) for v in value):
                return "int[]", value
            return "double[]", [float(v) for v in value]
        if all(isinstance(v, str) for v in value):
            return "string[]", value
    return None


def _flat_normals(points: np.ndarray, counts: np.ndarray, indices: np.ndarray) -> np.ndarray | None:
    """Face-varying flat normals (BIM geometry is faceted, so smooth normals would be wrong)."""
    if not len(points) or not indices.size:
        return None
    if np.all(counts == 3):
        triangles = points[indices].reshape(-1, 3, 3)
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(cross, axis=1, keepdims=True)
        cross = np.where(lengths > 0, cross / np.where(lengths > 0, lengths, 1.0), np.array([0.0, 0.0, 1.0]))
        return np.repeat(cross, 3, axis=0).astype(np.float32)
    normals = np.zeros((indices.size, 3), dtype=np.float64)
    offset = 0
    for count in counts.tolist():
        corners = points[indices[offset : offset + count]]
        # Newell's method handles triangles and planar polygons alike.
        shifted = np.roll(corners, -1, axis=0)
        normal = np.array(
            [
                np.sum((corners[:, 1] - shifted[:, 1]) * (corners[:, 2] + shifted[:, 2])),
                np.sum((corners[:, 2] - shifted[:, 2]) * (corners[:, 0] + shifted[:, 0])),
                np.sum((corners[:, 0] - shifted[:, 0]) * (corners[:, 1] + shifted[:, 1])),
            ]
        )
        length = np.linalg.norm(normal)
        normals[offset : offset + count] = normal / length if length > 0 else (0.0, 0.0, 1.0)
        offset += count
    return normals.astype(np.float32)
