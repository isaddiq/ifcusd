"""End-to-end conversion tests on synthetic IFC2X3 / IFC4 / IFC4X3 models."""

from __future__ import annotations

import filecmp

import numpy as np
import pytest
from conftest import SCHEMAS, open_stage, prim_named, prims_by_class
from model_factory import UNICODE_TEXT

from ifcusd import ConversionOptions, convert, validate_usd


def test_conversion_is_complete_and_valid(converted, schema) -> None:
    source, target, result = converted[schema]
    report = result.report
    assert report.complete, report.warnings
    assert report.missing_count == 0
    assert not report.geometry_errors
    validation = validate_usd(target, source=source)
    assert validation.ok, [(i.code, i.message) for i in validation.errors]
    assert validation.stats["missingObjects"] == 0
    assert validation.stats["missingPropertyValues"] == 0
    assert validation.stats["sourcePropertyValues"] > 0


def test_stage_metadata_and_provenance(converted, schema) -> None:
    source, target, _ = converted[schema]
    stage = open_stage(target)
    from pxr import UsdGeom

    assert stage.GetDefaultPrim().GetPath().pathString == "/World"
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    data = stage.GetRootLayer().customLayerData["ifcusd"]
    assert data["ifcSchema"] == schema
    assert data["schemaVersion"] == "1.0"
    assert "threads" not in data["options"]
    root = stage.GetPrimAtPath("/World")
    assert root.GetAttribute("ifc:file:schema").Get() == schema
    assert root.GetAttribute("ifc:units:LENGTHUNIT").Get() == "MILLIMETRE"
    assert root.GetAttribute("ifc:units:lengthScale").Get() == pytest.approx(0.001)
    assert root.GetAttribute("ifc:geo:refLatitude").Get() == pytest.approx(-33.8666667)
    if schema != "IFC2X3":
        assert root.GetAttribute("ifc:geo:crs:name").Get() == "EPSG:7856"
        assert root.GetAttribute("ifc:geo:eastings").Get() == pytest.approx(334000.0)


def test_spatial_hierarchy_and_kinds(converted, schema) -> None:
    from pxr import Usd

    _, target, _ = converted[schema]
    stage = open_stage(target)
    wall = prim_named(stage, "Wall 1")
    chain = []
    prim = wall.GetParent()
    while prim and prim.GetPath().pathString != "/":
        chain.append(prim.GetAttribute("ifc:class").Get() or prim.GetName())
        prim = prim.GetParent()
    assert chain[:4] == ["IfcBuildingStorey", "IfcBuilding", "IfcSite", "IfcProject"]
    assert Usd.ModelAPI(wall).GetKind() == "component"
    assert Usd.ModelAPI(wall.GetParent()).GetKind() == "group"
    assert wall.GetAttribute("ifc:parentRelationship").Get() == "contains"
    first_floor = prim_named(stage, "First Floor")
    from pxr import UsdGeom

    translation = UsdGeom.Xformable(first_floor).GetLocalTransformation().ExtractTranslation()
    assert translation[2] == pytest.approx(3.0)  # 3000 mm storey elevation, in metres


def test_identity_attributes_keep_original_values(converted, schema) -> None:
    source, target, _ = converted[schema]
    import ifcopenshell

    model = ifcopenshell.open(str(source))
    stage = open_stage(target)
    for wall in model.by_type("IfcWall"):
        prim = prim_named(stage, wall.Name)
        assert prim.GetAttribute("ifc:globalId").Get() == wall.GlobalId
        assert prim.GetAttribute("ifc:stepId").Get() == wall.id()
        assert prim.GetAttribute("ifc:class").Get() == "IfcWall"
        assert prim.GetMetadata("displayName") == wall.Name


