"""UsdPhysics schemas from the ``IfcUSD_Physics`` property set.

``ifcusd to-ifc`` stores the physics of a simulation in IFC as the
``IfcUSD_Physics`` property set; this module turns it back into native
``UsdPhysics`` schemas, so IFC -> USD -> IFC -> USD keeps the simulation set-up
that NVIDIA Isaac Sim and other PhysX tools use:

==========================================  ==================================================
IfcUSD_Physics                              UsdPhysics
==========================================  ==================================================
``Mass``, ``Density``, ``CenterOfMass``     ``PhysicsMassAPI`` on the element prim
``RigidBodyEnabled``, ``KinematicEnabled``  ``PhysicsRigidBodyAPI`` on the element prim
``CollisionEnabled``                        ``PhysicsCollisionAPI`` on each of its meshes
``CollisionApproximation``                  ``PhysicsMeshCollisionAPI`` on each of its meshes
``StaticFriction``, ``DynamicFriction``,    a ``Material`` with ``PhysicsMaterialAPI`` under
``Restitution``                             ``<root>/PhysicsMaterials``, bound to its meshes
                                            for the ``physics`` purpose
==========================================  ==================================================

``Articulated`` and ``JointCount`` describe a robot whose links were merged
into one IFC element; no articulation can be rebuilt from one mesh, so they
stay IFC data. Values are converted from project units to SI, then to stage
units (``metersPerUnit``; ``kilogramsPerUnit`` is 1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import astuple, dataclass
from typing import Any

from ifcusd.extension import COLLISION_APPROXIMATIONS, PHYSICS_SCHEMAS_KEY, PHYSICS_SET
from ifcusd.ifc.properties import PropertySet
from ifcusd.ifc.units import UnitResolver
from ifcusd.naming import safe_identifier
from ifcusd.usd.sdf import Gf, LayerWriter, Vt, set_display_name

PHYSICS_MATERIALS_SCOPE = "PhysicsMaterials"


@dataclass(frozen=True)
class PhysicsSpec:
    """Physics of one element, in stage units (kilograms, stage length units)."""

    mass: float | None = None
    density: float | None = None
    center_of_mass: tuple[float, float, float] | None = None
    rigid_body_enabled: bool | None = None
    kinematic_enabled: bool | None = None
    collision_enabled: bool | None = None
    approximation: str | None = None
    static_friction: float | None = None
    dynamic_friction: float | None = None
    restitution: float | None = None

    @property
    def has_mass(self) -> bool:
        return self.mass is not None or self.density is not None or self.center_of_mass is not None

    @property
    def material(self) -> tuple[float | None, float | None, float | None] | None:
        """``(static friction, dynamic friction, restitution)``, or ``None`` without a physics material."""
        values = (self.static_friction, self.dynamic_friction, self.restitution)
        return None if all(v is None for v in values) else values


def physics_spec(
    sets: Sequence[PropertySet],
    units: UnitResolver,
    *,
    meters_per_unit: float,
    center_of_mass: bool = True,
) -> PhysicsSpec | None:
    """The ``IfcUSD_Physics`` values among ``sets``, converted to stage units.

    ``center_of_mass`` is ``False`` for stages written in world coordinates,
    where prims no longer carry the element's placement frame.
    """
    values: dict[str, Any] = {}
    for pset in sets:
        if pset.name != PHYSICS_SET or pset.is_quantity:
            continue
        for value in pset.values:
            if len(value.path) == 1 and value.value is not None:
                values[value.display_name] = value.value.value
    if not values:
        return None

    def number(name: str, measure: str | None = None) -> float | None:
        raw = values.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return None
        return float(raw) * units.si_scale(measure)

    def flag(name: str) -> bool | None:
        raw = values.get(name)
        return raw if isinstance(raw, bool) else None

    density = number("Density", "IfcMassDensityMeasure")
    center = None
    raw_center = values.get("CenterOfMass")
    if center_of_mass and isinstance(raw_center, (list, tuple)) and len(raw_center) == 3:
        if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in raw_center):
            scale = units.si_scale("IfcLengthMeasure") / meters_per_unit
            center = tuple(float(c) * scale for c in raw_center)
    approximation = values.get("CollisionApproximation")
    spec = PhysicsSpec(
        mass=number("Mass", "IfcMassMeasure"),
        density=density * meters_per_unit**3 if density is not None else None,  # kg per (stage unit)^3
        center_of_mass=center,  # type: ignore[arg-type]
        rigid_body_enabled=flag("RigidBodyEnabled"),
        kinematic_enabled=flag("KinematicEnabled"),
        collision_enabled=flag("CollisionEnabled"),
        approximation=approximation if approximation in COLLISION_APPROXIMATIONS else None,
        static_friction=number("StaticFriction"),
        dynamic_friction=number("DynamicFriction"),
        restitution=number("Restitution"),
    )
    return spec if any(v is not None for v in astuple(spec)) else None


def write_physics(
    writer: LayerWriter, prim: Any, spec: PhysicsSpec, *, colliders: Sequence[Any], material_path: str | None
) -> list[str]:
    """Apply the physics of one element to its prim and its mesh prims (``colliders``).

    Returns the applied schemas, which are also listed in the prim's
    ``customData.ifc.physicsSchemas``: ``ifcusd to-ifc`` reads a listed schema
    that a simulation removed (Isaac Sim's Remove > Physics) as disabled.
    """
    applied = []
    if spec.has_mass:
        applied.append("PhysicsMassAPI")
        writer.add_api_schemas(prim, ["PhysicsMassAPI"])
        if spec.mass is not None:
            writer.attribute(prim, "physics:mass", "float", spec.mass, uniform=False)
        if spec.density is not None:
            writer.attribute(prim, "physics:density", "float", spec.density, uniform=False)
        if spec.center_of_mass is not None:
            writer.attribute(prim, "physics:centerOfMass", "point3f", Gf.Vec3f(*spec.center_of_mass), uniform=False)
    if spec.rigid_body_enabled is not None:
        applied.append("PhysicsRigidBodyAPI")
        writer.add_api_schemas(prim, ["PhysicsRigidBodyAPI"])
        writer.attribute(prim, "physics:rigidBodyEnabled", "bool", spec.rigid_body_enabled, uniform=False)
        if spec.kinematic_enabled is not None:
            writer.attribute(prim, "physics:kinematicEnabled", "bool", spec.kinematic_enabled, uniform=False)
    if spec.collision_enabled is not None and colliders:
        applied.append("PhysicsCollisionAPI")
        for collider in colliders:
            writer.add_api_schemas(collider, ["PhysicsCollisionAPI"])
            writer.attribute(collider, "physics:collisionEnabled", "bool", spec.collision_enabled, uniform=False)
            if spec.approximation is not None:
                writer.add_api_schemas(collider, ["PhysicsMeshCollisionAPI"])
                writer.attribute(collider, "physics:approximation", "token", spec.approximation)
    # The material is bound on the meshes, where PhysX reads it: bound on the element
    # prim it would also apply to child elements such as openings.
    if material_path is not None:
        for collider in colliders:
            writer.add_api_schemas(collider, ["MaterialBindingAPI"])
            writer.relationship(collider, "material:binding:physics", [material_path])
    if applied:
        data = dict(prim.customData) if prim.customData else {}
        ifc_data = dict(data.get("ifc", {}))
        ifc_data[PHYSICS_SCHEMAS_KEY] = Vt.StringArray(applied)
        data["ifc"] = ifc_data
        prim.customData = data
    return applied


def physics_material_name(material: tuple[float | None, float | None, float | None]) -> str:
    """A readable, deterministic prim name such as ``Physics_sf0_6_df0_45_r0_1``."""
    parts = [
        f"{label}{'x' if value is None else format(value, 'g')}"
        for label, value in zip(("sf", "df", "r"), material, strict=True)
    ]
    return safe_identifier("Physics_" + "_".join(parts), "PhysicsMaterial")


def write_physics_material(
    writer: LayerWriter, path: str, material: tuple[float | None, float | None, float | None]
) -> None:
    """A ``Material`` carrying only ``PhysicsMaterialAPI`` (no render network)."""
    spec = writer.prim(path, "Material", api_schemas=["PhysicsMaterialAPI"])
    names = ("physics:staticFriction", "physics:dynamicFriction", "physics:restitution")
    labels = ("static friction", "dynamic friction", "restitution")
    for name, value in zip(names, material, strict=True):
        if value is not None:
            writer.attribute(spec, name, "float", float(value), uniform=False)
    described = ", ".join(
        f"{label} {value:g}" for label, value in zip(labels, material, strict=True) if value is not None
    )
    set_display_name(spec, f"Physics material ({described})")
