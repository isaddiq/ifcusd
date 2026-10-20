"""UsdGeom authoring for tessellated IFC representations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ifcusd.ifc.geometry import ShapeData, chain_edges
from ifcusd.usd.sdf import Gf, LayerWriter, Vt

CURVE_WIDTH_METRES = 0.01


@dataclass
class MeshStats:
    points: int = 0
    triangles: int = 0
    subsets: int = 0


def write_mesh(
    writer: LayerWriter,
    path: str,
    shape: ShapeData,
    *,
    scale: float,
    material_paths: Sequence[str],
    display_colors: Sequence[tuple[tuple[float, float, float], float]],
    double_sided: bool,
    hidden: bool = False,
    specifier: str = "def",
) -> MeshStats:
    """Write ``shape`` as a ``UsdGeom.Mesh`` at ``path``.

    ``material_paths``/``display_colors`` are indexed like ``shape.materials``.
    """
    points = (shape.points * scale).astype(np.float32)
    faces = shape.face_indices.astype(np.int32)
    triangle_count = faces.size // 3
    if faces.size % 3 or (faces.size and int(faces.max()) >= len(points)) or (faces.size and int(faces.min()) < 0):
        raise ValueError("invalid triangle indices from tessellation")

    mesh = writer.prim(path, "Mesh", specifier=specifier, api_schemas=["MaterialBindingAPI"] if material_paths else ())
    writer.attribute(mesh, "subdivisionScheme", "token", "none")
    writer.attribute(mesh, "doubleSided", "bool", bool(double_sided))
    writer.attribute(mesh, "points", "point3f[]", Vt.Vec3fArray.FromNumpy(points), uniform=False)
    writer.attribute(
        mesh,
        "faceVertexCounts",
        "int[]",
        Vt.IntArray.FromNumpy(np.full(triangle_count, 3, dtype=np.int32)),
        uniform=False,
    )
    writer.attribute(mesh, "faceVertexIndices", "int[]", Vt.IntArray.FromNumpy(faces), uniform=False)
    writer.attribute(mesh, "extent", "float3[]", _extent(points), uniform=False)
    if shape.normals is not None and len(shape.normals) == len(points):
        normals = shape.normals.astype(np.float32)
        writer.attribute(
            mesh, "normals", "normal3f[]", Vt.Vec3fArray.FromNumpy(normals), uniform=False, interpolation="vertex"
        )
    if shape.uvs is not None and len(shape.uvs) == len(points):
        writer.attribute(
            mesh,
            "primvars:st",
            "texCoord2f[]",
            Vt.Vec2fArray.FromNumpy(shape.uvs.astype(np.float32)),
            uniform=False,
            interpolation="vertex",
        )
    if hidden:
        writer.attribute(mesh, "visibility", "token", "invisible", uniform=False)

    stats = MeshStats(points=len(points), triangles=triangle_count)
    if not material_paths:
        return stats

    ids = shape.material_ids
    if ids.size != triangle_count:
        ids = np.zeros(triangle_count, dtype=np.int32)
    ids = np.clip(ids, 0, len(material_paths) - 1)
    faces_by_path: dict[str, list[np.ndarray]] = {}
    for material_index in np.unique(ids).tolist():
        faces_by_path.setdefault(material_paths[material_index], []).append(
            np.nonzero(ids == material_index)[0].astype(np.int32)
        )
    dominant = int(np.bincount(ids).argmax()) if ids.size else 0
    color, opacity = display_colors[dominant]
    writer.attribute(
        mesh,
        "primvars:displayColor",
        "color3f[]",
        Vt.Vec3fArray([Gf.Vec3f(*color)]),
        uniform=False,
        interpolation="constant",
    )
    if opacity < 1.0:
        writer.attribute(
            mesh,
            "primvars:displayOpacity",
            "float[]",
            Vt.FloatArray([float(opacity)]),
            uniform=False,
            interpolation="constant",
        )

    if len(faces_by_path) == 1:
        writer.relationship(mesh, "material:binding", [next(iter(faces_by_path))])
        return stats

    writer.attribute(mesh, "subsetFamily:materialBind:familyType", "token", "partition")
    for material_path in sorted(faces_by_path):
        indices = np.sort(np.concatenate(faces_by_path[material_path]))
        subset_name = material_path.rsplit("/", 1)[-1]
        subset = writer.prim(f"{path}/{subset_name}", "GeomSubset", api_schemas=["MaterialBindingAPI"])
        writer.attribute(subset, "elementType", "token", "face")
        writer.attribute(subset, "familyName", "token", "materialBind")
        writer.attribute(subset, "indices", "int[]", Vt.IntArray.FromNumpy(indices.astype(np.int32)), uniform=False)
        writer.relationship(subset, "material:binding", [material_path])
        stats.subsets += 1
    return stats


def write_curves(
    writer: LayerWriter,
    path: str,
    shape: ShapeData,
    *,
    scale: float,
    guide: bool,
    color: tuple[float, float, float] | None,
    specifier: str = "def",
) -> int:
    """Write curve-only geometry as linear ``UsdGeom.BasisCurves``; returns curve count."""
    chains = chain_edges(shape.edge_indices)
    if not chains:
        return 0
    order = np.concatenate([np.asarray(chain, dtype=np.int64) for chain in chains])
    if int(order.max()) >= len(shape.points):
        raise ValueError("invalid edge indices from tessellation")
    points = (shape.points[order] * scale).astype(np.float32)
    curves = writer.prim(path, "BasisCurves", specifier=specifier)
    writer.attribute(curves, "type", "token", "linear")
    writer.attribute(curves, "curveVertexCounts", "int[]", Vt.IntArray([len(c) for c in chains]), uniform=False)
    writer.attribute(curves, "points", "point3f[]", Vt.Vec3fArray.FromNumpy(points), uniform=False)
    writer.attribute(curves, "extent", "float3[]", _extent(points, pad=CURVE_WIDTH_METRES * scale), uniform=False)
    writer.attribute(
        curves,
        "widths",
        "float[]",
        Vt.FloatArray([CURVE_WIDTH_METRES * scale]),
        uniform=False,
        interpolation="constant",
    )
    if color is not None:
        writer.attribute(
            curves,
            "primvars:displayColor",
            "color3f[]",
            Vt.Vec3fArray([Gf.Vec3f(*color)]),
            uniform=False,
            interpolation="constant",
        )
    if guide:
        writer.attribute(curves, "purpose", "token", "guide")
    return len(chains)


def _extent(points: np.ndarray, pad: float = 0.0) -> Any:
    if points.size == 0:
        return Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 0, 0)])
    low = points.min(axis=0) - pad
    high = points.max(axis=0) + pad
    return Vt.Vec3fArray([Gf.Vec3f(*low.tolist()), Gf.Vec3f(*high.tolist())])