def test_every_property_kind_is_typed(converted, schema) -> None:
    _, target, _ = converted[schema]
    stage = open_stage(target)
    wall = prim_named(stage, "Wall 1")
    ns = "ifc:pset:Custom_Data_Ext"

    def attr(name):
        attribute = wall.GetAttribute(f"{ns}:{name}")
        assert attribute, name
        return attribute

    assert attr("Load_bearing").Get() is True
    assert attr("Count").Get() == 3
    area = attr("Area_m2")
    assert area.Get() == pytest.approx(12.5)
    assert area.GetMetadata("displayName") == "Area (m²)"
    assert area.GetMetadata("displayGroup") == "Custom Data (Ext)"
    assert attr("Note").Get() == UNICODE_TEXT
    status = attr("Status")
    assert status.GetTypeName() == "token" and status.Get() == "NEW"
    assert list(status.GetMetadata("allowedTokens")) == ["NEW", "EXISTING", "DEMOLISH"]
    upper = attr("Tolerance:upperBoundValue")
    assert upper.Get() == pytest.approx(5.0)
    assert upper.GetCustomDataByKey("ifcUnit") == "MILLIMETRE"
    assert list(attr("Offsets").Get()) == pytest.approx([1.0, 2.5])
    assert list(attr("Curve:definedValues").Get()) == pytest.approx([10.0, 20.0])
    assert attr("Assembly:Inner").Get() == "x"
    reference = wall.GetRelationship(f"{ns}:Made_Of")
    assert reference and reference.GetTargets()[0].name.startswith("Concrete")


def test_quantities_keep_project_units(converted, schema) -> None:
    _, target, _ = converted[schema]
    stage = open_stage(target)
    wall = prim_named(stage, "Wall 1")
    length = wall.GetAttribute("ifc:qto:Qto_WallBaseQuantities:Length")
    assert length.Get() == pytest.approx(5000.0)
    assert length.GetCustomDataByKey("ifcUnit") == "MILLIMETRE"
    assert length.GetCustomDataByKey("ifcType") == "IfcQuantityLength"
    assert "IfcQuantitySetAPI:Qto_WallBaseQuantities" in wall.GetMetadata("apiSchemas").GetAddedOrExplicitItems()


def test_type_properties_are_inherited_and_tagged(converted, schema) -> None:
    _, target, _ = converted[schema]
    stage = open_stage(target)
    wall = prim_named(stage, "Wall 1")
    fire = wall.GetAttribute("ifc:pset:Pset_WallCommon:FireRating")
    assert fire.Get() == "REI90"  # the occurrence value wins over the type's REI60
    external = wall.GetAttribute("ifc:pset:Pset_WallCommon:IsExternal")
    assert external.Get() is True
    assert external.GetCustomDataByKey("ifcSource") == "type"
    wall_type = stage.GetPrimAtPath(wall.GetRelationship("ifc:isTypedBy").GetTargets()[0])
    assert wall_type.GetTypeName() == "Scope"
    assert wall_type.GetAttribute("ifc:pset:Pset_WallCommon:FireRating").Get() == "REI60"
    assert wall.GetCustomDataByKey("ifc:propertySets:Pset_WallCommon:source") == "type+occurrence"


def test_relationships_are_preserved(converted, schema) -> None:
    _, target, _ = converted[schema]
    stage = open_stage(target)
    wall = prim_named(stage, "Wall 1")
    material = stage.GetPrimAtPath(wall.GetRelationship("ifc:hasMaterial").GetTargets()[0])
    assert material.GetAttribute("ifc:class").Get() == "IfcMaterialLayerSetUsage"
    classification = stage.GetPrimAtPath(wall.GetRelationship("ifc:hasClassification").GetTargets()[0])
    assert classification.GetAttribute("ifc:class").Get() == "IfcClassificationReference"

    opening = prims_by_class(stage, "IfcOpeningElement")[0]
    assert opening.GetParent() == wall
    assert opening.GetAttribute("ifc:parentRelationship").Get() == "voids"
    door = prims_by_class(stage, "IfcDoor")[0]
    assert door.GetRelationship("ifc:fills").GetTargets() == [opening.GetPath()]

    group = prim_named(stage, "Envelope")
    members = group.GetRelationship("collection:members:includes").GetTargets()
    assert wall.GetPath() in members and len(members) == 2
    layer = prim_named(stage, "A-WALL")
    assert len(layer.GetRelationship("collection:members:includes").GetTargets()) == 1

    connection = prims_by_class(stage, "IfcRelConnectsElements")[0]
    assert connection.GetPath().pathString.startswith("/World/IfcData/Relationships/IfcRelConnectsElements/")
    assert connection.GetRelationship("ifc:relatingElement").GetTargets() == [wall.GetPath()]
    process = prims_by_class(stage, "IfcRelAssignsToProcess")[0]
    assert len(process.GetRelationship("ifc:relatedObjects").GetTargets()) == 2
    orphan = prim_named(stage, "Orphan Set")
    assert orphan.GetAttribute("ifc:pset:Orphan_Set:Lonely").Get() == "yes"


