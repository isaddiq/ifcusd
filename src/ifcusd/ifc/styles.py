"""IFC surface styles to physically based material descriptions.

IFC presentation styles predate PBR. The mapping used here:

====================================  ===========================================
IFC                                   UsdPreviewSurface
====================================  ===========================================
SurfaceColour x DiffuseColour factor  ``diffuseColor``
DiffuseColour (IfcColourRgb)          ``diffuseColor``
Transparency                          ``opacity = 1 - Transparency``
ReflectanceMethod METAL               ``metallic = 1``
IfcSpecularRoughness r                ``roughness = r``
IfcSpecularExponent e                 ``roughness = sqrt(2 / (e + 2))`` (Blinn-Phong to GGX)
ReflectanceMethod (no highlight)      roughness preset per method (MIRROR 0.05 ... MATT 0.9)
IfcSurfaceStyleRefraction             ``ior = RefractionIndex``
ReflectanceMethod GLASS               ``ior = 1.5`` unless given
IfcImageTexture / Blob / Pixel        ``UsdUVTexture`` driving ``diffuseColor``
====================================  ===========================================

The original IFC values are stored on the USD material as attributes, so the
approximation is always traceable.
"""

from __future__ import annotations

import binascii
import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifcusd.ifc import util
from ifcusd.naming import UniqueNames, safe_identifier

_ROUGHNESS_BY_METHOD = {
    "MIRROR": 0.05,
    "GLASS": 0.05,
    "METAL": 0.3,
    "PLASTIC": 0.4,
    "PHONG": 0.5,
    "BLINN": 0.5,
    "STRAUSS": 0.5,
    "PHYSICAL": 0.5,
    "MATT": 0.9,
    "FLAT": 1.0,
    "NOTDEFINED": 0.5,
}

DEFAULT_COLOR = (0.8, 0.8, 0.8)


@dataclass
class MaterialSpec:
    """Renderer-neutral description of one USD material."""

    key: str
    name: str = ""
    display_name: str | None = None
    source_class: str | None = None
    step_id: int | None = None
    base_color: tuple[float, float, float] = DEFAULT_COLOR
    opacity: float = 1.0
    metallic: float = 0.0
    roughness: float = 0.5
    ior: float | None = None
    specular_color: tuple[float, float, float] | None = None
    texture: str | None = None
    ifc_attributes: dict[str, Any] = field(default_factory=dict)


