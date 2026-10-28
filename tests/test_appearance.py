"""Colours and materials survive USD -> IFC: IfcUSD stages and materials authored in Omniverse or Isaac Sim."""

from __future__ import annotations

from pathlib import Path

import pytest

from ifcusd import ConversionOptions, convert, convert_usd_to_ifc

SAMPLES = Path(__file__).resolve().parent.parent / "ifc"


def _colours(model) -> dict[str, list[tuple[float, float, float]]]:
    """The colours a viewer shows for each product, as IfcOpenShell resolves them."""
    import ifcopenshell.geom

    settings = ifcopenshell.geom.settings()
    result = {}
    for product in model.by_type("IfcProduct"):
        if not product.Representation or product.is_a("IfcOpeningElement"):
            continue
        shape = ifcopenshell.geom.create_shape(settings, product)
        colours = set()
        for material in shape.geometry.materials:
            diffuse = material.diffuse
            rgb = (diffuse.r(), diffuse.g(), diffuse.b()) if hasattr(diffuse, "r") else tuple(diffuse)[:3]
            colours.add(tuple(round(float(c), 2) for c in rgb))
        result[product.Name or product.GlobalId] = sorted(colours)
    return result


@pytest.mark.parametrize("profile", ["generic", "omniverse"])
def test_rebuilt_ifc_keeps_every_colour(profile: str, tmp_path: Path) -> None:
    import ifcopenshell

    source = SAMPLES / "Architecture.ifc"
    usd = tmp_path / "model.usda"
    convert(source, usd, ConversionOptions.for_profile(profile, threads=1))
    rebuilt = convert_usd_to_ifc(usd, tmp_path / "rebuilt.ifc")
    expected = _colours(ifcopenshell.open(str(source)))
    assert len({c for colours in expected.values() for c in colours}) > 3  # a model with real colours
    assert _colours(rebuilt.model) == expected
    assert rebuilt.report.counts["styles"] >= 5


def _material(stage, path: str):
    from pxr import UsdShade

    return UsdShade.Material.Define(stage, path)


def _bind(prim, material) -> None:
    from pxr import UsdShade

    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


def _quad_mesh(stage, path: str, offset: float, faces: int = 1):
    from pxr import Gf, UsdGeom

    mesh = UsdGeom.Mesh.Define(stage, path)
    points, counts, indices = [], [], []
    for face in range(faces):
        x = offset + face
        base = len(points)
        points += [Gf.Vec3f(x, 0, 0), Gf.Vec3f(x + 1, 0, 0), Gf.Vec3f(x + 1, 1, 0), Gf.Vec3f(x, 1, 0)]
        counts.append(4)
        indices += [base, base + 1, base + 2, base + 3]
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    return mesh