def test_shared_type_geometry_is_instanced(converted, schema) -> None:
    _, target, result = converted[schema]
    stage = open_stage(target)
    tables = [prim_named(stage, "Table 1"), prim_named(stage, "Table 2")]
    bodies = [table.GetChild("Body") for table in tables]
    assert all(body.IsInstance() for body in bodies)
    assert bodies[0].GetPrototype() == bodies[1].GetPrototype()
    assert result.report.counts["prototypes"] >= 1


def test_spaces_are_hidden_and_semantics_labelled(converted, schema) -> None:
    from pxr import UsdGeom

    _, target, _ = converted[schema]
    stage = open_stage(target)
    space = prim_named(stage, "Room 1")
    assert UsdGeom.Imageable(space.GetChild("Body")).ComputeVisibility() == UsdGeom.Tokens.invisible
    assert (
        UsdGeom.Imageable(prim_named(stage, "Table 1").GetChild("Body")).ComputeVisibility() != UsdGeom.Tokens.invisible
    )
    wall = prim_named(stage, "Wall 1")
    assert list(wall.GetAttribute("semantics:labels:class").Get()) == ["IfcWall"]


def test_materials_come_from_ifc_styles(converted, schema) -> None:
    from pxr import UsdShade

    _, target, _ = converted[schema]
    stage = open_stage(target)
    material = UsdShade.Material(stage.GetPrimAtPath("/World/Looks/Concrete_Grey"))
    assert material
    shader = UsdShade.Shader(stage.GetPrimAtPath("/World/Looks/Concrete_Grey/PreviewSurface"))
    assert tuple(shader.GetInput("diffuseColor").Get()) == pytest.approx((0.5, 0.55, 0.6))
    assert shader.GetInput("opacity").Get() == pytest.approx(0.75)
    concrete = prim_named(stage, "Concrete")
    assert concrete.GetRelationship("ifc:surfaceStyle").GetTargets() == [material.GetPath()]


def test_world_geometry_matches_ifcopenshell(converted, schema) -> None:
    import ifcopenshell
    import ifcopenshell.geom
    from pxr import Usd, UsdGeom

    source, target, _ = converted[schema]
    model = ifcopenshell.open(str(source))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("weld-vertices", False)
    stage = open_stage(target)
    checked = 0
    for product in model.by_type("IfcProduct"):
        if product.is_a("IfcOpeningElement") or not product.Representation:
            continue
        shape = ifcopenshell.geom.create_shape(settings, product)
        expected = np.array(shape.geometry.verts).reshape(-1, 3)
        body = prim_named(stage, product.Name).GetChild("Body")
        points = None
        for prim in Usd.PrimRange(body, Usd.TraverseInstanceProxies()):
            if prim.IsA(UsdGeom.Mesh):
                local = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
                matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
                points = (np.c_[local, np.ones(len(local))] @ matrix)[:, :3]
        assert points is not None, product.Name
        np.testing.assert_allclose(points.min(axis=0), expected.min(axis=0), atol=1e-4)
        np.testing.assert_allclose(points.max(axis=0), expected.max(axis=0), atol=1e-4)
        checked += 1
    assert checked >= 6


def test_output_is_reproducible_across_thread_counts(ifc_files, tmp_path) -> None:
    source = ifc_files["IFC4"]
    outputs = []
    for threads in (1, 4):
        target = tmp_path / f"t{threads}.usda"
        convert(source, target, ConversionOptions.for_profile("omniverse", threads=threads))
        outputs.append(target)
    assert filecmp.cmp(*outputs, shallow=False)


def test_schema_list_is_complete() -> None:
    assert set(SCHEMAS) == {"IFC2X3", "IFC4", "IFC4X3_ADD2"}
