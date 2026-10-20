"""Target profiles, output formats and option switches."""

from __future__ import annotations

import pytest
from conftest import open_stage, prim_named, prims_by_class

from ifcusd import ConversionOptions, convert, validate_usd
from ifcusd.errors import InputError, OutputError


def test_unity_profile(ifc_files, tmp_path) -> None:
    from pxr import Gf, UsdGeom

    target = tmp_path / "unity.usdc"
    result = convert(ifc_files["IFC4"], target, ConversionOptions.for_profile("unity", meters_per_unit=0.01))
    stage = open_stage(target)
    assert UsdGeom.GetStageUpAxis(stage) == "Y"
    assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(0.01)
    root = UsdGeom.Xformable(stage.GetPrimAtPath("/World"))
    up = root.ComputeLocalToWorldTransform(0).TransformDir(Gf.Vec3d(0, 0, 1))
    assert tuple(up) == pytest.approx((0, 1, 0), abs=1e-9)  # IFC +Z becomes USD +Y
    assert not any(p.IsInstance() for p in stage.Traverse())
    assert result.report.counts["prototypes"] == 0
    assert not prim_named(stage, "Room 1").GetChild("Body")  # spaces skipped for Unity
    wall_body = prim_named(stage, "Wall 1").GetChild("Body")
    extent = UsdGeom.Mesh(wall_body).GetExtentAttr().Get()
    assert max(abs(v) for v in extent[1]) > 100  # centimetres
    assert validate_usd(target).ok


def test_omniverse_profile_adds_mdl_and_legacy_semantics(ifc_files, tmp_path) -> None:
    target = tmp_path / "omni.usd"
    convert(ifc_files["IFC4"], target, ConversionOptions.for_profile("omniverse"))
    stage = open_stage(target)
    material = stage.GetPrimAtPath("/World/Looks/Concrete_Grey")
    assert material.GetAttribute("outputs:mdl:surface").GetConnections()
    shader = stage.GetPrimAtPath("/World/Looks/Concrete_Grey/OmniPBR")
    assert shader.GetAttribute("info:mdl:sourceAsset:subIdentifier").Get() == "OmniPBR"
    assert shader.GetAttribute("inputs:enable_opacity").Get() is True
    wall = prim_named(stage, "Wall 1")
    assert wall.GetAttribute("semantic:Semantics:params:semanticData").Get() == "IfcWall"
    report = validate_usd(target)
    assert report.ok, [(i.code, i.message) for i in report.errors]


def test_usdz_package(ifc_files, tmp_path) -> None:
    target = tmp_path / "model.usdz"
    convert(ifc_files["IFC4"], target, ConversionOptions.for_profile("unity"))
    assert target.exists() and target.stat().st_size > 0
    assert prims_by_class(open_stage(target), "IfcWall")


def test_include_type_filter_keeps_spatial_structure(ifc_files, tmp_path) -> None:
    target = tmp_path / "walls.usda"
    result = convert(ifc_files["IFC4"], target, ConversionOptions(include_types=("IfcWall",)))
    stage = open_stage(target)
    assert len(prims_by_class(stage, "IfcWall")) == 2
    assert not prims_by_class(stage, "IfcDoor")
    assert prims_by_class(stage, "IfcBuildingStorey")
    assert result.report.counts["filteredProducts"] > 0
    assert result.report.coverage_totals()["filtered"] > 0
    assert result.report.missing_count == 0


def test_geometry_only_output(ifc_files, tmp_path) -> None:
    target = tmp_path / "geometry.usda"
    convert(ifc_files["IFC4"], target, ConversionOptions(include_metadata=False))
    stage = open_stage(target)
    assert not any(p.HasAttribute("ifc:globalId") for p in stage.Traverse())
    assert any(p.GetTypeName() == "Mesh" for p in stage.Traverse())
    assert not stage.GetPrimAtPath("/World/IfcData")


def test_metadata_sidecar(ifc_files, tmp_path) -> None:
    import json

    sidecar = tmp_path / "meta.json"
    convert(
        ifc_files["IFC4"],
        tmp_path / "m.usda",
        ConversionOptions(),
        metadata_json_path=sidecar,
        report_path=tmp_path / "r.json",
    )
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    assert document["format"] == "ifcusd-metadata/1"
    wall = next(o for o in document["objects"] if o["name"] == "Wall 1")
    assert wall["ifcClass"] == "IfcWall" and wall["path"].startswith("/World/")
    assert any(
        p["set"] == "Pset_WallCommon" and p["name"] == "FireRating" and p["value"] == "REI90"
        for p in wall["properties"]
    )
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["complete"] is True and report["coverageTotals"]["missing"] == 0


def test_schema_fallback_for_unsupported_identifiers(ifc_files, tmp_path) -> None:
    text = ifc_files["IFC4"].read_text(encoding="utf-8").replace("FILE_SCHEMA(('IFC4'))", "FILE_SCHEMA(('IFC4_ADD2'))")
    legacy = tmp_path / "legacy.ifc"
    legacy.write_text(text, encoding="utf-8")
    result = convert(legacy, tmp_path / "legacy.usda", ConversionOptions())
    assert result.report.declared_schema == "IFC4_ADD2"
    assert result.report.compatibility_schema == "IFC4"
    assert result.report.missing_count == 0
    stage = open_stage(tmp_path / "legacy.usda")
    assert stage.GetPrimAtPath("/World").GetAttribute("ifc:file:schemaIdentifier").Get() == "IFC4_ADD2"
    with pytest.raises(InputError):
        convert(legacy, tmp_path / "strict.usda", ConversionOptions(schema_fallback=False))


def test_ifczip_input(ifc_files, tmp_path) -> None:
    import zipfile

    archive = tmp_path / "model.ifczip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(ifc_files["IFC4"], "model.ifc")
    result = convert(archive, tmp_path / "zip.usda", ConversionOptions())
    assert result.report.input_format == "IFC-ZIP"
    assert result.report.missing_count == 0


def test_bad_paths_raise_clear_errors(ifc_files, tmp_path) -> None:
    with pytest.raises(OutputError):
        convert(ifc_files["IFC4"], tmp_path / "out.obj")
    with pytest.raises(InputError):
        convert(tmp_path / "missing.ifc", tmp_path / "out.usda")


def test_all_relationship_prims_mode(ifc_files, tmp_path) -> None:
    target = tmp_path / "graph.usda"
    result = convert(ifc_files["IFC4"], target, ConversionOptions(relationship_prims="all"))
    totals = result.report.coverage_totals()
    assert totals["native"] == 0 and totals["missing"] == 0
    stage = open_stage(target)
    aggregates = prims_by_class(stage, "IfcRelAggregates")
    assert aggregates and all(p.HasAttribute("ifc:globalId") for p in aggregates)
    defines = prims_by_class(stage, "IfcRelDefinesByProperties")
    assert defines and defines[0].GetAttribute("ifc:relatingPropertyDefinition").Get()
    # native mapping is still written as well
    assert prim_named(stage, "Wall 1").GetRelationship("ifc:isTypedBy").GetTargets()


def test_filters_are_accounted_as_filtered_not_missing(tmp_path) -> None:
    from conftest import SAMPLES

    result = convert(
        SAMPLES / "Architecture.ifc",
        tmp_path / "filtered.usda",
        ConversionOptions(include_types=("IfcWall",), exclude_types=("IfcSpace",)),
    )
    report = result.report
    assert report.complete and report.missing_count == 0
    assert report.coverage_totals()["filtered"] > 0
    assert not any(item["ifcClass"] == "IfcSpace" for item in report.products_without_geometry)
