from __future__ import annotations

from pathlib import Path

import pytest

from ifcusd.dependencies import MissingUsdDependencyError, import_usd
from ifcusd.geometry import GeometryMaterial, MeshGeometry
from ifcusd.usd_writer import UsdStageWriter


def test_usd_writer_authors_texture_shader_network(tmp_path: Path) -> None:
    try:
        usd = import_usd()
    except MissingUsdDependencyError:
        pytest.skip("OpenUSD Python bindings are not installed")

    output = tmp_path / "textured.usda"
    writer = UsdStageWriter(output, meters_per_unit=1.0)
    writer.add_mesh(
        "/IFC/TexturedWall",
        MeshGeometry(
            points=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            face_vertex_indices=[0, 1, 2],
            face_vertex_counts=[3],
            normals=[],
            texcoords=[(0, 0), (1, 0), (0, 1)],
            material_ids=[0],
            materials=[
                GeometryMaterial(
                    name="Brick",
                    diffuse=(1.0, 1.0, 1.0),
                    texture_paths=("textures/brick.png",),
                )
            ],
        ),
        {"ifc_class": "IfcWall"},
    )
    writer.save()

    stage = usd.Usd.Stage.Open(str(output))
    assert stage.GetPrimAtPath("/IFC/TexturedWall/Mesh").HasAttribute("primvars:st")
    texture = usd.UsdShade.Shader.Get(stage, "/IFC/Looks/Brick/DiffuseTexture")
    assert texture.GetInput("file").Get().path == "textures/brick.png"


def test_usd_writer_authors_material_face_subsets(tmp_path: Path) -> None:
    try:
        usd = import_usd()
    except MissingUsdDependencyError:
        pytest.skip("OpenUSD Python bindings are not installed")

    output = tmp_path / "subsets.usda"
    writer = UsdStageWriter(output, meters_per_unit=1.0)
    writer.add_mesh(
        "/IFC/MultiMaterial",
        MeshGeometry(
            points=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            face_vertex_indices=[0, 1, 2, 0, 2, 3],
            face_vertex_counts=[3, 3],
            normals=[],
            texcoords=[],
            material_ids=[0, 1],
            materials=[
                GeometryMaterial(name="Red", diffuse=(1.0, 0.0, 0.0)),
                GeometryMaterial(name="Blue", diffuse=(0.0, 0.0, 1.0)),
            ],
        ),
        {"ifc_class": "IfcWall"},
    )
    writer.save()

    stage = usd.Usd.Stage.Open(str(output))
    red_subset = stage.GetPrimAtPath("/IFC/MultiMaterial/Mesh/Red_0")
    blue_subset = stage.GetPrimAtPath("/IFC/MultiMaterial/Mesh/Blue_1")

    assert red_subset.IsValid()
    assert blue_subset.IsValid()
    assert list(red_subset.GetAttribute("indices").Get()) == [0]
    assert list(blue_subset.GetAttribute("indices").Get()) == [1]
    assert red_subset.GetRelationship("material:binding").GetTargets()[0] == usd.Sdf.Path(
        "/IFC/Looks/Red"
    )
    assert blue_subset.GetRelationship("material:binding").GetTargets()[0] == usd.Sdf.Path(
        "/IFC/Looks/Blue"
    )
