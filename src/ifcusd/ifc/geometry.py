"""Tessellation through the IfcOpenShell geometry iterator.

Shapes are produced in each product's local coordinate system together with
the product's world matrix. Products that share a representation (mapped
items, type geometry) come back with the same geometry id, which the USD
writer uses for instancing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import numpy as np

from ifcusd.options import ConversionOptions

log = logging.getLogger(__name__)


@dataclass
class ShapeData:
    """One tessellated representation of one product."""

    product_id: int
    context: str
    geometry_id: str
    matrix: tuple[float, ...]  # 16 values, row-vector convention (USD/Gf layout), metres
    points: np.ndarray  # (N, 3) float64, metres, local coordinates
    face_indices: np.ndarray  # (M * 3,) int32, triangles
    normals: np.ndarray | None  # (N, 3)
    uvs: np.ndarray | None  # (N, 2)
    material_ids: np.ndarray  # (M,) int32 indices into ``materials``
    materials: list[Any]  # IfcOpenShell style objects
    edge_indices: np.ndarray  # (E * 2,) int32, for curve-only shapes

    @property
    def is_mesh(self) -> bool:
        return self.face_indices.size > 0

    @property
    def is_curve(self) -> bool:
        return not self.is_mesh and self.edge_indices.size > 0

    @property
    def triangle_count(self) -> int:
        return int(self.face_indices.size // 3)


def make_settings(options: ConversionOptions, *, generate_uvs: bool) -> Any:
    settings = ifcopenshell.geom.settings()
    _set(settings, "use-world-coords", bool(options.use_world_coords))
    _set(settings, "weld-vertices", False)  # keeps per-corner normals for crisp BIM edges
    _set(settings, "apply-default-materials", True)
    _set(settings, "generate-uvs", bool(generate_uvs))
    if options.include_curves:
        _set(settings, "dimensionality", 2)  # curves, surfaces and solids
    if options.linear_deflection is not None:
        _set(settings, "mesher-linear-deflection", float(options.linear_deflection))
    if options.angular_deflection is not None:
        _set(settings, "mesher-angular-deflection", float(options.angular_deflection))
    return settings


def _set(settings: Any, name: str, value: Any) -> None:
    try:
        settings.set(name, value)
        return
    except Exception:
        pass
    legacy = name.upper().replace("-", "_")
    try:
        settings.set(getattr(settings, legacy), value)
    except Exception:
        log.debug("IfcOpenShell setting %s is not available", name)


def geometry_filters(options: ConversionOptions, model: Any) -> tuple[list[str] | None, list[str] | None]:
    """IfcOpenShell include/exclude class lists, restricted to classes in the model's schema."""
    exclude = list(options.exclude_types)
    if not options.include_openings:
        exclude.append("IfcOpeningElement")
    if options.spaces == "skip":
        exclude.append("IfcSpace")
    include = list(options.include_types) or None
    if include:
        # Spatial elements carry geometry too (sites, spaces); keep them with an include filter.
        include = include + ["IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcFacility", "IfcFacilityPart"]
        if options.spaces != "skip":
            include += ["IfcSpace", "IfcSpatialZone"]
    known = _known_classes(model)
    if known:
        include = sorted({name for name in include if name.lower() in known}) if include else None
        exclude = sorted({name for name in exclude if name.lower() in known})
    return include, (exclude or None)


def _known_classes(model: Any) -> set[str]:
    try:
        schema_name = str(getattr(model, "schema_identifier", None) or model.schema)
        schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema_name)
        return {declaration.name().lower() for declaration in schema.declarations()}
    except Exception:
        return set()


_silence_depth = 0
_DEVNULL_FD: int | None = None


@contextlib.contextmanager
def native_stdout_silenced() -> Iterator[None]:
    """Silence diagnostics IfcOpenShell's C++ core prints straight to stdout.

    Re-entrant: nested uses inside an active silenced block are free.
    """
    global _silence_depth, _DEVNULL_FD
    if _silence_depth:
        _silence_depth += 1
        try:
            yield
        finally:
            _silence_depth -= 1
        return
    try:
        sys.stdout.flush()
        saved = os.dup(1)
        if _DEVNULL_FD is None:
            _DEVNULL_FD = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_DEVNULL_FD, 1)
    except Exception:  # no real stdout file descriptor (embedded interpreters)
        yield
        return
    _silence_depth = 1
    try:
        yield
    finally:
        _silence_depth = 0
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os.dup2(saved, 1)
        os.close(saved)


