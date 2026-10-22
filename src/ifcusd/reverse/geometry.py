"""USD geometry to IFC representations.

Meshes (including ``GeomSubset`` material partitions) and the implicit gprims
robots are often built from (``Cube``, ``Sphere``, ``Cylinder``, ``Cone``,
``Capsule``, ``Plane``) are tessellated into triangles and written as:

* ``IfcTriangulatedFaceSet`` (IFC4 and later, representation type ``Tessellation``)
* ``IfcShellBasedSurfaceModel`` of triangular faces (IFC2X3, type ``SurfaceModel``)

Every distinct render material becomes an ``IfcSurfaceStyle`` (reusing the
source style when the USD material came from it). Instanced prims that share
a prototype become ``IfcMappedItem`` instances of one ``IfcRepresentationMap``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ifcopenshell.api.context
import ifcopenshell.api.style
import ifcopenshell.util.representation
import numpy as np

from ifcusd.dependencies import import_usd
from ifcusd.reverse.attributes import create
from ifcusd.reverse.stage import UsdScene
from ifcusd.reverse.values import ValueFactory

_usd = import_usd()
Usd, UsdGeom, UsdShade = _usd.Usd, _usd.UsdGeom, _usd.UsdShade

LATHE_SEGMENTS = 24
COORDINATE_DECIMALS = 6


@dataclass(frozen=True)
class Appearance:
    key: str
    name: str
    color: tuple[float, float, float]
    opacity: float
    step_id: int | None = None


@dataclass
class MeshPart:
    points: np.ndarray  # (n, 3) metres in the target frame
    triangles: np.ndarray  # (m, 3) indices into points
    appearance: Appearance | None


# ---------------------------------------------------------------------- reading USD


def mesh_parts(scene: UsdScene, prim: Any, to_target: np.ndarray) -> list[MeshPart]:
    """Triangulated parts of one mesh-like prim, in the target frame (metres).

    ``to_target`` maps the prim's local frame (metres) into the target frame.
    """
    tessellated = _tessellate(prim, scene.time)
    if tessellated is None:
        return []
    points, triangles, faces = tessellated
    if len(points) == 0 or len(triangles) == 0:
        return []
    local = points.astype(np.float64) * scene.meters_per_unit
    transformed = (np.c_[local, np.ones(len(local))] @ to_target.T)[:, :3]
    if np.linalg.det(to_target[:3, :3]) < 0:  # mirrored: keep faces pointing outwards
        triangles = triangles[:, ::-1]
    parts = []
    for selection, appearance in _partitions(prim, faces, scene.time, scene.synthetic_looks):
        selected = triangles[selection] if selection is not None else triangles
        if len(selected):
            parts.append(MeshPart(transformed, selected, appearance))
    return parts


def _tessellate(prim: Any, time: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
    """``(points, triangles, face index of each triangle)`` in stage units, or ``None``."""
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get(time)
        counts = mesh.GetFaceVertexCountsAttr().Get(time)
        indices = mesh.GetFaceVertexIndicesAttr().Get(time)
        if points is None or counts is None or indices is None:
            return None
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        triangles, faces = triangulate(np.asarray(counts), np.asarray(indices))
        valid = np.all((triangles >= 0) & (triangles < len(points)), axis=1)
        triangles, faces = triangles[valid], faces[valid]
        if mesh.GetOrientationAttr().Get(time) == UsdGeom.Tokens.leftHanded:
            triangles = triangles[:, ::-1]
        return points, triangles, faces
    primitive = primitive_mesh(prim, time)
    if primitive is None:
        return None
    return primitive[0], primitive[1], None


def triangulate(counts: np.ndarray, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fan-triangulate polygons; returns triangles and the face each came from."""
    counts = counts.astype(np.int64)
    indices = indices.astype(np.int64)
    if counts.size == 0 or indices.size == 0 or int(counts.sum()) > indices.size:
        return np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    per_face = np.maximum(counts - 2, 0)
    face = np.repeat(np.arange(len(counts)), per_face)
    offset = np.arange(int(per_face.sum())) - np.repeat(np.cumsum(per_face) - per_face, per_face)
    base = starts[face]
    triangles = np.stack([indices[base], indices[base + offset + 1], indices[base + offset + 2]], axis=1)
    return triangles, face


