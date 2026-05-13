from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import ifcopenshell.geom

from ifcusd.options import ConversionOptions


@dataclass(frozen=True)
class GeometryMaterial:
    name: str
    diffuse: tuple[float, float, float] | None = None
    surface: tuple[float, float, float] | None = None
    specular: tuple[float, float, float] | None = None
    opacity: float | None = None
    specularity: float | None = None
    style_id: int | None = None
    texture_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class MeshGeometry:
    points: list[tuple[float, float, float]]
    face_vertex_indices: list[int]
    face_vertex_counts: list[int]
    normals: list[tuple[float, float, float]]
    texcoords: list[tuple[float, float]]
    material_ids: list[int]
    materials: list[GeometryMaterial]

    @property
    def face_count(self) -> int:
        return len(self.face_vertex_counts)


def iter_mesh_geometries(model: Any, options: ConversionOptions) -> Iterable[tuple[Any, MeshGeometry]]:
    settings = make_geometry_settings(options)
    include = list(options.include_types) or None
    exclude = list(options.exclude_types) or None
    iterator = ifcopenshell.geom.iterator(
        settings,
        model,
        max(1, options.threads),
        include=include,
        exclude=exclude,
    )

    if not iterator.initialize():
        return

    while True:
        shape = iterator.get()
        try:
            entity = model.by_id(shape.id)
            mesh = mesh_from_shape(shape, model)
            if mesh.points and mesh.face_vertex_indices:
                yield entity, mesh
        except Exception:
            if not options.ignore_geometry_errors:
                raise

        if not iterator.next():
            break


def make_geometry_settings(options: ConversionOptions) -> Any:
    settings = ifcopenshell.geom.settings()
    _set_setting(settings, "USE_WORLD_COORDS", options.use_world_coords)
    _set_setting(settings, "APPLY_DEFAULT_MATERIALS", True)
    _set_setting(settings, "GENERATE_UVS", True)
    return settings


def mesh_from_shape(shape: Any, model: Any | None = None) -> MeshGeometry:
    geometry = shape.geometry
    points = _triples(getattr(geometry, "verts", ()) or ())
    indices = [int(value) for value in (getattr(geometry, "faces", ()) or ())]
    normals = _triples(getattr(geometry, "normals", ()) or ())
    texcoords = _pairs(getattr(geometry, "uvs", ()) or ())
    face_vertex_counts = [3] * (len(indices) // 3)
    material_ids = [int(value) for value in (getattr(geometry, "material_ids", ()) or ())]
    materials = [
        _material_from_ifcopenshell(item, model)
        for item in getattr(geometry, "materials", ()) or ()
    ]

    return MeshGeometry(
        points=points,
        face_vertex_indices=indices,
        face_vertex_counts=face_vertex_counts,
        normals=normals,
        texcoords=texcoords,
        material_ids=material_ids,
        materials=materials,
    )


def _set_setting(settings: Any, name: str, value: Any) -> None:
    try:
        settings.set(getattr(settings, name), value)
    except Exception:
        setting_name = name.lower().replace("_", "-")
        try:
            settings.set(setting_name, value)
        except Exception:
            return


def _triples(values: Any) -> list[tuple[float, float, float]]:
    raw_values = list(values)
    return [
        (float(raw_values[index]), float(raw_values[index + 1]), float(raw_values[index + 2]))
        for index in range(0, len(raw_values) - 2, 3)
    ]


def _pairs(values: Any) -> list[tuple[float, float]]:
    raw_values = list(values)
    return [
        (float(raw_values[index]), float(raw_values[index + 1]))
        for index in range(0, len(raw_values) - 1, 2)
    ]


def _material_from_ifcopenshell(material: Any, model: Any | None = None) -> GeometryMaterial:
    name = str(_first_attr(material, "name", "Name") or "IfcMaterial")
    diffuse = _color_tuple(_first_attr(material, "diffuse", "diffuse_color", "DiffuseColour"))
    surface = _color_tuple(_first_attr(material, "surface", "surface_color", "SurfaceColour"))
    specular = _color_tuple(_first_attr(material, "specular", "specular_color", "SpecularColour"))
    transparency = _float_or_none(_first_attr(material, "transparency", "Transparency"))
    opacity = None if transparency is None else max(0.0, min(1.0, 1.0 - transparency))
    style_id = _call_int(material, "instance_id")
    texture_paths = _texture_paths_for_style(model, style_id)
    specularity = _finite_float(_first_attr(material, "specularity", "Specularity"))
    return GeometryMaterial(
        name=name,
        diffuse=diffuse or surface,
        surface=surface,
        specular=specular,
        opacity=opacity,
        specularity=specularity,
        style_id=style_id,
        texture_paths=texture_paths,
    )


def _first_attr(value: Any, *names: str) -> Any:
    for name in names:
        try:
            attr_value = getattr(value, name)
        except Exception:
            continue
        if attr_value is not None:
            return attr_value
    return None


def _color_tuple(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    channels: list[Any]
    components = _first_attr(value, "components")
    if isinstance(components, (list, tuple)) and len(components) >= 3:
        channels = list(components[:3])
    elif all(hasattr(value, channel) for channel in ("r", "g", "b")):
        channels = [_call_or_value(value, "r"), _call_or_value(value, "g"), _call_or_value(value, "b")]
    elif all(hasattr(value, channel) for channel in ("Red", "Green", "Blue")):
        channels = [value.Red, value.Green, value.Blue]
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        channels = list(value[:3])
    else:
        return None

    try:
        return (
            max(0.0, min(1.0, float(channels[0]))),
            max(0.0, min(1.0, float(channels[1]))),
            max(0.0, min(1.0, float(channels[2]))),
        )
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _finite_float(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if number != number:
        return None
    return max(0.0, min(1.0, number))


def _call_or_value(value: Any, name: str) -> Any:
    attr = getattr(value, name)
    return attr() if callable(attr) else attr


def _call_int(value: Any, name: str) -> int | None:
    try:
        attr = getattr(value, name)
        result = attr() if callable(attr) else attr
        return int(result)
    except Exception:
        return None


def _texture_paths_for_style(model: Any | None, style_id: int | None) -> tuple[str, ...]:
    if model is None or style_id is None:
        return ()
    try:
        style = model.by_id(style_id)
    except Exception:
        return ()
    texture_paths: list[str] = []
    for style_item in _iter_style_items(style):
        if not _is_ifc_class(style_item, "IfcSurfaceStyleWithTextures"):
            continue
        for texture in _safe_iter(_first_attr(style_item, "Textures")):
            texture_path = _texture_path(texture)
            if texture_path and texture_path not in texture_paths:
                texture_paths.append(texture_path)
    return tuple(texture_paths)


def _iter_style_items(style: Any) -> list[Any]:
    items = _safe_iter(_first_attr(style, "Styles"))
    if not items and _is_ifc_class(style, "IfcSurfaceStyleWithTextures"):
        return [style]
    return items


def _texture_path(texture: Any) -> str | None:
    for attr_name in ("URLReference", "UrlReference", "Filename", "Location"):
        value = _first_attr(texture, attr_name)
        if value:
            return str(value)
    return None


def _safe_iter(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _is_ifc_class(entity: Any, ifc_class: str) -> bool:
    try:
        return bool(entity.is_a(ifc_class))
    except Exception:
        try:
            return entity.is_a() == ifc_class
        except Exception:
            return False