@pytest.fixture(scope="module")
def isaac_stage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Props with the materials Omniverse and Isaac Sim author, none of them written by IfcUSD."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

    path = tmp_path_factory.mktemp("appearance") / "scene.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())

    # OmniPBR (MDL only) whose colour is promoted to the material's interface
    wood = _material(stage, "/World/Looks/Wood")
    wood_input = wood.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f)
    wood_input.Set(Gf.Vec3f(0.6, 0.4, 0.2))
    shader = UsdShade.Shader.Define(stage, "/World/Looks/Wood/Shader")
    shader.SetSourceAsset("OmniPBR.mdl", "mdl")
    shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
    shader.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).ConnectToSource(wood_input)
    wood.CreateSurfaceOutput("mdl").ConnectToSource(shader.ConnectableAPI(), "out")
    table = UsdGeom.Xform.Define(stage, "/World/Table")
    top = UsdGeom.Cube.Define(stage, "/World/Table/Top")
    _bind(top.GetPrim(), wood)
    del table

    # MaterialX standard surface
    plastic = _material(stage, "/World/Looks/Plastic")
    mtlx = UsdShade.Shader.Define(stage, "/World/Looks/Plastic/Surface")
    mtlx.CreateIdAttr("ND_standard_surface_surfaceshader")
    mtlx.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.1, 0.2, 0.9))
    plastic.CreateSurfaceOutput("mtlx").ConnectToSource(mtlx.ConnectableAPI(), "out")
    _bind(_quad_mesh(stage, "/World/Bin", 3).GetPrim(), plastic)

    # OmniPBR straight from the Create menu: no colour authored, renders 0.2 grey
    default = _material(stage, "/World/Looks/OmniPBR")
    omnipbr = UsdShade.Shader.Define(stage, "/World/Looks/OmniPBR/Shader")
    omnipbr.SetSourceAsset("OmniPBR.mdl", "mdl")
    omnipbr.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
    default.CreateSurfaceOutput("mdl").ConnectToSource(omnipbr.ConnectableAPI(), "out")
    _bind(_quad_mesh(stage, "/World/Crate", 6).GetPrim(), default)

    # Textured UsdPreviewSurface: the mesh's displayColor stands in for the texture
    poster = _material(stage, "/World/Looks/Poster")
    preview = UsdShade.Shader.Define(stage, "/World/Looks/Poster/Preview")
    preview.CreateIdAttr("UsdPreviewSurface")
    texture = UsdShade.Shader.Define(stage, "/World/Looks/Poster/Texture")
    texture.CreateIdAttr("UsdUVTexture")
    texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("poster.png")
    preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), "rgb")
    poster.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(), "surface")
    panel = _quad_mesh(stage, "/World/Panel", 9)
    panel.CreateDisplayColorPrimvar().Set([Gf.Vec3f(0.9, 0.1, 0.1)])
    _bind(panel.GetPrim(), poster)

    # No material: a colour per face
    tiles = _quad_mesh(stage, "/World/Tiles", 12, faces=2)
    tiles.CreateDisplayColorPrimvar(UsdGeom.Tokens.uniform).Set([Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 0, 1)])

    # Two materials on one mesh (GeomSubsets): a constituent set in IFC
    duo = _quad_mesh(stage, "/World/Duo", 15, faces=2)
    for index, (name, material) in enumerate((("Left", wood), ("Right", plastic))):
        subset = UsdGeom.Subset.CreateGeomSubset(duo, name, UsdGeom.Tokens.face, [index], "materialBind")
        _bind(subset.GetPrim(), material)
    stage.Save()
    return path


@pytest.mark.parametrize("schema", ["IFC4", "IFC2X3"])
def test_omniverse_and_isaac_materials_become_ifc_colours(isaac_stage: Path, tmp_path: Path, schema: str) -> None:
    from ifcusd import IfcExportOptions

    result = convert_usd_to_ifc(isaac_stage, tmp_path / "scene.ifc", IfcExportOptions(schema=schema))
    colours = _colours(result.model)
    assert colours["Table"] == [(0.6, 0.4, 0.2)]  # MDL, through the material's interface
    assert colours["Bin"] == [(0.1, 0.2, 0.9)]  # MaterialX
    assert colours["Crate"] == [(0.2, 0.2, 0.2)]  # OmniPBR's own default
    assert colours["Panel"] == [(0.9, 0.1, 0.1)]  # textured: the mesh's displayColor
    assert colours["Tiles"] == [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0)]  # per-face displayColor
    assert colours["Duo"] == [(0.1, 0.2, 0.9), (0.6, 0.4, 0.2)]
    assert result.report.counts["materials"] == 4  # Wood, Plastic, OmniPBR, Poster


@pytest.mark.parametrize("schema", ["IFC4", "IFC2X3"])
def test_new_elements_are_made_of_their_usd_materials(isaac_stage: Path, tmp_path: Path, schema: str) -> None:
    import ifcopenshell.util.element as element

    from ifcusd import IfcExportOptions

    model = convert_usd_to_ifc(isaac_stage, tmp_path / "scene.ifc", IfcExportOptions(schema=schema)).model
    products = {p.Name: p for p in model.by_type("IfcProduct")}
    wood = element.get_material(products["Table"])
    assert wood.is_a("IfcMaterial") and wood.Name == "Wood"
    styles = [
        s
        for representation in wood.HasRepresentation
        for item in representation.Representations[0].Items
        for s in item.Styles
    ]
    assert styles  # the material carries its appearance
    duo = element.get_material(products["Duo"])
    if schema == "IFC2X3":
        assert duo.is_a("IfcMaterialList") and {m.Name for m in duo.Materials} == {"Wood", "Plastic"}
    else:
        assert duo.is_a("IfcMaterialConstituentSet")
        assert {c.Material.Name for c in duo.MaterialConstituents} == {"Wood", "Plastic"}
    assert element.get_material(products["Tiles"]) is None  # colours alone are no material
    assert len([m for m in model.by_type("IfcMaterial") if m.Name == "Wood"]) == 1  # shared, not repeated