def _partitions(
    prim: Any, faces: np.ndarray | None, time: Any, synthetic: str | None
) -> list[tuple[np.ndarray | None, Appearance | None]]:
    """Triangle selections per material: ``GeomSubset`` partitions, then the rest of the mesh."""
    own = appearance(prim, time, synthetic)
    if faces is None:
        return [(None, own)]
    subsets = [
        UsdGeom.Subset(child)
        for child in prim.GetFilteredChildren(Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))
        if child.IsA(UsdGeom.Subset)
    ]
    subsets = [
        s
        for s in subsets
        if s.GetElementTypeAttr().Get() == UsdGeom.Tokens.face and s.GetFamilyNameAttr().Get() == "materialBind"
    ]
    if not subsets:
        return [(None, own)]
    covered = np.zeros(len(faces), dtype=bool)
    result: list[tuple[np.ndarray | None, Appearance | None]] = []
    for subset in subsets:
        face_ids = np.asarray(subset.GetIndicesAttr().Get(time) or [], dtype=np.int64)
        selection = np.isin(faces, face_ids)
        covered |= selection
        result.append((selection, appearance(subset.GetPrim(), time, synthetic) or own))
    if not covered.all():
        result.append((~covered, own))
    return result


def appearance(prim: Any, time: Any, synthetic: str | None = None) -> Appearance | None:
    """Colour and opacity from the bound material, else from ``primvars:displayColor``.

    Materials under ``synthetic`` (IfcUSD's ``Looks`` scope) without an IFC
    origin are IfcOpenShell's default colours for unstyled elements: they give
    no appearance, so no style is invented for the IFC model.
    """
    material, _relationship = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if material:
        path = material.GetPath().pathString
        if synthetic and path.startswith(synthetic) and not material.GetPrim().HasAttribute("ifc:class"):
            return None
        return _material_appearance(material)
    if not prim.IsA(UsdGeom.Gprim):
        return None
    gprim = UsdGeom.Gprim(prim)
    colors = gprim.GetDisplayColorPrimvar().ComputeFlattened(time)
    if not colors:
        return None
    color = tuple(round(float(c), 4) for c in colors[0])
    opacities = gprim.GetDisplayOpacityPrimvar().ComputeFlattened(time)
    opacity = round(float(opacities[0]), 4) if opacities else 1.0
    return Appearance(f"color:{color}:{opacity}", "Colour {:.3f} {:.3f} {:.3f}".format(*color), color, opacity)


def _material_appearance(material: Any) -> Appearance:
    prim = material.GetPrim()
    color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    opacity = 1.0
    shader = _surface_shader(material)
    if shader is not None:
        for name in ("diffuseColor", "diffuse_color_constant", "base_color"):
            value = _input_value(shader, name)
            if value is not None:
                color = tuple(round(float(c), 4) for c in value)[:3]  # type: ignore[assignment]
                break
        for name in ("opacity", "opacity_constant"):
            value = _input_value(shader, name)
            if value is not None:
                opacity = round(float(value), 4)
                break
    step_id = None
    if str(prim.GetAttribute("ifc:class").Get() or "") == "IfcSurfaceStyle":
        step_value = prim.GetAttribute("ifc:stepId").Get()
        step_id = int(step_value) if step_value else None
    name = prim.GetMetadata("displayName") or prim.GetName()
    return Appearance(prim.GetPath().pathString, str(name), color, opacity, step_id)


def _surface_shader(material: Any) -> Any | None:
    for contexts in ([UsdShade.Tokens.universalRenderContext], ["mdl"]):
        try:
            source = material.ComputeSurfaceSource(contexts)
        except Exception:
            continue
        shader = source[0] if isinstance(source, tuple) else source
        if shader and shader.GetPrim().IsValid():
            return shader
    return None


def _input_value(shader: Any, name: str) -> Any | None:
    shader_input = shader.GetInput(name)
    if not shader_input:
        return None
    if shader_input.HasConnectedSource():
        return None  # textured: no constant to read
    return shader_input.Get()


