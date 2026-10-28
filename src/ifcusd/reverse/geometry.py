"""USD geometry to IFC representations.

Meshes (including ``GeomSubset`` material partitions) and the implicit gprims
robots are often built from (``Cube``, ``Sphere``, ``Cylinder``, ``Cone``,
``Capsule``, ``Plane``) are tessellated into triangles and written as:

* ``IfcTriangulatedFaceSet`` (IFC4 and later, representation type ``Tessellation``)
* ``IfcShellBasedSurfaceModel`` of triangular faces (IFC2X3, type ``SurfaceModel``)

Every distinct render material becomes an ``IfcSurfaceStyle`` (reusing the
source style when the USD material came from it). Colours are read from
UsdPreviewSurface, MDL (OmniPBR, OmniSurface, OmniGlass) and MaterialX
networks, through material interface connections; a textured material takes
the mesh's ``displayColor``. Meshes without materials keep per-face
``displayColor``. New elements also get their USD materials as
``IfcMaterial``. Instanced prims that share a prototype become
``IfcMappedItem`` instances of one ``IfcRepresentationMap``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ifcopenshell.api.context
import ifcopenshell.api.material
import ifcopenshell.api.style
import ifcopenshell.util.representation
import numpy as np

from ifcusd.dependencies import import_usd
from ifcusd.ifc.util import attr as util_attr
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
    """Triangle selections per material: ``GeomSubset`` partitions, then the rest of the mesh.

    A mesh without materials whose ``displayColor`` varies per face is split by colour.
    """
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
        if own is not None and own.key.startswith("color:"):
            return _color_partitions(prim, faces, time) or [(None, own)]
        return [(None, own)]
    covered = np.zeros(len(faces), dtype=bool)
    result: list[tuple[np.ndarray | None, Appearance | None]] = []
    for subset in subsets:
        face_ids = np.asarray(subset.GetIndicesAttr().Get(time) or [], dtype=np.int64)
        selection = np.isin(faces, face_ids)
        covered |= selection
        result.append((selection, appearance(subset.GetPrim(), time, synthetic, gprim=prim) or own))
    if not covered.all():
        result.append((~covered, own))
    return result


MAX_FACE_COLORS = 64  # more distinct face colours than this is a colour map, not materials


def _color_partitions(prim: Any, faces: np.ndarray, time: Any) -> list[tuple[np.ndarray, Appearance]] | None:
    """Triangles grouped by per-face ``displayColor`` (uniform, vertex or faceVarying), or ``None``."""
    if not prim.IsA(UsdGeom.Mesh):
        return None
    mesh = UsdGeom.Mesh(prim)
    primvar = mesh.GetDisplayColorPrimvar()
    colors = primvar.ComputeFlattened(time)
    counts = mesh.GetFaceVertexCountsAttr().Get(time)
    indices = mesh.GetFaceVertexIndicesAttr().Get(time)
    if not colors or len(colors) < 2 or counts is None or indices is None:
        return None
    colors = np.round(np.asarray(colors, dtype=np.float64).reshape(-1, 3), 3)
    counts = np.asarray(counts, dtype=np.int64)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    interpolation = primvar.GetInterpolation()
    if interpolation == UsdGeom.Tokens.uniform and len(colors) == len(counts):
        per_face = colors
    elif interpolation == UsdGeom.Tokens.faceVarying and len(colors) == len(indices):
        per_face = colors[starts]  # the colour of each face's first corner
    elif interpolation == UsdGeom.Tokens.vertex and len(colors) > int(np.max(indices, initial=0)):
        per_face = colors[np.asarray(indices, dtype=np.int64)[starts]]
    else:
        return None
    distinct, keys = np.unique(per_face, axis=0, return_inverse=True)
    if len(distinct) < 2 or len(distinct) > MAX_FACE_COLORS:
        return None
    opacities = mesh.GetDisplayOpacityPrimvar().ComputeFlattened(time)
    opacity = round(float(opacities[0]), 4) if opacities else 1.0
    triangle_keys = np.asarray(keys).reshape(-1)[faces]
    return [(triangle_keys == k, _color_appearance(tuple(distinct[k]), opacity)) for k in range(len(distinct))]


def appearance(prim: Any, time: Any, synthetic: str | None = None, *, gprim: Any = None) -> Appearance | None:
    """Colour and opacity from the bound material, else from ``primvars:displayColor``.

    ``gprim`` is the mesh a ``GeomSubset`` belongs to, whose ``displayColor``
    stands in for a textured material. Materials under ``synthetic`` (IfcUSD's
    ``Looks`` scope) without an IFC origin are IfcOpenShell's default colours
    for unstyled elements: they give no appearance, so no style is invented
    for the IFC model.
    """
    gprim = gprim if gprim is not None else prim
    display = _display_color(gprim, time)
    material = _bound_material(prim)
    if material:
        path = material.GetPath().pathString
        if synthetic and path.startswith(synthetic) and not material.GetPrim().HasAttribute("ifc:class"):
            return None
        return _material_appearance(material, display)
    if display is None:
        return None
    return _color_appearance(*display)


def _bound_material(prim: Any) -> Any:
    """The render material: bound for all purposes or the full render, else for preview."""
    binding = UsdShade.MaterialBindingAPI(prim)
    for purpose in (UsdShade.Tokens.full, UsdShade.Tokens.preview):
        material, _relationship = binding.ComputeBoundMaterial(purpose)
        if material:
            return material
    return None


def _display_color(prim: Any, time: Any) -> tuple[tuple[float, float, float], float] | None:
    """The first ``displayColor`` and ``displayOpacity`` of a gprim."""
    if not prim.IsA(UsdGeom.Gprim):
        return None
    gprim = UsdGeom.Gprim(prim)
    colors = gprim.GetDisplayColorPrimvar().ComputeFlattened(time)
    if not colors:
        return None
    opacities = gprim.GetDisplayOpacityPrimvar().ComputeFlattened(time)
    color = tuple(round(float(c), 4) for c in colors[0])
    return color, round(float(opacities[0]), 4) if opacities else 1.0  # type: ignore[return-value]


def _color_appearance(color: tuple[float, ...], opacity: float) -> Appearance:
    color = tuple(round(float(c), 4) for c in color[:3])
    return Appearance(f"color:{color}:{opacity}", "Colour {:.3f} {:.3f} {:.3f}".format(*color), color, opacity)  # type: ignore[arg-type]


# Constant colour and opacity inputs of the common surface shaders: UsdPreviewSurface, OmniPBR,
# OmniSurface, MaterialX / OpenPBR standard surfaces and OmniGlass.
_COLOR_INPUTS = (
    "diffuseColor",
    "diffuse_color_constant",
    "diffuse_reflection_color",
    "base_color",
    "baseColor",
    "glass_color",
    "diffuse_tint",
)
_OPACITY_INPUTS = ("opacity", "opacity_constant", "geometry_opacity")
# Colours MDL materials render with when their colour input is not authored.
_MDL_DEFAULT_COLORS = {"OmniPBR": (0.2, 0.2, 0.2), "OmniPBR_ClearCoat": (0.2, 0.2, 0.2)}
DEFAULT_COLOR = (0.8, 0.8, 0.8)


def _material_appearance(material: Any, display: tuple[tuple[float, float, float], float] | None) -> Appearance:
    """Colour and opacity of a material; ``display`` (the gprim's colour) stands in for textures."""
    prim = material.GetPrim()
    color: tuple[float, float, float] | None = None
    opacity: float | None = None
    shader = _surface_shader(material)
    if shader is not None:
        for name in _COLOR_INPUTS:
            value = _input_value(shader, name)
            if value is not None:
                color = tuple(round(float(c), 4) for c in value)[:3]  # type: ignore[assignment]
                break
        enabled = _input_value(shader, "enable_opacity")  # OmniPBR is opaque unless this is on
        if enabled is True or (enabled is None and not (_mdl_name(shader) or "").startswith("OmniPBR")):
            for name in _OPACITY_INPUTS:
                value = _input_value(shader, name)
                if value is not None:
                    values = [float(v) for v in value] if _iterable(value) else [float(value)]
                    opacity = round(sum(values) / len(values), 4)
                    break
    key = prim.GetPath().pathString
    if color is None:  # textured, or an MDL material with its default colour
        mdl = _mdl_name(shader) if shader is not None else None
        if display is not None:
            color = display[0]
            key = f"{key}|{color}"  # the same textured material on differently coloured meshes
        else:
            color = _MDL_DEFAULT_COLORS.get(mdl or "", DEFAULT_COLOR)  # type: ignore[assignment]
    if opacity is None:
        opacity = display[1] if display is not None else 1.0
    step_id = None
    if str(prim.GetAttribute("ifc:class").Get() or "") == "IfcSurfaceStyle":
        step_value = prim.GetAttribute("ifc:stepId").Get()
        step_id = int(step_value) if step_value else None
    name = prim.GetMetadata("displayName") or prim.GetName()
    return Appearance(key, str(name), color, opacity, step_id)  # type: ignore[arg-type]


def _mdl_name(shader: Any) -> str | None:
    identifier = shader.GetPrim().GetAttribute("info:mdl:sourceAsset:subIdentifier")
    return str(identifier.Get()) if identifier and identifier.HasAuthoredValue() else None


def _iterable(value: Any) -> bool:
    try:
        iter(value)
    except TypeError:
        return False
    return not isinstance(value, str)


def _surface_shader(material: Any) -> Any | None:
    """The surface shader for the universal (UsdPreviewSurface), MDL or MaterialX context."""
    for context in (UsdShade.Tokens.universalRenderContext, "mdl", "mtlx"):
        # usd-core binds ComputeSurfaceSource with one render context; some releases take a list.
        for argument in (context, [context]):
            try:
                source = material.ComputeSurfaceSource(argument)
            except Exception:  # Boost.Python ArgumentError: the other signature
                continue
            shader = source[0] if isinstance(source, tuple) else source
            if shader and shader.GetPrim().IsValid():
                return shader
            break
    return None


def _input_value(shader: Any, name: str) -> Any | None:
    """The constant value of a shader input, following connections to the material's interface.

    ``None`` when the input is missing, unset or driven by another shader (a texture).
    """
    shader_input = shader.GetInput(name)
    if not shader_input:
        return None
    for attribute in shader_input.GetValueProducingAttributes():
        if UsdShade.Input.IsInput(attribute):
            value = attribute.Get()
            if value is not None:
                return value
    return None


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
        self.materials = 0
        self._context: Any = None
        self._styles: dict[str, Any] = {}
        self._maps: dict[str, Any] = {}
        self._materials: dict[str, Any] = {}
        self._existing_materials: dict[str, Any] | None = None

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

    def associate_materials(self, product: Any, parts: list[MeshPart]) -> None:
        """Associate the USD render materials of a new element as IFC materials.

        Each USD material becomes an ``IfcMaterial`` of the same name whose
        appearance is its surface style; an element with several gets an
        ``IfcMaterialConstituentSet`` (``IfcMaterialList`` in IFC2X3). A
        material the model already has by that name is reused without
        changing its appearance.
        """
        looks: dict[str, Appearance] = {}
        for part in parts:
            look = part.appearance
            if look is not None and not look.key.startswith("color:"):
                looks.setdefault(look.key.split("|")[0], look)
        materials = [self._material(path, looks[path]) for path in sorted(looks)]
        materials = list({m.id(): m for m in materials}.values())
        if not materials:
            return
        if len(materials) == 1:
            ifcopenshell.api.material.assign_material(self.model, products=[product], material=materials[0])
        elif self.factory.is_ifc2x3:
            listing = ifcopenshell.api.material.add_material_set(self.model, set_type="IfcMaterialList")
            listing.Materials = materials
            ifcopenshell.api.material.assign_material(
                self.model, products=[product], type="IfcMaterialList", material=listing
            )
        else:
            name = str(util_attr(product, "Name") or "Materials")
            constituents = ifcopenshell.api.material.add_material_set(
                self.model, name=name, set_type="IfcMaterialConstituentSet"
            )
            for material in materials:
                ifcopenshell.api.material.add_constituent(
                    self.model, constituent_set=constituents, material=material, name=material.Name
                )
            ifcopenshell.api.material.assign_material(
                self.model, products=[product], type="IfcMaterialConstituentSet", material=constituents
            )

    def _material(self, path: str, look: Appearance) -> Any:
        material = self._materials.get(path)
        if material is not None:
            return material
        if self._existing_materials is None:
            self._existing_materials = {}
            for existing in self.model.by_type("IfcMaterial"):
                self._existing_materials.setdefault(str(existing.Name), existing)
        material = self._existing_materials.get(look.name)
        if material is None:
            material = ifcopenshell.api.material.add_material(self.model, name=look.name)
            style = self._style(look)
            if style is not None:
                ifcopenshell.api.style.assign_material_style(
                    self.model,
                    material=material,
                    style=style,
                    context=self.context,
                    should_use_presentation_style_assignment=self.factory.is_ifc2x3,
                )
            self._existing_materials[look.name] = material
            self.materials += 1
        self._materials[path] = material
        return material

    @property
    def styles(self) -> int:
        """Number of distinct surface styles the written geometry uses."""
        return len({style.id() for style in self._styles.values() if style is not None})

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
