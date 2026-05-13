from __future__ import annotations

from pathlib import Path
from typing import Any

from ifcusd.dependencies import import_usd
from ifcusd.geometry import GeometryMaterial, MeshGeometry
from ifcusd.metadata import metadata_json
from ifcusd.naming import safe_attr_name, safe_identifier


class UsdStageWriter:
    def __init__(self, output_path: Path, *, meters_per_unit: float, up_axis: str = "Z") -> None:
        self.output_path = output_path
        self.usd = import_usd()
        self.stage = self.usd.Usd.Stage.CreateNew(str(output_path))
        self.material_paths: dict[tuple[Any, ...], str] = {}

        self.usd.UsdGeom.SetStageMetersPerUnit(self.stage, meters_per_unit)
        self.usd.UsdGeom.SetStageUpAxis(self.stage, self._up_axis_token(up_axis))
        root = self.usd.UsdGeom.Xform.Define(self.stage, "/IFC")
        self.stage.SetDefaultPrim(root.GetPrim())
        self._ensure_xform("/IFC/Looks")

    def set_stage_metadata(self, metadata: dict[str, Any]) -> None:
        root = self.stage.GetPrimAtPath("/IFC")
        self.write_metadata(root, metadata)

    def ensure_entity_xform(self, path: str, metadata: dict[str, Any] | None = None) -> None:
        prim = self._ensure_xform(path)
        if metadata:
            self.write_metadata(prim, metadata)

    def add_mesh(self, entity_path: str, mesh_geometry: MeshGeometry, metadata: dict[str, Any]) -> None:
        self.ensure_entity_xform(entity_path, metadata)
        mesh_path = f"{entity_path}/Mesh"
        mesh = self.usd.UsdGeom.Mesh.Define(self.stage, mesh_path)

        mesh.CreateSubdivisionSchemeAttr().Set(self.usd.UsdGeom.Tokens.none)
        mesh.CreatePointsAttr(self._vec3f_array(mesh_geometry.points))
        mesh.CreateFaceVertexCountsAttr(self.usd.Vt.IntArray(mesh_geometry.face_vertex_counts))
        mesh.CreateFaceVertexIndicesAttr(self.usd.Vt.IntArray(mesh_geometry.face_vertex_indices))

        if mesh_geometry.normals:
            mesh.CreateNormalsAttr(self._vec3f_array(mesh_geometry.normals))
            interpolation = self._normal_interpolation(mesh_geometry)
            if interpolation is not None:
                mesh.SetNormalsInterpolation(interpolation)

        if mesh_geometry.texcoords:
            self._write_texcoords(mesh, mesh_geometry)

        self._bind_materials(mesh, mesh_path, mesh_geometry)

    def write_metadata(self, prim: Any, metadata: dict[str, Any]) -> None:
        self._set_attr(prim, "ifc:metadata", metadata_json(metadata))
        for key, value in metadata.items():
            if key == "property_sets" and isinstance(value, dict):
                self._write_nested_metadata(prim, "ifc:pset", value)
            elif key == "quantities" and isinstance(value, dict):
                self._write_nested_metadata(prim, "ifc:qto", value)
            elif _is_scalar(value):
                self._set_attr(prim, f"ifc:{safe_attr_name(key)}", value)
            elif key in {"type", "material", "relationships", "styles"}:
                self._set_attr(prim, f"ifc:{safe_attr_name(key)}", metadata_json({key: value}))

    def save(self) -> None:
        self.stage.GetRootLayer().Save()

    def _ensure_xform(self, path: str) -> Any:
        prim = self.stage.GetPrimAtPath(path)
        if prim and prim.IsValid():
            return prim
        return self.usd.UsdGeom.Xform.Define(self.stage, path).GetPrim()

    def _write_nested_metadata(self, prim: Any, namespace: str, values: dict[str, Any]) -> None:
        for group_name, group_values in values.items():
            if not isinstance(group_values, dict):
                self._set_attr(prim, f"{namespace}:{safe_attr_name(group_name)}", _stringify(group_values))
                continue
            for prop_name, prop_value in group_values.items():
                if prop_name == "id":
                    continue
                attr_name = (
                    f"{namespace}:{safe_attr_name(group_name)}:{safe_attr_name(prop_name)}"
                )
                self._set_attr(prim, attr_name, prop_value if _is_scalar(prop_value) else _stringify(prop_value))

    def _set_attr(self, prim: Any, attr_name: str, value: Any) -> None:
        sdf_type = self._sdf_type_for(value)
        prim.CreateAttribute(attr_name, sdf_type).Set(value)

    def _sdf_type_for(self, value: Any) -> Any:
        if isinstance(value, bool):
            return self.usd.Sdf.ValueTypeNames.Bool
        if isinstance(value, int):
            return self.usd.Sdf.ValueTypeNames.Int64
        if isinstance(value, float):
            return self.usd.Sdf.ValueTypeNames.Double
        return self.usd.Sdf.ValueTypeNames.String

    def _vec3f_array(self, values: list[tuple[float, float, float]]) -> Any:
        return self.usd.Vt.Vec3fArray([self.usd.Gf.Vec3f(*value) for value in values])

    def _vec2f_array(self, values: list[tuple[float, float]]) -> Any:
        return self.usd.Vt.Vec2fArray([self.usd.Gf.Vec2f(*value) for value in values])

    def _normal_interpolation(self, mesh_geometry: MeshGeometry) -> Any | None:
        normal_count = len(mesh_geometry.normals)
        if normal_count == len(mesh_geometry.face_vertex_indices):
            return self.usd.UsdGeom.Tokens.faceVarying
        if normal_count == len(mesh_geometry.points):
            return self.usd.UsdGeom.Tokens.vertex
        return None

    def _write_texcoords(self, mesh: Any, mesh_geometry: MeshGeometry) -> None:
        interpolation = self.usd.UsdGeom.Tokens.faceVarying
        texcoords = mesh_geometry.texcoords
        if len(texcoords) == len(mesh_geometry.points):
            interpolation = self.usd.UsdGeom.Tokens.vertex
        elif len(texcoords) != len(mesh_geometry.face_vertex_indices):
            return

        primvars = self.usd.UsdGeom.PrimvarsAPI(mesh.GetPrim())
        st = primvars.CreatePrimvar(
            "st",
            self.usd.Sdf.ValueTypeNames.TexCoord2fArray,
            interpolation,
        )
        st.Set(self._vec2f_array(texcoords))

    def _bind_materials(self, mesh: Any, mesh_path: str, mesh_geometry: MeshGeometry) -> None:
        if not mesh_geometry.materials:
            return

        if not mesh_geometry.material_ids or len(set(mesh_geometry.material_ids)) <= 1:
            material_index = mesh_geometry.material_ids[0] if mesh_geometry.material_ids else 0
            if material_index >= len(mesh_geometry.materials):
                return
            material = self._usd_material(mesh_geometry.materials[material_index])
            self.usd.UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)
            return

        binding_api = self.usd.UsdShade.MaterialBindingAPI(mesh.GetPrim())
        binding_api.SetMaterialBindSubsetsFamilyType(self.usd.UsdGeom.Tokens.nonOverlapping)
        face_indices_by_material: dict[int, list[int]] = {}
        for face_index, material_index in enumerate(mesh_geometry.material_ids[: mesh_geometry.face_count]):
            face_indices_by_material.setdefault(material_index, []).append(face_index)

        for material_index, face_indices in face_indices_by_material.items():
            if material_index >= len(mesh_geometry.materials):
                continue
            subset_name = safe_identifier(mesh_geometry.materials[material_index].name, "material")
            subset = binding_api.CreateMaterialBindSubset(
                f"{subset_name}_{material_index}",
                self.usd.Vt.IntArray(face_indices),
                self.usd.UsdGeom.Tokens.face,
            )
            material = self._usd_material(mesh_geometry.materials[material_index])
            self.usd.UsdShade.MaterialBindingAPI(subset.GetPrim()).Bind(material)

    def _usd_material(self, material: GeometryMaterial) -> Any:
        key = (
            material.name,
            material.diffuse,
            material.surface,
            material.specular,
            material.opacity,
            material.specularity,
            material.texture_paths,
        )
        existing_path = self.material_paths.get(key)
        if existing_path is not None:
            return self.usd.UsdShade.Material.Get(self.stage, existing_path)

        material_path = self._unique_material_path(material.name)
        usd_material = self.usd.UsdShade.Material.Define(self.stage, material_path)
        shader = self.usd.UsdShade.Shader.Define(self.stage, f"{material_path}/PreviewSurface")
        shader.CreateIdAttr("UsdPreviewSurface")
        diffuse = material.diffuse or (0.8, 0.8, 0.8)
        diffuse_input = shader.CreateInput("diffuseColor", self.usd.Sdf.ValueTypeNames.Color3f)
        diffuse_input.Set(self.usd.Gf.Vec3f(*diffuse))
        if material.texture_paths:
            self._connect_diffuse_texture(material_path, diffuse_input, material.texture_paths[0])
        shader.CreateInput("specularColor", self.usd.Sdf.ValueTypeNames.Color3f).Set(
            self.usd.Gf.Vec3f(*(material.specular or (0.0, 0.0, 0.0)))
        )
        shader.CreateInput("useSpecularWorkflow", self.usd.Sdf.ValueTypeNames.Int).Set(1)
        if material.specularity is not None:
            shader.CreateInput("roughness", self.usd.Sdf.ValueTypeNames.Float).Set(
                max(0.0, min(1.0, 1.0 - material.specularity))
            )
        shader.CreateInput("opacity", self.usd.Sdf.ValueTypeNames.Float).Set(
            1.0 if material.opacity is None else material.opacity
        )
        if material.opacity is not None and material.opacity < 1.0:
            shader.CreateInput("opacityMode", self.usd.Sdf.ValueTypeNames.Token).Set("transparent")
        output = usd_material.CreateSurfaceOutput()
        try:
            output.ConnectToSource(shader.ConnectableAPI(), "surface")
        except TypeError:
            output.ConnectToSource(shader, "surface")

        self.material_paths[key] = material_path
        return usd_material

    def _connect_diffuse_texture(self, material_path: str, diffuse_input: Any, texture_path: str) -> None:
        primvar = self.usd.UsdShade.Shader.Define(self.stage, f"{material_path}/Primvar_st")
        primvar.CreateIdAttr("UsdPrimvarReader_float2")
        primvar.CreateInput("varname", self.usd.Sdf.ValueTypeNames.Token).Set("st")

        texture = self.usd.UsdShade.Shader.Define(self.stage, f"{material_path}/DiffuseTexture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", self.usd.Sdf.ValueTypeNames.Asset).Set(
            self.usd.Sdf.AssetPath(texture_path)
        )
        texture.CreateInput("sourceColorSpace", self.usd.Sdf.ValueTypeNames.Token).Set("sRGB")
        st_input = texture.CreateInput("st", self.usd.Sdf.ValueTypeNames.TexCoord2f)
        try:
            st_input.ConnectToSource(primvar.ConnectableAPI(), "result")
            diffuse_input.ConnectToSource(texture.ConnectableAPI(), "rgb")
        except TypeError:
            st_input.ConnectToSource(primvar, "result")
            diffuse_input.ConnectToSource(texture, "rgb")

    def _unique_material_path(self, material_name: str) -> str:
        base = f"/IFC/Looks/{safe_identifier(material_name, 'Material')}"
        path = base
        index = 2
        while self.stage.GetPrimAtPath(path).IsValid():
            path = f"{base}_{index}"
            index += 1
        return path

    def _up_axis_token(self, up_axis: str) -> Any:
        return self.usd.UsdGeom.Tokens.y if up_axis.upper() == "Y" else self.usd.UsdGeom.Tokens.z


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return metadata_json({"value": value})