# ---------------------------------------------------------------------- primitives


def primitive_mesh(prim: Any, time: Any) -> tuple[np.ndarray, np.ndarray] | None:
    """Triangles for the implicit gprims, in their local frame (stage units)."""
    type_name = prim.GetTypeName()

    def get(name: str, default: float) -> float:
        attribute = prim.GetAttribute(name)
        value = attribute.Get(time) if attribute else None
        return float(value) if value is not None else default

    axis = str(prim.GetAttribute("axis").Get(time) or "Z") if prim.GetAttribute("axis") else "Z"
    if type_name == "Cube":
        return _cube(get("size", 2.0))
    if type_name == "Sphere":
        radius = get("radius", 1.0)
        return _lathe(_arc(radius, 0.0, math.pi, 12, 0.0))
    if type_name in ("Cylinder", "Cylinder_1"):
        height = get("height", 2.0)
        bottom = get("radiusBottom", get("radius", 1.0))
        top = get("radiusTop", get("radius", 1.0))
        return _axis(_lathe([(bottom, -height / 2), (top, height / 2)]), axis)
    if type_name == "Cone":
        height = get("height", 2.0)
        return _axis(_lathe([(get("radius", 1.0), -height / 2), (0.0, height / 2)]), axis)
    if type_name in ("Capsule", "Capsule_1"):
        height = get("height", 1.0)
        radius = get("radius", get("radiusBottom", 0.5))
        profile = _arc(radius, 0.0, math.pi / 2, 6, -height / 2) + _arc(radius, math.pi / 2, math.pi, 6, height / 2)
        return _axis(_lathe(profile), axis)
    if type_name == "Plane":
        width, length = get("width", 2.0) / 2, get("length", 2.0) / 2
        points = np.array([(-width, -length, 0), (width, -length, 0), (width, length, 0), (-width, length, 0)], float)
        return _axis((points, np.array([(0, 1, 2), (0, 2, 3)])), axis)
    return None


def _cube(size: float) -> tuple[np.ndarray, np.ndarray]:
    h = size / 2
    points = np.array([(x, y, z) for z in (-h, h) for y in (-h, h) for x in (-h, h)], dtype=np.float64)
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6)]
    triangles = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return points, np.array(triangles, dtype=np.int64)


def _arc(radius: float, start: float, stop: float, steps: int, z_offset: float) -> list[tuple[float, float]]:
    """Profile ``(radius, z)`` points of a circular arc from the bottom pole upwards."""
    angles = np.linspace(start, stop, steps + 1)
    return [(radius * math.sin(a), -radius * math.cos(a) + z_offset) for a in angles]