class StyleLibrary:
    """Creates and caches :class:`MaterialSpec` objects for one model."""

    def __init__(self, model: Any, *, source_dir: Path | None, output_path: Path | None) -> None:
        self.model = model
        self.source_dir = source_dir
        self.output_path = output_path
        self.specs: dict[str, MaterialSpec] = {}
        self.warnings: list[str] = []
        self._names = UniqueNames()
        self._style_names: dict[int, str] = {}
        # Pre-assign names to every IfcSurfaceStyle in file order so names do not
        # depend on the order in which geometry happens to be processed.
        for style in sorted(util.by_type(model, "IfcSurfaceStyle"), key=lambda e: e.id()):
            label = util.attr(style, "Name") or "SurfaceStyle"
            self._style_names[style.id()] = self._names.claim(safe_identifier(label, "SurfaceStyle"))
        self.has_textures = bool(util.by_type(model, "IfcSurfaceStyleWithTextures"))

    def spec_for(self, ios_material: Any) -> MaterialSpec:
        """Material for an IfcOpenShell geometry material."""
        instance_id = _call_int(ios_material, "instance_id")
        entity = None
        if instance_id:
            try:
                entity = self.model.by_id(instance_id)
            except Exception:
                entity = None
        if entity is not None and util.is_a(entity, "IfcSurfaceStyle"):
            key = f"style:{instance_id}"
            if key not in self.specs:
                spec = self._from_surface_style(entity, ios_material)
                spec.key = key
                spec.name = self._style_names.get(instance_id) or self._names.claim(f"SurfaceStyle_{instance_id}")
                self.specs[key] = spec
            return self.specs[key]

        values = _ios_values(ios_material)
        if entity is not None:
            key = f"entity:{instance_id}"
            base = f"{entity.is_a()}_{instance_id}"
        else:
            name = str(_first(ios_material, "name") or "Default")
            key = "default:" + name + ":" + ",".join(f"{c:.4f}" for c in values["base_color"])
            base = f"Default_{name}"
        if key not in self.specs:
            spec = MaterialSpec(key=key, display_name=str(_first(ios_material, "name") or base), **values)
            spec.source_class = entity.is_a() if entity is not None else None
            spec.step_id = instance_id or None
            spec.name = (
                f"{safe_identifier(base, 'Material')}_{_short_hash(key)}" if entity is None else safe_identifier(base)
            )
            self.specs[key] = spec
        return self.specs[key]

    def spec_for_style_entity(self, style: Any) -> MaterialSpec | None:
        """Material for an ``IfcSurfaceStyle`` referenced directly (e.g. by IfcMaterial)."""
        if not util.is_a(style, "IfcSurfaceStyle"):
            return None
        key = f"style:{style.id()}"
        if key not in self.specs:
            spec = self._from_surface_style(style, None)
            spec.key = key
            spec.name = self._style_names.get(style.id()) or self._names.claim(f"SurfaceStyle_{style.id()}")
            self.specs[key] = spec
        return self.specs[key]

    def style_name(self, style_id: int) -> str | None:
        return self._style_names.get(style_id)

    # ------------------------------------------------------------------ IFC styles

    def _from_surface_style(self, style: Any, ios_material: Any | None) -> MaterialSpec:
        spec = MaterialSpec(key="")
        spec.display_name = util.attr(style, "Name")
        spec.source_class = style.is_a()
        spec.step_id = style.id()
        side = util.attr(style, "Side")
        if side:
            spec.ifc_attributes["side"] = str(side)
        if ios_material is not None:
            ios = _ios_values(ios_material)
            spec.base_color, spec.opacity = ios["base_color"], ios["opacity"]

        surface_colour = None
        transparency = None
        highlight_roughness = None
        method = None
        for item in util.attr(style, "Styles", ()) or ():
            if util.is_a(item, "IfcSurfaceStyleShading"):
                surface_colour = _rgb(util.attr(item, "SurfaceColour")) or surface_colour
                if surface_colour is not None:
                    spec.base_color = surface_colour
                    spec.ifc_attributes["surfaceColour"] = surface_colour
                value = util.attr(item, "Transparency")
                if value is not None:
                    transparency = float(value)
                if util.is_a(item, "IfcSurfaceStyleRendering"):
                    diffuse = _colour_or_factor(util.attr(item, "DiffuseColour"), surface_colour)
                    if diffuse is not None:
                        spec.base_color = diffuse
                        spec.ifc_attributes["diffuseColour"] = diffuse
                    specular = _colour_or_factor(util.attr(item, "SpecularColour"), surface_colour)
                    if specular is not None:
                        spec.specular_color = specular
                        spec.ifc_attributes["specularColour"] = specular
                    for name in ("TransmissionColour", "DiffuseTransmissionColour", "ReflectionColour"):
                        colour = _colour_or_factor(util.attr(item, name), surface_colour)
                        if colour is not None:
                            spec.ifc_attributes[name[0].lower() + name[1:]] = colour
                    highlight = util.attr(item, "SpecularHighlight")
                    if highlight is not None:
                        highlight_type = highlight.is_a() if hasattr(highlight, "is_a") else None
                        raw = float(getattr(highlight, "wrappedValue", highlight))
                        spec.ifc_attributes["specularHighlight"] = raw
                        if highlight_type == "IfcSpecularRoughness":
                            highlight_roughness = max(0.0, min(1.0, raw))
                        elif raw >= 0:
                            highlight_roughness = max(0.02, min(1.0, math.sqrt(2.0 / (raw + 2.0))))
                    method = util.attr(item, "ReflectanceMethod") or method
            elif util.is_a(item, "IfcSurfaceStyleWithTextures"):
                spec.texture = self._texture_path(item) or spec.texture
            elif util.is_a(item, "IfcSurfaceStyleRefraction"):
                index = util.attr(item, "RefractionIndex")
                if index is not None:
                    spec.ior = float(index)
            elif util.is_a(item, "IfcExternallyDefinedSurfaceStyle"):
                for name in ("Location", "Identification", "Name"):
                    value = util.attr(item, name)
                    if value:
                        spec.ifc_attributes[f"external{name}"] = str(value)

        if transparency is not None:
            spec.opacity = max(0.0, min(1.0, 1.0 - transparency))
            spec.ifc_attributes["transparency"] = transparency
        if method:
            spec.ifc_attributes["reflectanceMethod"] = str(method)
            if str(method) == "METAL":
                spec.metallic = 1.0
            if str(method) == "GLASS" and spec.ior is None:
                spec.ior = 1.5
        if highlight_roughness is not None:
            spec.roughness = highlight_roughness
        elif method:
            spec.roughness = _ROUGHNESS_BY_METHOD.get(str(method), 0.5)
        return spec

    # ------------------------------------------------------------------ textures

    def _texture_path(self, style_with_textures: Any) -> str | None:
        for texture in util.attr(style_with_textures, "Textures", ()) or ():
            try:
                if util.is_a(texture, "IfcImageTexture"):
                    reference = util.attr(texture, "URLReference") or util.attr(texture, "UrlReference")
                    if reference:
                        return self._resolve_reference(str(reference))
                elif util.is_a(texture, "IfcBlobTexture"):
                    return self._write_blob(texture)
                elif util.is_a(texture, "IfcPixelTexture"):
                    return self._write_pixels(texture)
            except Exception as exc:  # texture problems must never stop conversion
                self.warnings.append(f"Texture #{texture.id()} could not be converted: {exc}")
        return None

    def _resolve_reference(self, reference: str) -> str:
        if "://" in reference or self.source_dir is None:
            return reference
        candidate = Path(reference)
        if not candidate.is_absolute():
            candidate = (self.source_dir / candidate).resolve()
        if candidate.exists() and self.output_path is not None:
            try:
                return Path(os.path.relpath(candidate, self.output_path.parent)).as_posix()
            except ValueError:  # different drive on Windows
                return candidate.as_posix()
        return reference

    def _texture_dir(self) -> Path | None:
        if self.output_path is None:
            return None
        directory = self.output_path.parent / f"{self.output_path.stem}_textures"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _write_blob(self, texture: Any) -> str | None:
        directory = self._texture_dir()
        raster = util.attr(texture, "RasterCode")
        if directory is None or raster is None:
            return None
        data = _binary_to_bytes(raster)
        extension = str(util.attr(texture, "RasterFormat") or "png").lower()
        target = directory / f"blob_{texture.id()}.{extension}"
        target.write_bytes(data)
        return f"{directory.name}/{target.name}"

    def _write_pixels(self, texture: Any) -> str | None:
        directory = self._texture_dir()
        if directory is None:
            return None
        width = int(util.attr(texture, "Width"))
        height = int(util.attr(texture, "Height"))
        components = int(util.attr(texture, "ColourComponents"))
        pixels = [_binary_to_bytes(p) for p in util.attr(texture, "Pixel", ()) or ()]
        target = directory / f"pixel_{texture.id()}.png"
        target.write_bytes(_encode_png(width, height, components, pixels))
        return f"{directory.name}/{target.name}"


