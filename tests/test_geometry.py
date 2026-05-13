from __future__ import annotations

from ifcusd.geometry import mesh_from_shape


class FakeTexture:
    URLReference = "textures/wall.png"


class FakeTexturedSurfaceStyle:
    Textures = (FakeTexture(),)

    def is_a(self, value: str | None = None):
        if value is None:
            return "IfcSurfaceStyleWithTextures"
        return value == "IfcSurfaceStyleWithTextures"


class FakeStyle:
    Styles = (FakeTexturedSurfaceStyle(),)


class FakeModel:
    def by_id(self, step_id: int):
        assert step_id == 77
        return FakeStyle()


class FakeMaterial:
    name = "Brick"
    diffuse = (0.2, 0.3, 0.4)
    specular = (0.05, 0.06, 0.07)
    transparency = 0.25
    specularity = 0.8

    def instance_id(self) -> int:
        return 77


class FakeGeometry:
    verts = (0, 0, 0, 1, 0, 0, 0, 1, 0)
    faces = (0, 1, 2)
    normals = ()
    uvs = (0, 0, 1, 0, 0, 1)
    material_ids = (0,)
    materials = (FakeMaterial(),)


class FakeShape:
    geometry = FakeGeometry()


def test_mesh_from_shape_extracts_material_color_opacity_and_texture_paths() -> None:
    mesh = mesh_from_shape(FakeShape(), FakeModel())

    assert mesh.points == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    assert mesh.face_vertex_indices == [0, 1, 2]
    assert mesh.texcoords == [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]

    material = mesh.materials[0]
    assert material.name == "Brick"
    assert material.diffuse == (0.2, 0.3, 0.4)
    assert material.specular == (0.05, 0.06, 0.07)
    assert material.opacity == 0.75
    assert material.specularity == 0.8
    assert material.texture_paths == ("textures/wall.png",)
