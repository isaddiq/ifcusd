"""Reading a composed USD stage for export to IFC.

The stage is opened with full composition, so robot and simulation data can
live in separate override layers on top of the converted file (the usual
Omniverse workflow) and still be exported.

Every prim falls into one of these groups:

* **IFC prims** carry ``ifc:class``. They match an entity of the source IFC by
  ``ifc:globalId`` (or ``ifc:stepId`` for resources) or become a new entity.
* **Representations** of an IFC product: its gprim children named after an IFC
  representation identifier (``Body``, ``Axis``, ``FootPrint``...).
* **New objects**: prims without ``ifc:class`` whose subtree holds renderable
  geometry, an articulated robot or exportable data, and sensor prims. Each
  becomes one IFC element whose geometry is its whole subtree.
* **Containers**: prims without ``ifc:class`` that hold IFC prims (for example
  ``Unassigned``) are transparent.
* Everything else (materials, lights, plain cameras, joints, render settings,
  physics scenes, excluded paths) is ignored.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ifcusd.dependencies import import_usd
from ifcusd.errors import InputError
from ifcusd.options import IfcExportOptions
from ifcusd.reverse.values import python_value, usd_element_type

_usd = import_usd()
Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics = _usd.Gf, _usd.Sdf, _usd.Usd, _usd.UsdGeom, _usd.UsdLux, _usd.UsdPhysics

# Child prim names IfcUSD gives representation geometry (IFC RepresentationIdentifier values).
REPRESENTATION_CHILD = re.compile(
    r"^(Body|Body_FallBack|Axis|Box|FootPrint|Annotation|Reference|Profile|Surface|Clearance|Lighting|CoG|Geometry)"
    r"(_\d+)?$"
)
MESH_TYPES = frozenset({"Mesh", "Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane", "Cylinder_1", "Capsule_1"})
IGNORED_TYPES = frozenset(
    {
        "Material",
        "Shader",
        "NodeGraph",
        "GeomSubset",
        "PhysicsScene",
        "RenderSettings",
        "RenderProduct",
        "RenderVar",
        "RenderPass",
        "OmniGraph",
        "OmniGraphNode",
    }
)
SENSOR_TYPES = re.compile(r"lidar|radar|sensor|imu|sonar|ultrasonic", re.IGNORECASE)
_STAGE_METADATA = (
    "upAxis",
    "metersPerUnit",
    "kilogramsPerUnit",
    "timeCodesPerSecond",
    "framesPerSecond",
    "startTimeCode",
    "endTimeCode",
)


@dataclass(eq=False)
class PrimRecord:
    """A prim that becomes (or updates) one IFC entity."""

    prim: Any
    path: str
    ifc_class: str
    new_object: bool = False
    object_type: str | None = None
    parent: PrimRecord | None = None
    children: list[PrimRecord] = field(default_factory=list)
    geometry: list[Any] = field(default_factory=list)
    global_id: str | None = None
    step_id: int | None = None
    parent_relationship: str | None = None
    entity: Any = None
    matched: bool = False

    @property
    def name(self) -> str:
        display = self.prim.GetMetadata("displayName")
        return str(display) if display else self.prim.GetName()


def open_stage(path: Path, layers: Sequence[Path] = ()) -> tuple[Any, dict[str, Any]]:
    """Open ``path`` with ``layers`` composed on top (later layers are stronger).

    Returns the stage and the IfcUSD provenance of ``path`` (empty for USD
    files not written by IfcUSD).
    """
    base = Sdf.Layer.FindOrOpen(str(path))
    if base is None:
        raise InputError(f"Could not open USD file {path}")
    provenance = dict(base.customLayerData.get("ifcusd", {}) or {})
    if not layers:
        return Usd.Stage.Open(base), provenance
    extra = []
    for layer_path in layers:
        layer = Sdf.Layer.FindOrOpen(str(layer_path))
        if layer is None:
            raise InputError(f"Could not open USD layer {layer_path}")
        extra.append(layer)
    stack = [*reversed(extra), base]  # strongest first
    root = Sdf.Layer.CreateAnonymous("ifcusd_export.usda")
    root.subLayerPaths = [layer.identifier for layer in stack]
    for key in _STAGE_METADATA:
        source = next((layer for layer in stack if layer.pseudoRoot.HasInfo(key)), None)
        if source is not None:
            root.pseudoRoot.SetInfo(key, source.pseudoRoot.GetInfo(key))
    if base.defaultPrim:
        root.defaultPrim = base.defaultPrim
    return Usd.Stage.Open(root), provenance


def ifc_class_of(prim: Any) -> str | None:
    attribute = prim.GetAttribute("ifc:class")
    if not attribute or not attribute.HasAuthoredValue():
        return None
    value = attribute.Get()
    return str(value) if value else None


def authored(prim: Any, name: str, time: Any = None) -> Any:
    """Python value of an authored attribute, or ``None``."""
    attribute = prim.GetAttribute(name)
    if not attribute or not attribute.HasAuthoredValue():
        return None
    return attribute_value(attribute, time if time is not None else Usd.TimeCode.EarliestTime())


def attribute_value(attribute: Any, time: Any) -> Any:
    """Python value of an attribute at ``time``; float/half values are rounded to their precision."""
    value = python_value(attribute.Get(time))
    base, _count, _role = usd_element_type(str(attribute.GetTypeName()))
    if base in ("float", "half"):
        value = _round_single(value)
    return value


def layer_name(prop: Any) -> str:
    """File name of the strongest layer that authors ``prop``."""
    stack = prop.GetPropertyStack()
    if not stack:
        return ""
    layer = stack[0].layer
    return Path(layer.realPath or layer.identifier).name


def time_samples(attribute: Any) -> list[tuple[float, Any]]:
    """``[(time code, value)]`` when the attribute has two or more time samples."""
    times = attribute.GetTimeSamples()
    if len(times) < 2:
        return []
    return [(float(t), attribute_value(attribute, Usd.TimeCode(t))) for t in times]


class UsdScene:
    """A composed stage plus everything needed to read it in IFC terms."""

    def __init__(self, stage: Any, options: IfcExportOptions, provenance: dict[str, Any]) -> None:
        self.stage = stage
        self.options = options
        self.provenance = provenance
        self.meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
        self.kilograms_per_unit = float(UsdPhysics.GetStageKilogramsPerUnit(stage) or 1.0)
        self.time_codes_per_second = float(stage.GetTimeCodesPerSecond() or 24.0)
        self.warnings: list[str] = []
        self.time = self._export_time()
        self.root = self._ifc_root()
        self._xforms = UsdGeom.XformCache(self.time)
        self._frame = self._ifc_frame()
        self._ifc_ancestors: set[str] = set()
        self.records: list[PrimRecord] = []
        self.by_path: dict[str, PrimRecord] = {}

    # ------------------------------------------------------------------ stage facts

    def _export_time(self) -> Any:
        value = self.options.time
        if value == "earliest":
            return Usd.TimeCode.EarliestTime()
        if value == "default":
            return Usd.TimeCode.Default()
        if value == "latest":
            if self.stage.HasAuthoredTimeCodeRange():
                return Usd.TimeCode(self.stage.GetEndTimeCode())
            self.warnings.append("time 'latest' requested but the stage has no end time code; using the earliest")
            return Usd.TimeCode.EarliestTime()
        return Usd.TimeCode(float(value))

    def _ifc_root(self) -> Any | None:
        prim = self.stage.GetDefaultPrim()
        if prim and (prim.HasAttribute("ifc:file:schema") or self.provenance):
            return prim
        return None

    def _ifc_frame(self) -> Any:
        """Matrix taking world coordinates to the IFC project frame (row-vector convention)."""
        if self.root is not None:
            return self._xforms.GetLocalToWorldTransform(self.root).GetInverse()
        frame = Gf.Matrix4d(1.0)
        if UsdGeom.GetStageUpAxis(self.stage) == UsdGeom.Tokens.y:
            frame.SetRotate(Gf.Rotation(Gf.Vec3d(1.0, 0.0, 0.0), 90.0))  # Y-up to IFC's Z-up
        return frame

    @property
    def synthetic_looks(self) -> str | None:
        """Path prefix of the render materials IfcUSD wrote (``/World/Looks/``), for IfcUSD stages."""
        return f"{self.root.GetPath().pathString}/Looks/" if self.root is not None else None

    @property
    def file_info(self) -> dict[str, Any]:
        """``ifc:file:*``, ``ifc:units:*`` and ``ifc:geo:*`` values of the IfcUSD root prim."""
        if self.root is None:
            return {}
        info = {}
        for attribute in self.root.GetAttributes():
            name = attribute.GetName()
            if name.startswith(("ifc:file:", "ifc:units:", "ifc:geo:")) and attribute.HasAuthoredValue():
                info[name] = attribute_value(attribute, Usd.TimeCode.Default())
        return info

    # ------------------------------------------------------------------ transforms

    def ifc_matrix(self, prim: Any) -> np.ndarray:
        """Prim local frame (metres) to IFC project frame (metres), column-vector convention."""
        return _metres(self._xforms.GetLocalToWorldTransform(prim) * self._frame, self.meters_per_unit)

    def ifc_matrix_at(self, prim: Any, time: float) -> np.ndarray:
        cache = UsdGeom.XformCache(Usd.TimeCode(time))
        return _metres(cache.GetLocalToWorldTransform(prim) * self._frame, self.meters_per_unit)

    def relative_matrix(self, prim: Any, ancestor: Any) -> np.ndarray:
        """``prim`` local frame (metres) to ``ancestor`` local frame (metres)."""
        matrix, _resets = self._xforms.ComputeRelativeTransform(prim, ancestor)
        return _metres(matrix, self.meters_per_unit)

    # ------------------------------------------------------------------ classification

    def scan(self) -> list[PrimRecord]:
        """Find every prim that becomes an IFC entity, in namespace (pre-)order."""
        for prim in self.stage.Traverse():
            if ifc_class_of(prim) and not self._ignored(prim):
                path = prim.GetPath().GetParentPath()
                while not path.IsAbsoluteRootPath():
                    self._ifc_ancestors.add(path.pathString)
                    path = path.GetParentPath()
        for child in self.stage.GetPseudoRoot().GetFilteredChildren(Usd.PrimDefaultPredicate):
            self._visit(child, None)
        return self.records

    def _visit(self, prim: Any, parent: PrimRecord | None) -> None:
        if self._ignored(prim):
            return
        ifc_class = ifc_class_of(prim)
        if ifc_class:
            record = self._add(prim, ifc_class, parent)
            for child in prim.GetFilteredChildren(Usd.PrimDefaultPredicate):
                if self._is_representation(child):
                    record.geometry.append(child)
                else:
                    self._visit(child, record)
            return
        if prim.GetPath().pathString not in self._ifc_ancestors and self.options.include_new_objects:
            detected = self._detect_new_object(prim)
            if detected is not None:
                record = self._add(prim, detected[0], parent, new_object=True, object_type=detected[1])
                record.geometry.append(prim)
                return
        for child in prim.GetFilteredChildren(Usd.PrimDefaultPredicate):
            self._visit(child, parent)

    def _add(
        self,
        prim: Any,
        ifc_class: str,
        parent: PrimRecord | None,
        *,
        new_object: bool = False,
        object_type: str | None = None,
    ) -> PrimRecord:
        record = PrimRecord(
            prim=prim,
            path=prim.GetPath().pathString,
            ifc_class=ifc_class,
            new_object=new_object,
            object_type=object_type,
            parent=parent,
        )
        if not new_object:
            global_id = authored(prim, "ifc:globalId")
            step_id = authored(prim, "ifc:stepId")
            record.global_id = str(global_id) if global_id else None
            record.step_id = int(step_id) if isinstance(step_id, int) else None
            relationship = authored(prim, "ifc:parentRelationship")
            record.parent_relationship = str(relationship) if relationship else None
        if parent is not None:
            parent.children.append(record)
        self.records.append(record)
        self.by_path[record.path] = record
        return record

    def _ignored(self, prim: Any) -> bool:
        path = prim.GetPath().pathString
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in self.options.exclude_paths):
            return True
        type_name = prim.GetTypeName()
        if type_name in IGNORED_TYPES or type_name.endswith("Light") or prim.HasAPI(UsdLux.LightAPI):
            return True
        if prim.IsA(UsdPhysics.Joint):
            return True
        if prim.IsA(UsdGeom.Camera) and not self.is_sensor(prim) and not ifc_class_of(prim):
            return True
        return False

    def _is_representation(self, prim: Any) -> bool:
        if ifc_class_of(prim) or not REPRESENTATION_CHILD.match(prim.GetName()):
            return False
        return prim.IsA(UsdGeom.Gprim) or prim.IsInstance()

    def is_sensor(self, prim: Any) -> bool:
        if SENSOR_TYPES.search(prim.GetTypeName()):
            return True
        return prim.IsA(UsdGeom.Camera) and any(n.startswith("omni:sensor") for n in prim.GetPropertyNames())

    def is_mesh(self, prim: Any) -> bool:
        return prim.GetTypeName() in MESH_TYPES or prim.IsA(UsdGeom.Mesh)

    def renderable(self, prim: Any) -> bool:
        imageable = UsdGeom.Imageable(prim)
        if imageable.ComputeVisibility(self.time) == UsdGeom.Tokens.invisible:
            return False
        return imageable.ComputePurpose() in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)

    def has_export_data(self, prim: Any) -> bool:
        namespaces = ("ifc:pset:", "ifc:qto:", *(f"{ns}:" for ns in self.options.extension_namespaces))
        if any(name.startswith(namespaces) for name in prim.GetPropertyNames()):
            return True
        return prim.HasAPI(UsdPhysics.MassAPI) or prim.HasAPI(UsdPhysics.RigidBodyAPI)

    def _detect_new_object(self, prim: Any) -> tuple[str, str | None] | None:
        """``(ifc class, object type)`` when ``prim`` is a new object, ``None`` to look at its children.

        The USD model hierarchy decides granularity: scene roots and ``assembly``
        or ``group`` models are containers, ``component`` models, robots
        (articulation roots), sensors, prims carrying exportable data and bare
        gprims are objects, and an untyped ``Xform`` is an object when it
        directly holds mesh geometry.
        """
        if self.is_sensor(prim):
            return "IfcSensor", prim.GetTypeName() or "Sensor"
        children = prim.GetFilteredChildren(Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate))
        # The articulation root is either the robot prim itself or its root link (a rigid
        # body); a child that is a whole robot does not make its parent one.
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI) or any(
            child.HasAPI(UsdPhysics.ArticulationRootAPI) and child.HasAPI(UsdPhysics.RigidBodyAPI) for child in children
        ):
            return self.options.new_object_class, "Robot"
        if self.is_mesh(prim):
            return (self.options.new_object_class, None) if self.renderable(prim) else None
        if prim.GetParent().IsPseudoRoot():
            return None
        kind = str(Usd.ModelAPI(prim).GetKind() or "")
        if kind in ("assembly", "group"):
            return None
        if self.has_export_data(prim):
            return self.options.new_object_class, None
        if kind in ("component", "subcomponent") or any(self.is_mesh(child) for child in children):
            if self.mesh_prims(prim):
                return self.options.new_object_class, None
        return None

    def mesh_prims(self, root: Any) -> list[Any]:
        """Renderable mesh-like prims in the subtree of a new object (instance proxies included)."""
        found = []
        iterator = iter(Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)))
        for prim in iterator:
            if prim != root and (self._ignored(prim) or ifc_class_of(prim)):
                iterator.PruneChildren()
                continue
            if self.is_mesh(prim) and self.renderable(prim):
                found.append(prim)
        return found

    def subtree(self, root: Any) -> list[Any]:
        """Every non-ignored prim of a new object's subtree (instance proxies included)."""
        found = []
        iterator = iter(Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)))
        for prim in iterator:
            if prim != root and (self._ignored(prim) or ifc_class_of(prim)):
                iterator.PruneChildren()
                continue
            found.append(prim)
        return found


