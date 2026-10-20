"""IFC5 (IFCX) composition and conversion."""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest
from conftest import open_stage

from ifcusd import ConversionOptions, convert, validate_usd
from ifcusd.ifcx.compose import Composer, IfcxCompositionError, find_roots, flatten, load_layer

HEADER = {
    "id": "test",
    "ifcxVersion": "ifcx_alpha",
    "dataVersion": "1.0.0",
    "author": "tests",
    "timestamp": "2025-01-01",
}
CUBE = {
    "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
    "faceVertexIndices": [
        0,
        2,
        1,
        0,
        3,
        2,
        4,
        5,
        6,
        4,
        6,
        7,
        0,
        1,
        5,
        0,
        5,
        4,
        1,
        2,
        6,
        1,
        6,
        5,
        2,
        3,
        7,
        2,
        7,
        6,
        3,
        0,
        4,
        3,
        4,
        7,
    ],
}


def _write(path, data, schemas=None):
    path.write_text(
        json.dumps({"header": HEADER, "imports": [], "schemas": schemas or {}, "data": data}), encoding="utf-8"
    )
    return path


def _base_layer(tmp_path):
    points = np.array([[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0]], dtype="<f4")
    data = [
        {"path": "root", "children": {"Project": "p"}},
        {
            "path": "p",
            "children": {"Storey": "s"},
            "attributes": {"bsi::ifc::class": {"code": "IfcProject", "uri": "https://example/IfcProject"}},
        },
        {
            "path": "s",
            "children": {"Wall": "w", "Window_A": "win_a", "Window_B": "win_b", "Scan": "scan"},
            "attributes": {"bsi::ifc::class": {"code": "IfcBuildingStorey"}},
        },
        {
            "path": "w",
            "children": {"Body": "w_body"},
            "attributes": {
                "bsi::ifc::class": {"code": "IfcWall"},
                "bsi::ifc::prop::IsExternal": True,
                "bsi::ifc::presentation::diffuseColor": [0.5, 0.5, 0.5],
                "usd::xformop": {"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [2, 0, 0, 1]]},
            },
        },
        {"path": "w_body", "attributes": {"usd::usdgeom::mesh": CUBE}},
        {
            "path": "window_type",
            "children": {"Frame": "frame"},
            "attributes": {"bsi::ifc::class": {"code": "IfcWindow"}, "bsi::ifc::prop::TypeName": "WT01"},
        },
        {
            "path": "frame",
            "attributes": {
                "usd::usdgeom::mesh": CUBE,
                "bsi::ifc::presentation::diffuseColor": [0.8, 0.7, 0.6],
                "bsi::ifc::presentation::opacity": 0.5,
            },
        },
        {
            "path": "win_a",
            "inherits": {"type": "window_type"},
            "attributes": {"bsi::ifc::spaceBoundary": {"relatedelement": {"ref": "w"}}},
        },
        {
            "path": "win_b",
            "inherits": {"type": "window_type"},
            "attributes": {"usd::xformop": {"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [5, 0, 0, 1]]}},
        },
        {"path": "scan", "attributes": {"points::base64": {"positions": base64.b64encode(points.tobytes()).decode()}}},
    ]
    return _write(tmp_path / "base.ifcx", data)


def test_composition_follows_ifcx_rules(tmp_path) -> None:
    layer = load_layer(_base_layer(tmp_path))
    nodes = flatten([layer])
    assert find_roots(nodes) == ["root"]
    root = Composer(nodes).compose("root")
    storey = root.children["Project"].children["Storey"]
    window_a, window_b = storey.children["Window_A"], storey.children["Window_B"]
    assert window_a.attributes["bsi::ifc::prop::TypeName"] == "WT01"  # inherited from the type
    assert "Frame" in window_a.children and "Frame" in window_b.children
    assert window_a.children["Frame"] is not window_b.children["Frame"]  # composed copies, not shared


def test_cycles_are_reported(tmp_path) -> None:
    path = _write(
        tmp_path / "cycle.ifcx", [{"path": "a", "children": {"b": "b"}}, {"path": "b", "children": {"a": "a"}}]
    )
    with pytest.raises(IfcxCompositionError):
        find_roots(flatten([load_layer(path)]))


def test_ifcx_conversion_with_override_layer(tmp_path) -> None:
    base = _base_layer(tmp_path)
    override = _write(
        tmp_path / "fire.ifcx",
        [{"path": "w", "attributes": {"bsi::ifc::prop::FireRating": "R60"}}],
        schemas={
            "bsi::ifc::prop::FireRating": {
                "value": {"dataType": "Enum", "enumRestrictions": {"options": ["R30", "R60"]}}
            }
        },
    )
    target = tmp_path / "ifc5.usda"
    sidecar = tmp_path / "ifc5.json"
    result = convert(base, target, ConversionOptions(), layers=[override], metadata_json_path=sidecar)
    assert result.report.ifc_schema == "IFC5"
    assert result.report.complete and result.report.missing_count == 0
    records = json.loads(sidecar.read_text(encoding="utf-8"))["objects"]
    wall_record = next(r for r in records if r["ifcClass"] == "IfcWall")
    assert {"set": "bsi::ifc::prop", "name": "FireRating"}.items() <= next(
        p for p in wall_record["properties"] if p["name"] == "FireRating"
    ).items()
    stage = open_stage(target)
    from pxr import Usd, UsdGeom

    wall = next(p for p in stage.Traverse() if p.GetAttribute("ifc:class").Get() == "IfcWall")
    assert wall.GetAttribute("bsi:ifc:prop:IsExternal").Get() is True
    fire = wall.GetAttribute("bsi:ifc:prop:FireRating")
    assert fire.Get() == "R60" and fire.GetTypeName() == "token"
    assert list(fire.GetMetadata("allowedTokens")) == ["R30", "R60"]
    assert fire.GetCustomDataByKey("ifcxKey") == "bsi::ifc::prop::FireRating"
    body = wall.GetChild("Body")
    assert body.IsA(UsdGeom.Mesh)
    world = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    assert world.ExtractTranslation()[0] == pytest.approx(2.0)

    windows = [p for p in stage.Traverse() if p.GetAttribute("ifc:class").Get() == "IfcWindow"]
    assert len(windows) == 2
    boundary = windows[0].GetRelationship("bsi:ifc:spaceBoundary:relatedelement")
    assert boundary.GetTargets() == [wall.GetPath()]
    scan = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Points))
    assert len(UsdGeom.Points(scan).GetPointsAttr().Get()) == 3
    assert validate_usd(target).ok
