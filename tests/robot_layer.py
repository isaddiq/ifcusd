"""Authors a robot-training override layer on top of a converted stage.

It edits and adds data the way a robot or simulation pipeline does in NVIDIA
Omniverse / Isaac Sim: in its own layer, never touching the converted file.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade


def find(stage: Usd.Stage, name: str) -> Usd.Prim:
    for prim in stage.TraverseAll():
        if prim.GetAttribute("ifc:name").Get() == name:
            return prim
    raise LookupError(name)


def write_robot_layer(converted: Path, layer_path: Path) -> Path:
    """Write ``layer_path`` (a sublayer-able override layer) for ``converted``; returns it."""
    layer = Sdf.Layer.CreateNew(str(layer_path))
    root = Sdf.Layer.CreateAnonymous("robot_session.usda")
    root.subLayerPaths = [layer.identifier, str(converted)]
    stage = Usd.Stage.Open(root)
    stage.SetEditTarget(Usd.EditTarget(layer))
    layer.timeCodesPerSecond = 10.0
    layer.startTimeCode, layer.endTimeCode = 0.0, 20.0
    layer.pseudoRoot.SetInfo("kilogramsPerUnit", 1.0)

    wall, wall2 = find(stage, "Wall 1"), find(stage, "Wall 2")
    table1, table2, room = find(stage, "Table 1"), find(stage, "Table 2"), find(stage, "Room 1")
    storey, door = find(stage, "Ground Floor"), find(stage, "Door")

    # Edited IFC data: an attribute, an occurrence property and an override of a type property.
    wall.GetAttribute("ifc:name").Set("Wall 1 (scanned)")
    wall.GetAttribute("ifc:pset:Pset_WallCommon:FireRating").Set("REI120")
    wall2.GetAttribute("ifc:pset:Pset_WallCommon:IsExternal").Set(False)
    wall.GetAttribute("ifc:qto:Qto_WallBaseQuantities:Length").Set(5012.5)

    # Robot data in the extension namespaces (SI units).
    wall.CreateAttribute("robot:navigation:traversable", Sdf.ValueTypeNames.Bool).Set(False)
    clearance = wall.CreateAttribute("robot:navigation:clearance", Sdf.ValueTypeNames.Double)
    clearance.Set(0.85)
    clearance.SetCustomDataByKey("ifcValueType", "IfcLengthMeasure")
    wall.CreateAttribute("robot:surfaceRoughness", Sdf.ValueTypeNames.Float).Set(0.35)
    room.CreateAttribute("robot:inspection:visits", Sdf.ValueTypeNames.Int).Set(4)
    room.CreateAttribute("robot:inspection:waypoint", Sdf.ValueTypeNames.Point3f).Set(Gf.Vec3f(1.5, 2.0, 0.0))
    temperature = room.CreateAttribute("sim:sensors:temperature", Sdf.ValueTypeNames.Double)
    for frame in range(11):
        temperature.Set(20.0 + 0.5 * frame, Usd.TimeCode(frame))
    # A property set authored directly in the IFC namespace.
    room.CreateAttribute("ifc:pset:Robot_Inspection:Inspector", Sdf.ValueTypeNames.String).Set("AMR-07")

    # Physics and semantics on an existing element.
    UsdPhysics.MassAPI.Apply(table1).CreateMassAttr(25.0)
    body = table1.GetChild("Body")
    UsdPhysics.CollisionAPI.Apply(body)
    material = UsdShade.Material.Define(stage, "/World/PhysicsMaterials/Wood")
    physics = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    physics.CreateStaticFrictionAttr(0.6)
    physics.CreateDynamicFrictionAttr(0.45)
    physics.CreateRestitutionAttr(0.1)
    UsdShade.MaterialBindingAPI.Apply(table1).Bind(
        material, UsdShade.Tokens.weakerThanDescendants, materialPurpose="physics"
    )
    table1.CreateAttribute("semantics:labels:affordance", Sdf.ValueTypeNames.TokenArray).Set(["movable", "graspable"])
    # Labelling tools often write to the mesh: it still describes the element.
    body.CreateAttribute("semantics:labels:material", Sdf.ValueTypeNames.TokenArray).Set(["wood"])
    body.CreateAttribute("robot:perception:detections", Sdf.ValueTypeNames.Int).Set(17)

    # A robot rearranged the furniture.
    UsdGeom.Xformable(table2).MakeMatrixXform().Set(_translated(table2, Gf.Vec3d(1.0, 0.5, 0.0)))

    # A new articulated robot that drives through the building (trajectory).
    robot = UsdGeom.Xform.Define(stage, "/World/Robot")
    UsdPhysics.ArticulationRootAPI.Apply(robot.GetPrim())
    robot.GetPrim().CreateAttribute("robot:battery", Sdf.ValueTypeNames.Double).Set(0.82)
    translate = robot.AddTranslateOp()
    for frame in range(21):
        translate.Set(Gf.Vec3d(0.5 * frame, 1.0, 0.0), Usd.TimeCode(frame))
    base = UsdGeom.Cube.Define(stage, "/World/Robot/Base")
    base.CreateSizeAttr(0.6)
    base.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.3))
    base.CreateDisplayColorAttr([Gf.Vec3f(1.0, 0.5, 0.0)])
    UsdPhysics.MassAPI.Apply(base.GetPrim()).CreateMassAttr(40.0)
    head = UsdGeom.Sphere.Define(stage, "/World/Robot/Head")
    head.CreateRadiusAttr(0.2)
    head.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.8))
    UsdPhysics.MassAPI.Apply(head.GetPrim()).CreateMassAttr(5.0)
    UsdPhysics.RevoluteJoint.Define(stage, "/World/Robot/Neck")
    robot.GetPrim().CreateAttribute("semantics:labels:class", Sdf.ValueTypeNames.TokenArray).Set(["robot"])

    # A lidar mounted on Wall 2, and an obstacle placed on the ground floor.
    lidar = stage.DefinePrim(f"{wall2.GetPath()}/Lidar", "OmniLidar")
    UsdGeom.Xformable(lidar).AddTranslateOp().Set(Gf.Vec3d(2.0, 0.1, 2.5))
    obstacle = UsdGeom.Cylinder.Define(stage, f"{storey.GetPath()}/Obstacle")
    obstacle.CreateRadiusAttr(0.25)
    obstacle.CreateHeightAttr(1.0)
    obstacle.AddTranslateOp().Set(Gf.Vec3d(3.0, 1.0, 0.5))

    # A copy-pasted wall (it keeps the GlobalId of its original) and a removed door.
    copy = stage.DefinePrim(f"{storey.GetPath()}/Wall_2_copy", "Xform")
    copy.GetReferences().AddInternalReference(wall2.GetPath())
    UsdGeom.Xformable(copy).MakeMatrixXform().Set(_translated(wall2, Gf.Vec3d(0.0, 6.0, 0.0)))
    door.SetActive(False)

    layer.Save()
    return layer_path


def _translated(prim: Usd.Prim, offset: Gf.Vec3d) -> Gf.Matrix4d:
    """Local transform that moves ``prim`` by ``offset`` (in its parent's frame)."""
    local = UsdGeom.Xformable(prim).GetLocalTransformation()
    return local * Gf.Matrix4d().SetTranslate(offset)


def write_plain_scene(path: Path) -> Path:
    """A USD scene that was never IFC, as built in Isaac Sim: Y-up, centimetres, two robot styles."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    Usd.ModelAPI(UsdGeom.Xform.Define(stage, "/World/Warehouse").GetPrim()).SetKind("assembly")
    shelf = UsdGeom.Xform.Define(stage, "/World/Warehouse/Shelf_01")
    Usd.ModelAPI(shelf.GetPrim()).SetKind("component")
    shelf.AddTranslateOp().Set(Gf.Vec3d(300, 0, -200))  # 3 m east, 2 m north (-Z is north when Y is up)
    frame = UsdGeom.Cube.Define(stage, "/World/Warehouse/Shelf_01/Frame")
    frame.CreateSizeAttr(100)
    frame.AddTranslateOp().Set(Gf.Vec3d(0, 50, 0))
    # A mobile robot whose root prim is the articulation root.
    carter = UsdGeom.Xform.Define(stage, "/World/Carter")
    UsdPhysics.ArticulationRootAPI.Apply(carter.GetPrim())
    UsdGeom.Sphere.Define(stage, "/World/Carter/Chassis").CreateRadiusAttr(30)
    # An arm whose root link is the articulation root, with sibling links.
    arm = UsdGeom.Xform.Define(stage, "/World/Franka")
    arm.AddTranslateOp().Set(Gf.Vec3d(-100, 0, 0))
    for index in range(3):
        link = UsdGeom.Xform.Define(stage, f"/World/Franka/link{index}")
        link.AddTranslateOp().Set(Gf.Vec3d(0, 20 * index, 0))
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        UsdPhysics.MassAPI.Apply(link.GetPrim()).CreateMassAttr(2.0)
        UsdGeom.Cylinder.Define(stage, f"/World/Franka/link{index}/visual").CreateRadiusAttr(5)
    UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath("/World/Franka/link0"))
    # Things that are not part of the facility.
    stage.DefinePrim("/World/Light", "DistantLight")
    stage.DefinePrim("/OmniverseKit_Persp", "Camera")
    stage.GetRootLayer().Save()
    return path
