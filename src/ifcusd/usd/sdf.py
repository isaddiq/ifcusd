"""Thin, fast authoring helpers on top of the Sdf layer API.

Writing through ``Sdf`` instead of ``Usd.Stage`` avoids recomposition on every
edit, which matters for models with hundreds of thousands of prims. All
schema-related tokens are spelled out explicitly here, so the output does not
depend on which USD plugins happen to be loaded at conversion time.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ifcusd.dependencies import import_usd

_usd = import_usd()
Sdf = _usd.Sdf
Gf = _usd.Gf
Vt = _usd.Vt

_TYPE_CACHE: dict[str, Any] = {}


def value_type(type_name: str) -> Any:
    cached = _TYPE_CACHE.get(type_name)
    if cached is None:
        cached = Sdf.ValueTypeNames.Find(type_name)
        if not cached:
            raise ValueError(f"Unknown Sdf value type {type_name!r}")
        _TYPE_CACHE[type_name] = cached
    return cached


class LayerWriter:
    """Author prims and properties into one ``Sdf.Layer``."""

    def __init__(self, layer: Any) -> None:
        self.layer = layer

    # ------------------------------------------------------------------ prims

    def prim(
        self,
        path: str,
        type_name: str = "",
        *,
        specifier: str = "def",
        kind: str | None = None,
        api_schemas: Sequence[str] = (),
        display_name: str | None = None,
        documentation: str | None = None,
    ) -> Any:
        """Create (or fetch) the prim spec at ``path``; parents must exist or are made ``over``."""
        sdf_path = Sdf.Path(path)
        spec = self.layer.GetPrimAtPath(sdf_path)
        if spec is None:
            parent_path = sdf_path.GetParentPath()
            if parent_path.IsAbsoluteRootPath():
                owner = self.layer
            else:
                owner = self.layer.GetPrimAtPath(parent_path) or Sdf.CreatePrimInLayer(self.layer, parent_path)
            spec = Sdf.PrimSpec(owner, sdf_path.name, _specifier(specifier), type_name)
        spec.specifier = _specifier(specifier)
        if type_name:
            spec.typeName = type_name
        if kind:
            spec.kind = kind
        if api_schemas:
            self.add_api_schemas(spec, api_schemas)
        if display_name:
            set_display_name(spec, display_name)
        if documentation:
            spec.documentation = documentation
        return spec

    @staticmethod
    def add_api_schemas(spec: Any, names: Iterable[str]) -> None:
        existing = spec.GetInfo("apiSchemas") if spec.HasInfo("apiSchemas") else None
        items = list(existing.prependedItems) if existing is not None else []
        for name in names:
            if name not in items:
                items.append(name)
        spec.SetInfo("apiSchemas", Sdf.TokenListOp.Create(prependedItems=items))

    # ------------------------------------------------------------------ properties

    @staticmethod
    def attribute(
        spec: Any,
        name: str,
        type_name: str,
        value: Any = None,
        *,
        uniform: bool = True,
        custom: bool = False,
        display_name: str | None = None,
        display_group: str | None = None,
        documentation: str | None = None,
        custom_data: dict[str, Any] | None = None,
        allowed_tokens: Sequence[str] | None = None,
        interpolation: str | None = None,
    ) -> Any:
        attr = spec.attributes.get(name) if name in spec.attributes else None
        if attr is None:
            variability = Sdf.VariabilityUniform if uniform else Sdf.VariabilityVarying
            attr = Sdf.AttributeSpec(spec, name, value_type(type_name), variability, custom)
        if value is not None:
            attr.default = value
        if display_name:
            attr.displayName = display_name
        if display_group:
            attr.displayGroup = display_group
        if documentation:
            attr.documentation = documentation
        if custom_data:
            attr.customData = custom_data
        if allowed_tokens:
            attr.allowedTokens = list(allowed_tokens)
        if interpolation:
            attr.SetInfo("interpolation", interpolation)
        return attr

    @staticmethod
    def relationship(
        spec: Any,
        name: str,
        targets: Iterable[str],
        *,
        custom: bool = False,
        display_name: str | None = None,
        display_group: str | None = None,
        custom_data: dict[str, Any] | None = None,
    ) -> Any:
        paths = []
        seen = set()
        for target in targets:
            if target and target not in seen:
                seen.add(target)
                paths.append(Sdf.Path(target))
        rel = spec.relationships.get(name) if name in spec.relationships else None
        if rel is None:
            rel = Sdf.RelationshipSpec(spec, name, custom, Sdf.VariabilityUniform)
            rel.targetPathList.explicitItems = paths
        else:
            current = list(rel.targetPathList.explicitItems)
            rel.targetPathList.explicitItems = current + [p for p in paths if p not in current]
        if display_name:
            rel.SetInfo("displayName", display_name)
        if display_group:
            rel.SetInfo("displayGroup", display_group)
        if custom_data:
            rel.customData = custom_data
        return rel

    @staticmethod
    def connect(spec: Any, name: str, type_name: str, source: str, *, value: Any = None) -> Any:
        attr = spec.attributes.get(name) if name in spec.attributes else None
        if attr is None:
            attr = Sdf.AttributeSpec(spec, name, value_type(type_name), Sdf.VariabilityVarying, False)
        if value is not None:
            attr.default = value
        attr.connectionPathList.explicitItems = [Sdf.Path(source)]
        return attr

    # ------------------------------------------------------------------ transforms

    def transform(self, spec: Any, matrix: Any) -> None:
        """Author a single ``xformOp:transform`` (double precision)."""
        self.attribute(spec, "xformOp:transform", "matrix4d", matrix, uniform=False)
        self.attribute(spec, "xformOpOrder", "token[]", ["xformOp:transform"])

    def rotate_x(self, spec: Any, degrees: float) -> None:
        self.attribute(spec, "xformOp:rotateX", "double", float(degrees), uniform=False)
        self.attribute(spec, "xformOpOrder", "token[]", ["xformOp:rotateX"])


def usd_dictionary(data: dict[str, Any]) -> dict[str, Any]:
    """Make a Python dict safe for USD dictionary metadata.

    ``None`` values are dropped, string lists become ``string[]`` and other
    lists are stored as canonical JSON (USD dictionaries cannot hold untyped
    Python lists in a way that survives ``.usda`` round-trips).
    """
    import json

    result: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, dict):
            result[str(key)] = usd_dictionary(value)
        elif isinstance(value, (list, tuple)):
            items = [v for v in value if v is not None]
            if all(isinstance(v, str) for v in items):
                result[str(key)] = Vt.StringArray(items)
            else:
                result[str(key)] = json.dumps(items, sort_keys=True, separators=(",", ":"))
        elif isinstance(value, (bool, int, float, str)):
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result


def set_display_name(spec: Any, text: str) -> None:
    """Prim ``displayName`` metadata (USD 23.11+); ignored on older USD."""
    try:
        spec.SetInfo("displayName", text)
    except Exception:
        pass


def _specifier(name: str) -> Any:
    return {"def": Sdf.SpecifierDef, "over": Sdf.SpecifierOver, "class": Sdf.SpecifierClass}[name]


def matrix_from_rows(values: Sequence[float]) -> Any:
    return Gf.Matrix4d(*[float(v) for v in values])