def iterate_shapes(
    model: Any,
    options: ConversionOptions,
    *,
    generate_uvs: bool,
    progress: Callable[[float], None] | None = None,
) -> Iterator[ShapeData]:
    """Yield :class:`ShapeData` for every tessellated product representation."""
    settings = make_settings(options, generate_uvs=generate_uvs)
    include, exclude = geometry_filters(options, model)
    kwargs: dict[str, Any] = {}
    if include:
        kwargs["include"] = include
    elif exclude:
        kwargs["exclude"] = exclude
    with native_stdout_silenced():
        iterator = ifcopenshell.geom.iterator(settings, model, max(1, options.threads), **kwargs)
        initialized = iterator.initialize()
    if not initialized:
        return
    last_progress = -1
    while True:
        with native_stdout_silenced():
            shape = iterator.get()
        data = _shape_data(shape)
        if data is not None and include and exclude:
            entity = model.by_id(data.product_id)
            if any(_is_a(entity, name) for name in exclude):
                data = None
        if data is not None:
            yield data
        if progress is not None:
            try:
                value = int(iterator.progress())
            except Exception:
                value = last_progress
            if value != last_progress:
                last_progress = value
                progress(value / 100.0)
        with native_stdout_silenced():
            more = iterator.next()
        if not more:
            break


def _is_a(entity: Any, name: str) -> bool:
    try:
        return bool(entity.is_a(name))
    except Exception:
        return False


def _shape_data(shape: Any) -> ShapeData | None:
    geometry = shape.geometry
    points = _array(geometry, "verts", np.float64).reshape(-1, 3)
    faces = _array(geometry, "faces", np.int32)
    edges = _array(geometry, "edges", np.int32) if faces.size == 0 else np.zeros(0, dtype=np.int32)
    if points.size == 0 or (faces.size == 0 and edges.size == 0):
        return None
    normals = _array(geometry, "normals", np.float64)
    normals = normals.reshape(-1, 3) if normals.size == points.size else None
    uvs = _array(geometry, "uvs", np.float64)
    uvs = uvs.reshape(-1, 2) if uvs.size and uvs.size // 2 == len(points) else None
    material_ids = _array(geometry, "material_ids", np.int32)
    try:
        matrix = tuple(float(v) for v in shape.transformation.matrix)
    except Exception:
        matrix = tuple(float(v) for v in np.eye(4).reshape(-1))
    if len(matrix) == 12:  # older IfcOpenShell: 4x3 column-major
        m = matrix
        matrix = (m[0], m[1], m[2], 0.0, m[3], m[4], m[5], 0.0, m[6], m[7], m[8], 0.0, m[9], m[10], m[11], 1.0)
    return ShapeData(
        product_id=int(shape.id),
        context=str(getattr(shape, "context", "") or "Body"),
        geometry_id=str(geometry.id),
        matrix=matrix,
        points=points,
        face_indices=faces,
        normals=normals,
        uvs=uvs,
        material_ids=material_ids,
        materials=list(getattr(geometry, "materials", ()) or ()),
        edge_indices=edges,
    )


def _array(geometry: Any, name: str, dtype: Any) -> np.ndarray:
    buffer = getattr(geometry, f"{name}_buffer", None)
    if buffer is not None:
        try:
            return np.frombuffer(buffer, dtype=dtype).copy()
        except Exception:
            pass
    values = getattr(geometry, name, None)
    if values is None:
        return np.zeros(0, dtype=dtype)
    return np.asarray(values, dtype=dtype).reshape(-1)


def chain_edges(edges: np.ndarray) -> list[list[int]]:
    """Join consecutive segments (a-b, b-c, ...) into polylines."""
    pairs = edges.reshape(-1, 2)
    chains: list[list[int]] = []
    current: list[int] = []
    for a, b in pairs.tolist():
        if current and current[-1] == a:
            current.append(b)
        else:
            if len(current) >= 2:
                chains.append(current)
            current = [a, b]
    if len(current) >= 2:
        chains.append(current)
    return chains


def ifcopenshell_log() -> str:
    try:
        return str(ifcopenshell.ifcopenshell_wrapper.get_log())
    except Exception:
        return ""