# ---------------------------------------------------------------------- helpers


def _first(value: Any, *names: str) -> Any:
    for name in names:
        try:
            attribute = getattr(value, name)
        except Exception:
            continue
        if attribute is not None:
            return attribute
    return None


def _call_int(value: Any, name: str) -> int | None:
    try:
        attribute = getattr(value, name)
        result = attribute() if callable(attribute) else attribute
        return int(result)
    except Exception:
        return None


def _ios_colour(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        components = getattr(value, "components", None)
        if callable(components):
            components = components()
        if components is not None and len(components) >= 3:
            return tuple(max(0.0, min(1.0, float(c))) for c in components[:3])  # type: ignore[return-value]
    except Exception:
        pass
    try:
        channels = [getattr(value, c) for c in ("r", "g", "b")]
        channels = [c() if callable(c) else c for c in channels]
        return tuple(max(0.0, min(1.0, float(c))) for c in channels)  # type: ignore[return-value]
    except Exception:
        pass
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return tuple(max(0.0, min(1.0, float(c))) for c in value[:3])  # type: ignore[return-value]
    return None


def _ios_values(material: Any) -> dict[str, Any]:
    diffuse = _ios_colour(_first(material, "diffuse")) or _ios_colour(_first(material, "surface"))
    transparency = None
    try:
        has_transparency = material.has_transparency() if hasattr(material, "has_transparency") else True
        if has_transparency:
            transparency = float(material.transparency)
    except Exception:
        transparency = None
    if transparency is not None and not math.isfinite(transparency):
        transparency = None
    opacity = 1.0 if transparency is None else max(0.0, min(1.0, 1.0 - transparency))
    return {"base_color": diffuse or DEFAULT_COLOR, "opacity": opacity}


def _rgb(colour: Any) -> tuple[float, float, float] | None:
    if colour is None or not util.is_a(colour, "IfcColourRgb"):
        return None
    try:
        return (
            max(0.0, min(1.0, float(colour.Red))),
            max(0.0, min(1.0, float(colour.Green))),
            max(0.0, min(1.0, float(colour.Blue))),
        )
    except Exception:
        return None


def _colour_or_factor(value: Any, surface: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    rgb = _rgb(value)
    if rgb is not None:
        return rgb
    try:
        factor = float(getattr(value, "wrappedValue", value))
    except Exception:
        return None
    base = surface or DEFAULT_COLOR
    return tuple(max(0.0, min(1.0, c * factor)) for c in base)  # type: ignore[return-value]


def _short_hash(text: str) -> str:
    return format(binascii.crc32(text.encode("utf-8")) & 0xFFFFFFFF, "08x")


def _binary_to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    text = str(value)
    if set(text) <= {"0", "1"} and text:
        padding = (-len(text)) % 8
        text = "0" * padding + text
        return int(text, 2).to_bytes(len(text) // 8, "big")
    return bytes.fromhex(text)


def _encode_png(width: int, height: int, components: int, pixels: list[bytes]) -> bytes:
    colour_type = {1: 0, 2: 4, 3: 2, 4: 6}[components]
    rows = []
    for y in range(height):
        row = b"".join(p[-components:] for p in pixels[y * width : (y + 1) * width])
        rows.append(b"\x00" + row)
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
