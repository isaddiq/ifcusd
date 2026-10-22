"""USD to IFC: standalone reconstruction, merge with robot data, and the full round trip."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from ifcusd import ConversionOptions, IfcExportOptions, convert, convert_usd_to_ifc, validate_usd
from ifcusd.reverse.converter import validate_ifc


def _psets(entity):
    import ifcopenshell.util.element as element

    def clean(value):
        if hasattr(value, "wrappedValue"):  # defined-type values compare by type and value across files
            return (value.is_a(), clean(value.wrappedValue))
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "id"}
        if isinstance(value, float):
            return round(value, 9)
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    return clean(element.get_psets(entity))


def _issue_keys(model) -> set[tuple[str, str]]:
    return {
        (i.get("instance", "").split("(")[0].split("=")[-1], i.get("attribute", ""))
        for i in validate_ifc(model)["issues"]
    }


def _by_name(model, name: str, ifc_class: str = "IfcProduct") -> list:
    return [e for e in model.by_type(ifc_class) if e.Name == name]


# ---------------------------------------------------------------------- standalone


@pytest.fixture(scope="module")
def rebuilt(converted, model_dir: Path) -> dict[str, tuple]:
    """Every converted synthetic model rebuilt from USD alone: {schema: (source model, result)}."""
    import ifcopenshell

    results = {}
    for schema, (source, usd, _forward) in converted.items():
        result = convert_usd_to_ifc(usd, model_dir / f"{schema}.rebuilt.ifc")
        results[schema] = (ifcopenshell.open(str(source)), result)
    return results


def test_standalone_keeps_every_object_and_value(rebuilt, schema) -> None:
    import ifcopenshell.util.element as element

    source, result = rebuilt[schema]
    model = result.model
    assert result.report.mode == "standalone"
    assert model.schema_identifier == source.schema_identifier
    for entity in source.by_type("IfcObjectDefinition"):
        copy = model.by_guid(entity.GlobalId)
        assert copy.is_a() == entity.is_a()
        assert copy.Name == entity.Name
        assert _psets(copy) == _psets(entity), entity.Name
        for relation in (element.get_container, element.get_aggregate, element.get_type):
            expected, actual = relation(entity), relation(copy)
            assert (expected and expected.GlobalId) == (actual and actual.GlobalId), (entity.Name, relation.__name__)
        expected_material, actual_material = element.get_material(entity), element.get_material(copy)
        assert (expected_material and expected_material.is_a()) == (actual_material and actual_material.is_a())


def test_standalone_keeps_relationships_units_and_validity(rebuilt, schema) -> None:
    source, result = rebuilt[schema]
    model = result.model
    assert Counter(r.is_a() for r in model.by_type("IfcRelationship")) == Counter(
        r.is_a() for r in source.by_type("IfcRelationship")
    )
    units = sorted((u.UnitType, getattr(u, "Prefix", None), u.Name) for u in model.by_type("IfcNamedUnit"))
    assert units == sorted((u.UnitType, getattr(u, "Prefix", None), u.Name) for u in source.by_type("IfcNamedUnit"))
    layer = _by_name(model, "A-WALL", "IfcPresentationLayerAssignment")[0]
    assert len(layer.AssignedItems) == 1
    assert [s.Name for s in model.by_type("IfcSurfaceStyle")] == ["Concrete Grey"]  # no invented default styles
    assert _issue_keys(model) <= _issue_keys(source)  # nothing invalid beyond what the source has


def test_standalone_geometry_matches_source(rebuilt, schema) -> None:
    import ifcopenshell.geom

    source, result = rebuilt[schema]
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    checked = 0
    for entity in source.by_type("IfcProduct"):
        if entity.is_a("IfcOpeningElement") or not entity.Representation:
            continue
        # Keep each shape referenced while reading its buffers (IfcOpenShell 0.8 frees them with the shape).
        expected_shape = ifcopenshell.geom.create_shape(settings, entity)
        expected = np.array(expected_shape.geometry.verts).reshape(-1, 3)
        actual_shape = ifcopenshell.geom.create_shape(settings, result.model.by_guid(entity.GlobalId))
        actual = np.array(actual_shape.geometry.verts).reshape(-1, 3)
        np.testing.assert_allclose(actual.min(axis=0), expected.min(axis=0), atol=1e-4)
        np.testing.assert_allclose(actual.max(axis=0), expected.max(axis=0), atol=1e-4)
        checked += 1
    assert checked >= 6
    tables = [result.model.by_guid(t.GlobalId) for t in _by_name(source, "Table 1") + _by_name(source, "Table 2")]
    items = [t.Representation.Representations[0].Items[0] for t in tables]
    assert all(item.is_a("IfcMappedItem") for item in items)
    assert items[0].MappingSource == items[1].MappingSource  # instancing survives


# ---------------------------------------------------------------------- merge


def test_unchanged_usd_writes_nothing(converted, model_dir: Path, schema) -> None:
    source, usd, _forward = converted[schema]
    result = convert_usd_to_ifc(usd, model_dir / f"{schema}.unchanged.ifc", source=source, validate=True)
    report = result.report
    assert report.mode == "merge" and report.source_matches_usd is True
    assert {k: v for k, v in report.counts.items() if v and k != "matched"} == {}
    assert report.introduced_errors == 0


@pytest.fixture(scope="module")
def merged(converted, model_dir: Path) -> dict[str, tuple]:
    """Each converted model with a robot-training layer, merged back: {schema: (source, layer, result)}."""
    from robot_layer import write_robot_layer

    results = {}
    for schema, (source, usd, _forward) in converted.items():
        layer = write_robot_layer(usd, model_dir / f"robot_{schema}.usda")
        result = convert_usd_to_ifc(
            usd,
            model_dir / f"{schema}.merged.ifc",
            source=source,
            layers=[layer],
            validate=True,
            report_path=model_dir / f"{schema}.merged.json",
        )
        results[schema] = (source, layer, result)
    return results


def test_merge_writes_edited_ifc_data(merged, schema) -> None:
    import ifcopenshell.util.element as element

    _source, _layer, result = merged[schema]
    model = result.model
    assert result.report.introduced_errors == 0, result.report.validation
    wall = _by_name(model, "Wall 1 (scanned)")[0]
    psets = _psets(wall)
    assert psets["Pset_WallCommon"]["FireRating"] == "REI120"
    assert psets["Qto_WallBaseQuantities"]["Length"] == pytest.approx(5012.5)
    # Overriding a type property creates an occurrence property; the shared type set is unchanged.
    original_wall2 = [w for w in _by_name(model, "Wall 2") if w.GlobalId == _wall2_guid(merged, schema)][0]
    assert _psets(original_wall2)["Pset_WallCommon"]["IsExternal"] is False
    wall_type = element.get_type(original_wall2)
    assert _psets(wall_type)["Pset_WallCommon"]["IsExternal"] is True


def _wall2_guid(merged, schema) -> str:
    import ifcopenshell

    source = ifcopenshell.open(str(merged[schema][0]))
    return _by_name(source, "Wall 2")[0].GlobalId


def test_merge_writes_robot_namespaces_in_project_units(merged, schema) -> None:
    model = merged[schema][2].model
    wall = _by_name(model, "Wall 1 (scanned)")[0]
    psets = _psets(wall)
    assert psets["Robot_Navigation"] == {"clearance": pytest.approx(850.0), "traversable": False}  # 0.85 m in mm
    assert psets["Robot_Common"]["surfaceRoughness"] == pytest.approx(0.35)
    room = _psets(_by_name(model, "Room 1")[0])
    assert room["Robot_Inspection"]["Inspector"] == "AMR-07"
    assert room["Robot_Inspection"]["visits"] == 4
    assert room["Robot_Inspection"]["waypoint"] == pytest.approx([1500.0, 2000.0, 0.0])


def test_merge_writes_time_series(merged, schema) -> None:
    model = merged[schema][2].model
    series = {
        p.Name: p.PropertyReference
        for p in model.by_type("IfcPropertyReferenceValue")
        if p.PropertyReference is not None and p.PropertyReference.is_a("IfcTimeSeries")
    }
    temperature = series["temperature"]
    assert temperature.is_a("IfcRegularTimeSeries") and temperature.DataOrigin == "SIMULATED"
    assert temperature.TimeStep == pytest.approx(0.1)
    assert [v.ListValues[0].wrappedValue for v in temperature.Values] == pytest.approx(
        [20 + 0.5 * i for i in range(11)]
    )
    trajectory = series["Trajectory"]
    assert len(trajectory.Values) == 21
    assert [v.wrappedValue for v in trajectory.Values[-1].ListValues] == pytest.approx(
        [10000.0, 1000.0, 0.0, 1, 0, 0, 0]
    )
    if schema != "IFC2X3":
        assert [r.Name for r in temperature.HasExternalReference] == [
            f"{_room_path(merged, schema)}.sim:sensors:temperature"
        ]


def _room_path(merged, schema) -> str:
    from pxr import Usd

    stage = Usd.Stage.Open(str(Path(merged[schema][1]).with_name(f"{schema}.usda")))
    return next(p for p in stage.Traverse() if p.GetAttribute("ifc:name").Get() == "Room 1").GetPath().pathString


def test_merge_writes_physics_semantics_and_moves(merged, schema) -> None:
    import ifcopenshell
    import ifcopenshell.util.placement as placement

    source_path, _layer, result = merged[schema]
    model = result.model
    assert _psets(_by_name(model, "Table 1")[0])["Robot_Perception"] == {"detections": 17}  # read from its Body mesh
    table = _psets(_by_name(model, "Table 1")[0])["IfcUSD_Physics"]
    assert table == {
        "Mass": pytest.approx(25.0),
        "CollisionEnabled": True,
        "StaticFriction": 0.6,
        "DynamicFriction": 0.45,
        "Restitution": 0.1,
    }
    labels = {
        (r.RelatingClassification.ReferencedSource.Name, r.RelatingClassification.Name, o.Name)
        for r in model.by_type("IfcRelAssociatesClassification")
        if r.RelatingClassification.is_a("IfcClassificationReference")
        for o in r.RelatedObjects
    }
    assert {
        ("affordance", "movable", "Table 1"),
        ("affordance", "graspable", "Table 1"),
        ("class", "robot", "Robot"),
        ("material", "wood", "Table 1"),
    } <= labels
    source = ifcopenshell.open(str(source_path))
    before = placement.get_local_placement(_by_name(source, "Table 2")[0].ObjectPlacement)[:3, 3]
    after = placement.get_local_placement(_by_name(model, "Table 2")[0].ObjectPlacement)[:3, 3]
    assert after - before == pytest.approx([1000.0, 500.0, 0.0])
    assert result.report.counts["placementsUpdated"] == 1


def test_merge_adds_new_objects(merged, schema) -> None:
    import ifcopenshell.util.element as element

    model = merged[schema][2].model
    robot = _by_name(model, "Robot")[0]
    assert robot.is_a("IfcBuildingElementProxy") and robot.ObjectType == "Robot"
    assert robot.Tag == "/World/Robot"
    assert element.get_container(robot) is not None
    assert len(robot.Representation.Representations[0].Items) == 2
    physics = _psets(robot)["IfcUSD_Physics"]
    assert physics["Mass"] == pytest.approx(45.0) and physics["Articulated"] is True and physics["JointCount"] == 1
    simulation = _psets(robot)["IfcUSD_Simulation"]
    assert simulation["PathLength"] == pytest.approx(10000.0) and simulation["Duration"] == pytest.approx(2.0)
    sensor_class = "IfcBuildingElementProxy" if schema == "IFC2X3" else "IfcSensor"
    lidars = _by_name(model, "Lidar")
    assert len(lidars) == 2 and all(lidar.is_a(sensor_class) for lidar in lidars)  # the copied wall carries one too
    for lidar in lidars:
        assert element.get_container(lidar).Name == "Ground Floor"
        hosts = [r.RelatingElement for r in model.by_type("IfcRelConnectsElements") if r.RelatedElement == lidar]
        assert [h.Name for h in hosts] == ["Wall 2"]
    obstacle = _by_name(model, "Obstacle")[0]
    assert element.get_container(obstacle).Name == "Ground Floor" and obstacle.Representation is not None


def test_new_objects_keep_their_identity_across_runs(merged, model_dir: Path) -> None:
    from pxr import Sdf

    source, layer, first = merged["IFC4"]
    second_run = Sdf.Layer.FindOrOpen(str(layer))
    copy = Sdf.Layer.CreateNew(str(model_dir / "robot_IFC4_run2.usda"))
    copy.TransferContent(second_run)
    copy.GetAttributeAtPath("/World/Robot.robot:battery").default = 0.5
    copy.Save()
    usd = Path(layer).with_name("IFC4.usda")
    second = convert_usd_to_ifc(usd, model_dir / "IFC4.run2.ifc", source=source, layers=[copy.realPath])
    robots = [_by_name(result.model, "Robot")[0] for result in (first, second)]
    assert robots[0].GlobalId == robots[1].GlobalId
    assert _psets(robots[1])["Robot_Common"]["battery"] == pytest.approx(0.5)


def test_merge_handles_copies_and_removals(merged, schema) -> None:
    import ifcopenshell.util.element as element

    _source, _layer, result = merged[schema]
    model, report = result.model, result.report
    walls = _by_name(model, "Wall 2")
    assert len(walls) == 2 and len({w.GlobalId for w in walls}) == 2
    assert report.counts["duplicates"] == 1
    assert {element.get_type(w).Name for w in walls} == {"WT-200"}
    if schema != "IFC2X3":
        assert len(element.get_type(walls[0]).Types) == 1  # one IfcRelDefinesByType per type
    assert [(item["ifcClass"], item["name"]) for item in report.not_in_usd] == [("IfcDoor", "Door")]
    assert _by_name(model, "Door")  # deactivated in USD, kept in IFC


def test_merge_declares_the_extension(merged, model_dir: Path, schema) -> None:
    model = merged[schema][2].model
    report = json.loads((model_dir / f"{schema}.merged.json").read_text(encoding="utf-8"))
    if schema == "IFC2X3":
        assert report["counts"]["templates"] == 0
        assert any("no property set templates" in w for w in report["warnings"])
        return
    library = _by_name(model, "IfcUSD Extension", "IfcProjectLibrary")[0]
    declared = {d.Name: d for rel in library.Declares for d in rel.RelatedDefinitions}
    assert set(declared) == {
        "IfcUSD_Physics",
        "IfcUSD_Simulation",
        "Robot_Common",
        "Robot_Inspection",
        "Robot_Navigation",
        "Robot_Perception",
        "Sim_Sensors",
    }
    physics = {t.Name: (t.TemplateType, t.PrimaryMeasureType) for t in declared["IfcUSD_Physics"].HasPropertyTemplates}
    assert physics["Mass"] == ("P_SINGLEVALUE", "IfcMassMeasure")
    navigation = {t.Name: t.PrimaryMeasureType for t in declared["Robot_Navigation"].HasPropertyTemplates}
    assert navigation == {"clearance": "IfcLengthMeasure", "traversable": "IfcBoolean"}
    assert "IfcWall" in declared["Robot_Navigation"].ApplicableEntity
    assert declared["Sim_Sensors"].HasPropertyTemplates[0].TemplateType == "P_REFERENCEVALUE"
    assert all(t.Defines for t in declared.values())  # IfcRelDefinesByTemplate links the sets


# ---------------------------------------------------------------------- round trip


def test_enriched_ifc_converts_back_and_stays_stable(merged, model_dir: Path, schema) -> None:
    from pxr import Usd

    merged_path = Path(merged[schema][2].output_path)
    usd = model_dir / f"{schema}.cycle.usda"
    forward = convert(merged_path, usd, ConversionOptions(threads=1))
    assert forward.report.complete
    assert validate_usd(usd, source=merged_path).ok
    stage = Usd.Stage.Open(str(usd))
    robot = next(p for p in stage.Traverse() if p.GetAttribute("ifc:name").Get() == "Robot")
    assert robot.GetAttribute("ifc:pset:Robot_Common:battery").Get() == pytest.approx(0.82)
    again = convert_usd_to_ifc(usd, model_dir / f"{schema}.cycle.ifc", source=merged_path, validate=True)
    assert {k: v for k, v in again.report.counts.items() if v and k != "matched"} == {}
    assert again.report.introduced_errors == 0


@pytest.mark.parametrize("profile, overrides", [("unity", {}), ("generic", {"meters_per_unit": 0.01})])
def test_y_up_and_scaled_stages_do_not_move_products(ifc_files, tmp_path: Path, profile, overrides) -> None:
    source = ifc_files["IFC4"]
    usd = tmp_path / "model.usda"
    convert(source, usd, ConversionOptions.for_profile(profile, threads=1, **overrides))
    result = convert_usd_to_ifc(usd, tmp_path / "model.ifc", source=source)
    assert result.report.counts.get("placementsUpdated", 0) == 0


def test_plain_usd_scene_becomes_ifc(tmp_path: Path) -> None:
    import ifcopenshell.util.element as element
    import ifcopenshell.util.placement as placement
    from robot_layer import write_plain_scene

    result = convert_usd_to_ifc(write_plain_scene(tmp_path / "scene.usda"), tmp_path / "scene.ifc", validate=True)
    model = result.model
    assert result.report.valid
    objects = {e.Name: e for e in model.by_type("IfcBuildingElementProxy")}
    assert set(objects) == {"Shelf_01", "Carter", "Franka"}
    assert objects["Carter"].ObjectType == objects["Franka"].ObjectType == "Robot"
    assert _psets(objects["Franka"])["IfcUSD_Physics"]["Mass"] == pytest.approx(6.0)
    shelf = placement.get_local_placement(objects["Shelf_01"].ObjectPlacement)[:3, 3]
    assert shelf == pytest.approx([3.0, 2.0, 0.0])  # Y-up centimetres became Z-up metres
    assert {element.get_container(e).Name for e in objects.values()} == {"Default Storey"}


def test_output_is_reproducible(converted, model_dir: Path) -> None:
    source, usd, _forward = converted["IFC4"]
    paths = []
    for index in range(2):
        path = model_dir / f"repro_{index}.ifc"
        convert_usd_to_ifc(usd, path)
        paths.append(path)
    first, second = (
        [line for line in p.read_text(encoding="utf-8").splitlines() if not line.startswith("FILE_NAME")] for p in paths
    )
    assert first == second


# ---------------------------------------------------------------------- CLI and options


def test_cli_to_ifc(converted, model_dir: Path, capsys) -> None:
    from ifcusd.cli import main

    source, usd, _forward = converted["IFC4"]
    target, report = model_dir / "cli.ifc", model_dir / "cli.json"
    status = main(["to-ifc", str(usd), str(target), "--source", str(source), "--validate", "--report", str(report)])
    assert status == 0
    assert "merge" in capsys.readouterr().out
    assert json.loads(report.read_text(encoding="utf-8"))["mode"] == "merge"
    assert main(["convert", str(usd), str(model_dir / "wrong.ifc")]) == 2


def test_export_options_are_validated() -> None:
    assert IfcExportOptions(time="12").time == 12.0
    for bad in (
        {"time": "soon"},
        {"extension_namespaces": ("ifc",)},
        {"time_origin": "yesterday"},
        {"new_object_class": "Wall"},
    ):
        with pytest.raises(ValueError):
            IfcExportOptions(**bad)


def test_schema_conflict_in_merge_mode(converted, model_dir: Path) -> None:
    from ifcusd import InputError

    source, usd, _forward = converted["IFC4"]
    with pytest.raises(InputError):
        convert_usd_to_ifc(usd, model_dir / "x.ifc", IfcExportOptions(schema="IFC2X3"), source=source)


# ---------------------------------------------------------------------- helpers


def test_value_helpers() -> None:
    from ifcusd.reverse.values import usd_element_type, values_equal

    assert usd_element_type("point3f[]") == ("float", 3, "point")
    assert usd_element_type("matrix4d") == ("double", 16, "matrix")
    assert usd_element_type("quatf") == ("float", 4, "quaternion")
    assert usd_element_type("token") == ("token", 1, "")
    assert values_equal([1.0, (2.0, 3.0)], [1.0, [2.0, 3.0 + 1e-13]])
    assert not values_equal(True, 1)


def test_geometry_helpers() -> None:
    from ifcusd.reverse.geometry import _cube, _lathe, triangulate
    from ifcusd.reverse.stage import quaternion, rigid

    triangles, faces = triangulate(np.array([3, 4, 5]), np.arange(12))
    assert len(triangles) == 1 + 2 + 3 and faces.tolist() == [0, 1, 1, 2, 2, 2]
    points, cube = _cube(2.0)
    assert len(points) == 8 and len(cube) == 12
    _points, cylinder = _lathe([(1.0, -1.0), (1.0, 1.0)])
    edges = Counter(tuple(sorted(e)) for t in cylinder.tolist() for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))
    assert set(edges.values()) == {2}  # closed, manifold
    matrix = np.diag([2.0, 2.0, 2.0, 1.0])
    matrix[:3, 3] = [1, 2, 3]
    placement, residual = rigid(matrix)
    assert np.allclose(placement[:3, :3], np.eye(3)) and np.allclose(placement @ residual, matrix)
    assert quaternion(np.eye(3)) == pytest.approx((1, 0, 0, 0))