def rigid(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a transform into a rigid placement and the residual (scale, shear, mirroring).

    Returns ``(placement, residual)`` with ``matrix == placement @ residual``.
    IFC placements are rigid, so the residual is baked into geometry.
    """
    u, _s, vt = np.linalg.svd(matrix[:3, :3])
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    placement = np.eye(4)
    placement[:3, :3] = rotation
    placement[:3, 3] = matrix[:3, 3]
    return placement, np.linalg.inv(placement) @ matrix


def quaternion(rotation: np.ndarray) -> tuple[float, float, float, float]:
    """Unit quaternion ``(w, x, y, z)`` of a rotation matrix (column-vector convention)."""
    m = rotation
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * np.sqrt(trace + 1.0)
        q = (0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = ((m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s)
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = ((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s)
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = ((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s)
    if q[0] < 0:  # canonical hemisphere
        q = tuple(-c for c in q)
    return tuple(float(c) for c in q)  # type: ignore[return-value]


def _metres(matrix: Any, meters_per_unit: float) -> np.ndarray:
    """Row-vector ``Gf.Matrix4d`` in stage units to a column-vector matrix in metres."""
    result = np.array(matrix, dtype=np.float64).T
    result[:3, 3] *= meters_per_unit
    return result


def _round_single(value: Any) -> Any:
    """Shortest decimal for a single-precision value (0.800000011920929 -> 0.8)."""
    if isinstance(value, float):
        return float(f"{value:.7g}")
    if isinstance(value, tuple):
        return tuple(_round_single(v) for v in value)
    if isinstance(value, list):
        return [_round_single(v) for v in value]
    return value
