"""UsdShade material networks.

Every material gets a ``UsdPreviewSurface`` network on the universal render
context, which is what Unity, Omniverse, usdview, Blender and Apple tools
read. The Omniverse profile adds an ``OmniPBR`` MDL network on the ``mdl``
render context so RTX uses its native material model.
"""

from __future__ import annotations

from typing import Any

from ifcusd.ifc.styles import MaterialSpec
from ifcusd.schema import ENTITY_API
from ifcusd.usd.sdf import Gf, LayerWriter, set_display_name


def write_material(writer: LayerWriter, path: str, spec: MaterialSpec, *, mdl: bool) -> None:
    material = writer.prim(path, "Material", api_schemas=[ENTITY_API] if spec.source_class else ())
    if spec.display_name:
        set_display_name(material, spec.display_name)

    if spec.source_class:
        writer.attribute(material, "ifc:class", "token", spec.source_class, display_group="IFC", display_name="Class")
    if spec.step_id:
        writer.attribute(material, "ifc:stepId", "int", int(spec.step_id), display_group="IFC")
    if spec.display_name:
        writer.attribute(material, "ifc:name", "string", spec.display_name, display_group="IFC", display_name="Name")
    for key, value in sorted(spec.ifc_attributes.items()):
        if isinstance(value, tuple) and len(value) == 3:
            writer.attribute(
                material, f"ifc:style:{key}", "color3f", Gf.Vec3f(*value), custom=True, display_group="IFC Style"
            )
        elif isinstance(value, float):
            writer.attribute(material, f"ifc:style:{key}", "double", value, custom=True, display_group="IFC Style")
        else:
            writer.attribute(material, f"ifc:style:{key}", "string", str(value), custom=True, display_group="IFC Style")

    surface = writer.prim(f"{path}/PreviewSurface", "Shader")
    writer.attribute(surface, "info:id", "token", "UsdPreviewSurface")
    diffuse_input = writer.attribute(
        surface, "inputs:diffuseColor", "color3f", Gf.Vec3f(*spec.base_color), uniform=False
    )
    writer.attribute(surface, "inputs:opacity", "float", float(spec.opacity), uniform=False)
    writer.attribute(surface, "inputs:metallic", "float", float(spec.metallic), uniform=False)
    writer.attribute(surface, "inputs:roughness", "float", float(spec.roughness), uniform=False)
    if spec.ior is not None:
        writer.attribute(surface, "inputs:ior", "float", float(spec.ior), uniform=False)
    writer.attribute(surface, "inputs:useSpecularWorkflow", "int", 0, uniform=False)
    writer.attribute(surface, "outputs:surface", "token", None, uniform=False)
    writer.connect(material, "outputs:surface", "token", f"{path}/PreviewSurface.outputs:surface")

    if spec.texture:
        reader = writer.prim(f"{path}/TexCoordReader", "Shader")
        writer.attribute(reader, "info:id", "token", "UsdPrimvarReader_float2")
        writer.attribute(reader, "inputs:varname", "string", "st", uniform=False)
        writer.attribute(reader, "outputs:result", "float2", None, uniform=False)
        texture = writer.prim(f"{path}/DiffuseTexture", "Shader")
        writer.attribute(texture, "info:id", "token", "UsdUVTexture")
        writer.attribute(texture, "inputs:file", "asset", _asset(spec.texture), uniform=False)
        writer.attribute(texture, "inputs:sourceColorSpace", "token", "sRGB", uniform=False)
        writer.attribute(texture, "inputs:wrapS", "token", "repeat", uniform=False)
        writer.attribute(texture, "inputs:wrapT", "token", "repeat", uniform=False)
        writer.connect(texture, "inputs:st", "float2", f"{path}/TexCoordReader.outputs:result")
        writer.attribute(texture, "outputs:rgb", "float3", None, uniform=False)
        diffuse_input.connectionPathList.explicitItems = [_sdf_path(f"{path}/DiffuseTexture.outputs:rgb")]

    if mdl:
        _write_omnipbr(writer, path, spec)


def _write_omnipbr(writer: LayerWriter, path: str, spec: MaterialSpec) -> None:
    shader = writer.prim(f"{path}/OmniPBR", "Shader")
    writer.attribute(shader, "info:implementationSource", "token", "sourceAsset")
    writer.attribute(shader, "info:mdl:sourceAsset", "asset", _asset("OmniPBR.mdl"))
    writer.attribute(shader, "info:mdl:sourceAsset:subIdentifier", "token", "OmniPBR")
    writer.attribute(shader, "inputs:diffuse_color_constant", "color3f", Gf.Vec3f(*spec.base_color), uniform=False)
    writer.attribute(shader, "inputs:reflection_roughness_constant", "float", float(spec.roughness), uniform=False)
    writer.attribute(shader, "inputs:metallic_constant", "float", float(spec.metallic), uniform=False)
    if spec.opacity < 1.0:
        writer.attribute(shader, "inputs:enable_opacity", "bool", True, uniform=False)
        writer.attribute(shader, "inputs:opacity_constant", "float", float(spec.opacity), uniform=False)
    if spec.texture:
        writer.attribute(shader, "inputs:diffuse_texture", "asset", _asset(spec.texture), uniform=False)
    writer.attribute(shader, "outputs:out", "token", None, uniform=False)
    material = writer.layer.GetPrimAtPath(path)
    for output in ("outputs:mdl:surface", "outputs:mdl:displacement", "outputs:mdl:volume"):
        writer.connect(material, output, "token", f"{path}/OmniPBR.outputs:out")


def _asset(path: str) -> Any:
    from ifcusd.usd.sdf import Sdf

    return Sdf.AssetPath(path)


def _sdf_path(path: str) -> Any:
    from ifcusd.usd.sdf import Sdf

    return Sdf.Path(path)
