"""Conversion options and target profiles."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

PROFILES = ("generic", "omniverse", "unity")
NAMING_MODES = ("name_guid", "guid", "name")
SPACE_MODES = ("hidden", "visible", "skip")
MATERIAL_MODELS = ("preview", "preview+mdl")
RELATIONSHIP_MODES = ("generic", "all")
UP_AXES = ("Y", "Z")

# Settings that each target profile changes from the dataclass defaults. Anything a
# caller passes explicitly to ``ConversionOptions.for_profile`` wins over these.
_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "generic": {},
    "omniverse": {
        "up_axis": "Z",
        "material_model": "preview+mdl",
        "instancing": True,
        "include_curves": True,
        "legacy_semantics": True,
    },
    "unity": {
        # Unity's USD importer handles meshes, transforms and UsdPreviewSurface only.
        "up_axis": "Y",
        "instancing": False,
        "include_curves": False,
        "spaces": "skip",
        "material_model": "preview",
        "semantic_labels": False,
    },
}


def default_thread_count() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count - 1)


ProgressCallback = Callable[[str, float], None]


@dataclass(frozen=True)
class ConversionOptions:
    """Options controlling IFC to USD conversion.

    Use :meth:`for_profile` to start from the settings tuned for a target
    application (``"generic"``, ``"omniverse"`` or ``"unity"``).
    """

    profile: str = "generic"

    # Scene layout
    up_axis: str = "Z"
    meters_per_unit: float | None = 1.0
    root_prim_name: str = "World"
    naming: str = "name_guid"

    # Element selection (applies to non-spatial products; spatial structure is always kept)
    include_types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()

    # Geometry
    threads: int = field(default_factory=default_thread_count)
    use_world_coords: bool = False
    instancing: bool = True
    include_curves: bool = True
    include_openings: bool = False
    spaces: str = "hidden"
    generate_uvs: bool = False
    double_sided: bool = True
    linear_deflection: float | None = None
    angular_deflection: float | None = None

    # IFC data
    include_metadata: bool = True
    include_properties: bool = True
    include_quantities: bool = True
    include_materials: bool = True
    include_relationships: bool = True
    relationship_prims: str = "generic"
    include_owner_history: bool = True
    inherit_type_properties: bool = True
    semantic_labels: bool = True
    legacy_semantics: bool = False

    # Look development
    material_model: str = "preview"

    # Robustness
    ignore_geometry_errors: bool = True
    schema_fallback: bool = True

    progress: ProgressCallback | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"profile must be one of {PROFILES}, got {self.profile!r}")
        if self.naming not in NAMING_MODES:
            raise ValueError(f"naming must be one of {NAMING_MODES}, got {self.naming!r}")
        if self.spaces not in SPACE_MODES:
            raise ValueError(f"spaces must be one of {SPACE_MODES}, got {self.spaces!r}")
        if self.relationship_prims not in RELATIONSHIP_MODES:
            raise ValueError(f"relationship_prims must be one of {RELATIONSHIP_MODES}, got {self.relationship_prims!r}")
        if self.material_model not in MATERIAL_MODELS:
            raise ValueError(f"material_model must be one of {MATERIAL_MODELS}, got {self.material_model!r}")
        if self.up_axis.upper() not in UP_AXES:
            raise ValueError(f"up_axis must be 'Y' or 'Z', got {self.up_axis!r}")
        if self.up_axis != self.up_axis.upper():
            object.__setattr__(self, "up_axis", self.up_axis.upper())
        if self.meters_per_unit is not None and not self.meters_per_unit > 0:
            raise ValueError("meters_per_unit must be a positive number")
        if self.threads < 1:
            raise ValueError("threads must be at least 1")
        if not self.root_prim_name or not self.root_prim_name.replace("_", "a").isalnum():
            raise ValueError("root_prim_name must be a valid USD identifier")
        if self.root_prim_name[0].isdigit():
            raise ValueError("root_prim_name must not start with a digit")
        object.__setattr__(self, "include_types", tuple(self.include_types))
        object.__setattr__(self, "exclude_types", tuple(self.exclude_types))

    @classmethod
    def for_profile(cls, profile: str = "generic", **overrides: Any) -> ConversionOptions:
        """Return options for a target profile, with ``overrides`` applied on top."""
        if profile not in PROFILES:
            raise ValueError(f"profile must be one of {PROFILES}, got {profile!r}")
        values = dict(_PROFILE_DEFAULTS[profile])
        values.update(overrides)
        values["profile"] = profile
        return cls(**values)

    @property
    def output_meters_per_unit(self) -> float:
        return float(self.meters_per_unit or 1.0)

    @property
    def output_scale(self) -> float:
        """Factor applied to metre values to express them in output stage units."""
        return 1.0 / self.output_meters_per_unit

    @property
    def write_mdl(self) -> bool:
        return self.material_model == "preview+mdl"

    def with_changes(self, **changes: Any) -> ConversionOptions:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Serializable view of the options (used for provenance metadata)."""
        data = asdict(self)
        data.pop("progress", None)
        data["include_types"] = list(self.include_types)
        data["exclude_types"] = list(self.exclude_types)
        return data

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]