def _lathe(profile: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Revolve a ``(radius, z)`` profile around Z; closed with caps where the radius is not zero."""
    angles = np.linspace(0.0, 2 * math.pi, LATHE_SEGMENTS, endpoint=False)
    rings = [[(r * math.cos(a), r * math.sin(a), z) for a in angles] for r, z in profile]
    points = [p for ring in rings for p in ring]
    n = LATHE_SEGMENTS
    triangles = []
    for i in range(len(profile) - 1):
        for j in range(n):
            a, b = i * n + j, i * n + (j + 1) % n
            c, d = (i + 1) * n + (j + 1) % n, (i + 1) * n + j
            triangles += [(a, b, c), (a, c, d)]
    for ring_index, flip in ((0, True), (len(profile) - 1, False)):
        if profile[ring_index][0] > 0:
            center = len(points)
            points.append((0.0, 0.0, profile[ring_index][1]))
            for j in range(n):
                a, b = ring_index * n + j, ring_index * n + (j + 1) % n
                triangles.append((center, b, a) if flip else (center, a, b))
    return clean(np.array(points, dtype=np.float64), np.array(triangles, dtype=np.int64))


def _axis(mesh: tuple[np.ndarray, np.ndarray], axis: str) -> tuple[np.ndarray, np.ndarray]:
    """Rotate a Z-aligned primitive onto its ``axis`` (cyclic permutations keep handedness)."""
    points, triangles = mesh
    if axis == "X":
        points = points[:, [2, 0, 1]]
    elif axis == "Y":
        points = points[:, [1, 2, 0]]
    return points, triangles


def clean(points: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop degenerate triangles and unused points."""
    if len(triangles) == 0:
        return points[:0], triangles
    a, b, c = (points[triangles[:, k]] for k in range(3))
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    distinct = (
        (triangles[:, 0] != triangles[:, 1])
        & (triangles[:, 1] != triangles[:, 2])
        & (triangles[:, 0] != triangles[:, 2])
    )
    triangles = triangles[distinct & (area > 1e-14)]
    used = np.unique(triangles)
    remap = np.full(len(points), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return points[used], remap[triangles]


# ---------------------------------------------------------------------- writing IFC


class RepresentationWriter:
    """Writes body representations, styles and mapped items into one model."""

    def __init__(
        self,
        model: Any,
        factory: ValueFactory,
        *,
        length_scale: float,
        warn: Callable[[str], None],
    ) -> None:
        self.model = model
        self.factory = factory
        self.length_scale = length_scale
        self.warn = warn
        self.triangles = 0
        self.representations = 0
        self._context: Any = None
        self._styles: dict[str, Any] = {}
        self._maps: dict[str, Any] = {}

    @property
    def context(self) -> Any:
        if self._context is None:
            context = ifcopenshell.util.representation.get_context(self.model, "Model", "Body", "MODEL_VIEW")
            if context is None:
                parent = ifcopenshell.util.representation.get_context(self.model, "Model")
                if parent is None:
                    parent = ifcopenshell.api.context.add_context(self.model, context_type="Model")
                context = ifcopenshell.api.context.add_context(
                    self.model,
                    context_type="Model",
                    context_identifier="Body",
                    target_view="MODEL_VIEW",
                    parent=parent,
                )
            self._context = context
        return self._context

    def shape(self, parts: list[MeshPart]) -> Any | None:
        """An ``IfcShapeRepresentation`` of the parts, one item per appearance."""
        items = self._items(parts)
        if not items:
            return None
        self.representations += 1
        return create(
            self.model,
            "IfcShapeRepresentation",
            self.warn,
            ContextOfItems=self.context,
            RepresentationIdentifier="Body",
            RepresentationType="SurfaceModel" if self.factory.is_ifc2x3 else "Tessellation",
            Items=items,
        )

    def mapped_shape(self, instances: list[tuple[str, Callable[[], list[MeshPart]], np.ndarray]]) -> Any | None:
        """A ``MappedRepresentation`` of shared prototypes; ``None`` when a transform cannot be mapped.

        Each instance is ``(prototype key, parts in the prototype frame, prototype->product transform)``.
        """
        items = []
        for key, parts, transform in instances:
            operator = self._operator(transform)
            if operator is None:
                return None
            source = self._maps.get(key)
            if source is None:
                shape = self.shape(parts())
                if shape is None:
                    continue
                origin = create(
                    self.model,
                    "IfcAxis2Placement3D",
                    self.warn,
                    Location=self.model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
                )
                source = create(
                    self.model, "IfcRepresentationMap", self.warn, MappingOrigin=origin, MappedRepresentation=shape
                )
                self._maps[key] = source
            items.append(create(self.model, "IfcMappedItem", self.warn, MappingSource=source, MappingTarget=operator))
        if not items:
            return None
        self.representations += 1
        return create(
            self.model,
            "IfcShapeRepresentation",
            self.warn,
            ContextOfItems=self.context,
            RepresentationIdentifier="Body",
            RepresentationType="MappedRepresentation",
            Items=items,
        )

    def assign(self, product: Any, shape: Any) -> None:
        product.Representation = create(self.model, "IfcProductDefinitionShape", self.warn, Representations=[shape])

    # ------------------------------------------------------------------ items

    def _items(self, parts: list[MeshPart]) -> list[Any]:
        groups: dict[str, list[MeshPart]] = {}
        for part in parts:
            groups.setdefault(part.appearance.key if part.appearance else "", []).append(part)
        items = []
        for key in sorted(groups):
            members = groups[key]
            points, triangles = _merge(members)
            points, triangles = clean(points, triangles)
            if len(triangles) == 0:
                continue
            coordinates = np.round(points / self.length_scale, COORDINATE_DECIMALS)
            item = (
                self._surface_model(coordinates, triangles)
                if self.factory.is_ifc2x3
                else self._face_set(coordinates, triangles)
            )
            self.triangles += len(triangles)
            if members[0].appearance is not None:
                style = self._style(members[0].appearance)
                if style is not None:
                    ifcopenshell.api.style.assign_item_style(self.model, item=item, style=style)
            items.append(item)
        return items

    def _face_set(self, coordinates: np.ndarray, triangles: np.ndarray) -> Any:
        point_list = self.model.create_entity(
            "IfcCartesianPointList3D", CoordList=[tuple(float(v) for v in row) for row in coordinates]
        )
        return create(
            self.model,
            "IfcTriangulatedFaceSet",
            self.warn,
            Coordinates=point_list,
            CoordIndex=[tuple(int(i) + 1 for i in row) for row in triangles],
        )

    def _surface_model(self, coordinates: np.ndarray, triangles: np.ndarray) -> Any:
        points = [
            self.model.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in row))
            for row in coordinates
        ]
        faces = []
        for a, b, c in triangles.tolist():
            loop = self.model.create_entity("IfcPolyLoop", Polygon=[points[a], points[b], points[c]])
            bound = self.model.create_entity("IfcFaceOuterBound", Bound=loop, Orientation=True)
            faces.append(self.model.create_entity("IfcFace", Bounds=[bound]))
        shell = self.model.create_entity("IfcOpenShell", CfsFaces=faces)
        return self.model.create_entity("IfcShellBasedSurfaceModel", SbsmBoundary=[shell])

    def _style(self, look: Appearance) -> Any | None:
        if look.key in self._styles:
            return self._styles[look.key]
        style = None
        if look.step_id is not None:
            try:
                candidate = self.model.by_id(look.step_id)
            except Exception:
                candidate = None
            if candidate is not None and candidate.is_a("IfcSurfaceStyle"):
                style = candidate
        if style is None:
            style = ifcopenshell.api.style.add_style(self.model, name=look.name)
            attributes: dict[str, Any] = {
                "SurfaceColour": {"Name": None, "Red": look.color[0], "Green": look.color[1], "Blue": look.color[2]},
                "Transparency": round(1.0 - look.opacity, 4),
            }
            if self.factory.is_ifc2x3:
                attributes["ReflectanceMethod"] = "NOTDEFINED"
            ifcopenshell.api.style.add_surface_style(
                self.model,
                style=style,
                ifc_class="IfcSurfaceStyleRendering" if self.factory.is_ifc2x3 else "IfcSurfaceStyleShading",
                attributes=attributes,
            )
        self._styles[look.key] = style
        return style

    def _operator(self, transform: np.ndarray) -> Any | None:
        """``IfcCartesianTransformationOperator3D`` for a rotation + uniform scale + translation."""
        rotation = transform[:3, :3]
        determinant = float(np.linalg.det(rotation))
        if determinant <= 0:
            return None
        scale = determinant ** (1.0 / 3.0)
        axes = rotation / scale
        if not np.allclose(axes.T @ axes, np.eye(3), atol=1e-6):
            return None
        origin = np.round(transform[:3, 3] / self.length_scale, COORDINATE_DECIMALS)

        def direction(vector: np.ndarray) -> Any:
            return self.model.create_entity(
                "IfcDirection", DirectionRatios=tuple(float(v) for v in np.round(vector, 12))
            )

        return create(
            self.model,
            "IfcCartesianTransformationOperator3D",
            self.warn,
            Axis1=direction(axes[:, 0]),
            Axis2=direction(axes[:, 1]),
            LocalOrigin=self.model.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in origin)),
            Scale=None if math.isclose(scale, 1.0, abs_tol=1e-9) else float(scale),
            Axis3=direction(axes[:, 2]),
        )


def _merge(parts: list[MeshPart]) -> tuple[np.ndarray, np.ndarray]:
    points, triangles, offset = [], [], 0
    for part in parts:
        points.append(part.points)
        triangles.append(part.triangles + offset)
        offset += len(part.points)
    return np.concatenate(points), np.concatenate(triangles)
