"""IfcUSD_Physics <-> UsdPhysics: physics survives IFC -> USD -> IFC."""

from __future__ import annotations

from pathlib import Path

import pytest
from model_factory import PHYSICS_MASS

from ifcusd import ConversionOptions, convert, convert_usd_to_ifc, validate_usd

MASS = PHYSICS_MASS  # more digits than a 32-bit float holds


@pytest.fixture(scope="module")
def physics_usd(physics_ifc: Path) -> Path:
    target = physics_ifc.with_name("physics.usda")
    convert(physics_ifc, target, ConversionOptions(threads=1, meters_per_unit=0.01))  # centimetre stage
    return target


def _prim(stage, name: str):
    return next(p for p in stage.Traverse() if p.GetAttribute("ifc:name").Get() == name)


def test_physics_becomes_usd_physics(physics_ifc: Path, physics_usd: Path) -> None:
    from pxr import Usd, UsdPhysics, UsdShade

    assert validate_usd(physics_usd, source=physics_ifc).ok
    stage = Usd.Stage.Open(str(physics_usd))
    assert UsdPhysics.GetStageKilogramsPerUnit(stage) == 1.0
    wall = _prim(stage, "Wall 1")
    mass = UsdPhysics.MassAPI(wall)
    assert wall.HasAPI(UsdPhysics.MassAPI)
    assert mass.GetMassAttr().Get() == pytest.approx(MASS, rel=1e-7)
    assert mass.GetDensityAttr().Get() == pytest.approx(2400.0 * 1e-6)  # kg per cubic centimetre
    assert tuple(mass.GetCenterOfMassAttr().Get()) == pytest.approx((250.0, 10.0, 150.0))  # centimetres
    body = UsdPhysics.RigidBodyAPI(wall)
    assert body.GetRigidBodyEnabledAttr().Get() is True and body.GetKinematicEnabledAttr().Get() is True
    mesh = wall.GetChild("Body")
    assert mesh.HasAPI(UsdPhysics.CollisionAPI) and mesh.HasAPI(UsdPhysics.MeshCollisionAPI)
    assert UsdPhysics.MeshCollisionAPI(mesh).GetApproximationAttr().Get() == "convexHull"
    assert not wall.HasAPI(UsdPhysics.ArticulationRootAPI)  # one merged mesh is no articulation
    applied = list(wall.GetCustomDataByKey("ifc:physicsSchemas"))
    assert applied == ["PhysicsMassAPI", "PhysicsRigidBodyAPI", "PhysicsCollisionAPI"]
    materials = []
    for name in ("Wall 1", "Wall 2"):
        bound, _rel = UsdShade.MaterialBindingAPI(_prim(stage, name).GetChild("Body")).ComputeBoundMaterial("physics")
        materials.append(bound.GetPath())
    opening = _prim(stage, "Door Opening")  # a child of Wall 1: it must not inherit the wall's physics
    assert not UsdShade.MaterialBindingAPI(opening).ComputeBoundMaterial("physics")[0]
    assert materials[0] == materials[1]  # identical materials are shared
    physics_material = UsdPhysics.MaterialAPI(stage.GetPrimAtPath(materials[0]))
    assert physics_material.GetStaticFrictionAttr().Get() == pytest.approx(0.7)
    assert physics_material.GetRestitutionAttr().Get() == pytest.approx(0.05)
    # The IFC data stays as well.
    assert wall.GetAttribute("ifc:pset:IfcUSD_Physics:Mass").Get() == pytest.approx(MASS, rel=1e-12)


def test_unchanged_physics_round_trip_writes_nothing(physics_ifc: Path, physics_usd: Path, tmp_path: Path) -> None:
    result = convert_usd_to_ifc(physics_usd, tmp_path / "same.ifc", source=physics_ifc)
    assert {k: v for k, v in result.report.counts.items() if v and k != "matched"} == {}


def test_standalone_keeps_full_precision(physics_usd: Path, tmp_path: Path) -> None:
    import ifcopenshell.util.element as element

    model = convert_usd_to_ifc(physics_usd, tmp_path / "rebuilt.ifc").model
    wall = next(w for w in model.by_type("IfcWall") if w.Name == "Wall 1")
    physics = element.get_psets(wall)["IfcUSD_Physics"]
    assert physics["Mass"] == MASS  # the exact IFC value, not the 32-bit float copy
    assert physics["CenterOfMass"] == pytest.approx([2500.0, 100.0, 1500.0])


def _edit(usd: Path, layer_path: Path, edit) -> Path:
    from pxr import Sdf, Usd

    layer = Sdf.Layer.CreateNew(str(layer_path))
    session = Sdf.Layer.CreateAnonymous()
    session.subLayerPaths = [layer.identifier, str(usd)]
    stage = Usd.Stage.Open(session)
    stage.SetEditTarget(Usd.EditTarget(layer))
    edit(stage)
    layer.Save()
    return layer_path


def test_edits_in_the_simulation_and_on_ifc_data_are_both_written(
    physics_ifc: Path, physics_usd: Path, tmp_path: Path
) -> None:
    import ifcopenshell.util.element as element
    from pxr import UsdPhysics

    def edit(stage) -> None:
        wall = _prim(stage, "Wall 1")
        UsdPhysics.MassAPI(wall).GetMassAttr().Set(2000.0)  # changed in Isaac Sim
        UsdPhysics.RigidBodyAPI(wall).GetKinematicEnabledAttr().Set(False)
        wall.GetAttribute("ifc:pset:IfcUSD_Physics:Restitution").Set(0.2)  # changed as IFC data

    layer = _edit(physics_usd, tmp_path / "edits.usda", edit)
    result = convert_usd_to_ifc(physics_usd, tmp_path / "edited.ifc", source=physics_ifc, layers=[layer])
    walls = {w.Name: element.get_psets(w)["IfcUSD_Physics"] for w in result.model.by_type("IfcWall")}
    assert walls["Wall 1"]["Mass"] == pytest.approx(2000.0)
    assert walls["Wall 1"]["KinematicEnabled"] is False
    assert walls["Wall 1"]["Restitution"] == pytest.approx(0.2)
    assert walls["Wall 1"]["StaticFriction"] == pytest.approx(0.7)
    assert walls["Wall 2"]["Mass"] == MASS  # untouched element, untouched values
    assert walls["Wall 2"]["Restitution"] == pytest.approx(0.05)


def _remove_wall_physics(stage) -> None:
    from pxr import UsdPhysics

    wall = _prim(stage, "Wall 1")
    wall.RemoveAPI(UsdPhysics.RigidBodyAPI)  # Isaac Sim: Remove > Physics > Rigid Body
    wall.GetChild("Body").RemoveAPI(UsdPhysics.CollisionAPI)


@pytest.mark.parametrize("where", ["layer", "in place"])
def test_physics_removed_in_the_simulation_is_disabled(
    physics_ifc: Path, physics_usd: Path, tmp_path: Path, where: str
) -> None:
    import shutil

    import ifcopenshell.util.element as element
    from pxr import Usd

    if where == "layer":
        usd = physics_usd
        layers = [_edit(physics_usd, tmp_path / "removed.usda", _remove_wall_physics)]
    else:  # edited and saved in the converted file itself, as Isaac Sim does by default
        usd = Path(shutil.copy(physics_usd, tmp_path / "saved.usda"))
        stage = Usd.Stage.Open(str(usd))
        _remove_wall_physics(stage)
        stage.GetRootLayer().Save()
        layers = []
    result = convert_usd_to_ifc(usd, tmp_path / "removed.ifc", source=physics_ifc, layers=layers)
    walls = {w.Name: element.get_psets(w)["IfcUSD_Physics"] for w in result.model.by_type("IfcWall")}
    assert walls["Wall 1"]["RigidBodyEnabled"] is False
    assert walls["Wall 1"]["CollisionEnabled"] is False
    assert walls["Wall 1"]["Mass"] == MASS
    assert walls["Wall 2"]["RigidBodyEnabled"] is True and walls["Wall 2"]["CollisionEnabled"] is True


def test_physics_off_stage_round_trip_writes_nothing(physics_ifc: Path, tmp_path: Path) -> None:
    usd = tmp_path / "no_physics.usda"
    convert(physics_ifc, usd, ConversionOptions(threads=1, physics=False))
    result = convert_usd_to_ifc(usd, tmp_path / "same.ifc", source=physics_ifc)
    assert {k: v for k, v in result.report.counts.items() if v and k != "matched"} == {}


def test_center_of_mass_follows_the_prim_scale(physics_ifc: Path, physics_usd: Path, tmp_path: Path) -> None:
    import ifcopenshell.util.element as element
    from pxr import Gf, UsdGeom, UsdPhysics

    def edit(stage) -> None:
        crate = UsdGeom.Xform.Define(stage, "/World/Crate")
        crate.AddScaleOp().Set(Gf.Vec3f(2.0, 2.0, 2.0))
        UsdGeom.Cube.Define(stage, "/World/Crate/Box").GetSizeAttr().Set(50.0)
        mass = UsdPhysics.MassAPI.Apply(crate.GetPrim())
        mass.GetMassAttr().Set(12.0)
        mass.GetCenterOfMassAttr().Set(Gf.Vec3f(10.0, 0.0, 5.0))  # centimetres, before the scale

    layer = _edit(physics_usd, tmp_path / "crate.usda", edit)
    result = convert_usd_to_ifc(physics_usd, tmp_path / "crate.ifc", source=physics_ifc, layers=[layer])
    crate = next(e for e in result.model.by_type("IfcProduct") if e.Name == "Crate")
    physics = element.get_psets(crate)["IfcUSD_Physics"]
    assert physics["Mass"] == pytest.approx(12.0)
    assert physics["CenterOfMass"] == pytest.approx([200.0, 0.0, 100.0])  # millimetres, scaled


@pytest.mark.parametrize(
    "options", [ConversionOptions.for_profile("unity", threads=1), ConversionOptions(physics=False)]
)
def test_physics_can_be_left_out(physics_ifc: Path, tmp_path: Path, options: ConversionOptions) -> None:
    from pxr import Usd, UsdPhysics

    target = tmp_path / "plain.usda"
    result = convert(physics_ifc, target, options)
    stage = Usd.Stage.Open(str(target))
    assert not any(p.HasAPI(UsdPhysics.MassAPI) for p in stage.Traverse())
    assert result.report.counts["physicsPrims"] == 0
