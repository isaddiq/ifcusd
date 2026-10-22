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

    Options are immutable. Start from a target preset with
    [`for_profile`][ifcusd.options.ConversionOptions.for_profile] and override
    individual fields, or change an existing instance with
    [`with_changes`][ifcusd.options.ConversionOptions.with_changes].

    Example:
        ```python
        from ifcusd import ConversionOptions

        options = ConversionOptions.for_profile("omniverse", meters_per_unit=0.01, threads=8)
        walls_only = options.with_changes(include_types=("IfcWall",))
        ```

    Attributes:
        profile: Target preset the options were derived from: ``"generic"``,
            ``"omniverse"`` or ``"unity"``.
        up_axis: Stage up axis, ``"Z"`` (IFC's convention) or ``"Y"``. For
            ``"Y"`` the root prim gets a single ``rotateX -90``; no vertex is altered.
        meters_per_unit: Stage linear unit in metres (``0.01`` for centimetres).
            Geometry is scaled; property values keep their IFC project units.
        root_prim_name: Name of the root and default prim.
        naming: Prim naming scheme: ``"name_guid"`` (default, stable across
            re-exports), ``"guid"`` or ``"name"``.
        include_types: Only convert elements of these IFC classes (subclasses
            included). Spatial structure is always kept.
        exclude_types: Skip elements of these IFC classes (subclasses included).
        threads: Tessellation threads. Output does not depend on it.
        use_world_coords: Bake transforms into points instead of writing
            local meshes under transforms (disables instancing).
        instancing: Share identical type geometry through instanceable prims.
        include_curves: Write curve representations (axes, alignments,
            annotations) as ``BasisCurves``.
        include_openings: Also tessellate opening elements (written hidden).
        spaces: Space geometry: ``"hidden"``, ``"visible"`` or ``"skip"``.
        generate_uvs: Generate box-projected UVs (automatic for textured models).
        double_sided: Author meshes as double-sided.
        linear_deflection: Tessellation chord tolerance in metres
            (IfcOpenShell default when ``None``).
        angular_deflection: Tessellation angular tolerance in radians
            (IfcOpenShell default when ``None``).
        include_metadata: Write IFC data at all. ``False`` writes geometry only.
        include_properties: Write property sets.
        include_quantities: Write quantity sets.
        include_materials: Write IFC material definitions (render materials
            are always written).
        include_relationships: Write relationship prims, classifications,
            documents, libraries, approvals, constraints and layers.
        relationship_prims: ``"generic"`` writes prims only for relationships
            without a native USD form; ``"all"`` writes every relationship as
            a prim as well (complete relationship graph).
        include_owner_history: Write ``IfcOwnerHistory`` records.
        inherit_type_properties: Copy type property sets onto occurrences
            (occurrence values win per property).
        semantic_labels: Author ``SemanticsLabelsAPI`` class labels.
        legacy_semantics: Also author the legacy Omniverse ``SemanticsAPI``.
        material_model: ``"preview"`` (UsdPreviewSurface) or ``"preview+mdl"``
            (adds an OmniPBR MDL network).
        ignore_geometry_errors: Continue when an element cannot be tessellated
            (it is listed in the report). ``False`` raises instead.
        schema_fallback: Read IFC schema identifiers the installed
            IfcOpenShell does not know with the closest supported schema.
        progress: Optional callback ``progress(stage, fraction)`` called with
            ``stage`` in ``read``, ``semantics``, ``geometry``, ``write``, ``done``.
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
        """Options tuned for a target application, with ``overrides`` applied on top.

        Args:
            profile: ``"generic"``, ``"omniverse"`` or ``"unity"``.
            **overrides: Any [`ConversionOptions`][ifcusd.options.ConversionOptions] field.

        Returns:
            The combined options.

        Raises:
            ValueError: If the profile or an option value is invalid.
        """
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
        """Copy of these options with some fields changed."""
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


# Prims that NVIDIA Omniverse and Isaac Sim add to every stage and that are not part of
# the modelled facility: viewport cameras, render settings and the default ground plane.
DEFAULT_EXCLUDE_PATHS = ("/OmniverseKit_*", "/Render", "/Render/*", "*/defaultGroundPlane", "*/GroundPlane")
TIME_KEYWORDS = ("earliest", "latest", "default")


@dataclass(frozen=True)
class IfcExportOptions:
    """Options controlling USD to IFC conversion.

    With a source IFC file (*merge* mode) the source is the starting point and
    only what changed or was added in USD is written. Without one (*standalone*
    mode) the whole IFC model is rebuilt from the stage.

    Example:
        ```python
        from ifcusd import IfcExportOptions

        options = IfcExportOptions(time="latest", extension_namespaces=("robot", "sim", "nav"))
        ```

    Attributes:
        schema: Target IFC schema for standalone mode (``IFC2X3``, ``IFC4``,
            ``IFC4X3_ADD2``). Defaults to the schema recorded in the stage, or
            ``IFC4``. In merge mode the source schema is always kept.
        time: Stage time whose values and transforms are exported:
            ``"earliest"`` (default: the first time sample, or the default
            value of static attributes), ``"latest"`` (the stage end time),
            ``"default"`` (default values only) or a USD time code.
        update_attributes: Write changed IFC attributes (``ifc:name``, ...) and
            changed ``ifc:class`` values of existing entities.
        update_properties: Write changed and new property and quantity values.
        update_placements: Move existing products whose USD transform changed.
        include_new_objects: Turn prims without IFC identity (robots, sensors,
            obstacles, ...) into new IFC elements.
        include_geometry: Write tessellated geometry for new elements (and for
            every product in standalone mode).
        include_physics: Write ``UsdPhysics`` data as the ``IfcUSD_Physics`` property set.
        include_semantics: Write ``UsdSemantics`` labels as classification references.
        include_time_series: Write time-sampled values as IFC time series.
        include_trajectories: Write animated transforms as a ``Trajectory`` time
            series in the ``IfcUSD_Simulation`` property set.
        extension_namespaces: Attribute namespaces collected into property sets:
            ``robot:navigation:clearance`` becomes the property ``clearance`` of
            the set ``Robot_Navigation`` and ``robot:battery`` the property
            ``battery`` of ``Robot_Common``. Values in these namespaces are in
            SI units (lengths in stage units), not project units.
        property_templates: Declare every new, non-standard property set as an
            ``IfcPropertySetTemplate`` in an ``IfcProjectLibrary`` (IFC4 and later).
        new_object_class: IFC class for new prims that are neither robots nor
            sensors and carry no ``ifc:class``.
        exclude_paths: ``fnmatch`` patterns of prim paths to ignore.
        time_origin: ISO 8601 date and time of USD time code 0, for time series.
        schema_fallback: Read a source IFC with an unknown schema identifier
            using the closest supported schema.
    """

    schema: str | None = None
    time: str | float = "earliest"
    update_attributes: bool = True
    update_properties: bool = True
    update_placements: bool = True
    include_new_objects: bool = True
    include_geometry: bool = True
    include_physics: bool = True
    include_semantics: bool = True
    include_time_series: bool = True
    include_trajectories: bool = True
    extension_namespaces: tuple[str, ...] = ("robot", "sim")
    property_templates: bool = True
    new_object_class: str = "IfcBuildingElementProxy"
    exclude_paths: tuple[str, ...] = DEFAULT_EXCLUDE_PATHS
    time_origin: str = "1970-01-01T00:00:00"
    schema_fallback: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.time, str) and self.time not in TIME_KEYWORDS:
            try:
                object.__setattr__(self, "time", float(self.time))
            except ValueError:
                raise ValueError(f"time must be one of {TIME_KEYWORDS} or a number, got {self.time!r}") from None
        for namespace in self.extension_namespaces:
            if not namespace or not namespace.replace("_", "a").isalnum() or namespace == "ifc":
                raise ValueError(f"invalid extension namespace {namespace!r}")
        if not self.new_object_class.startswith("Ifc"):
            raise ValueError(f"new_object_class must be an IFC class name, got {self.new_object_class!r}")
        from datetime import datetime

        try:
            datetime.fromisoformat(self.time_origin)
        except ValueError:
            raise ValueError(f"time_origin must be an ISO 8601 date and time, got {self.time_origin!r}") from None
        object.__setattr__(self, "extension_namespaces", tuple(self.extension_namespaces))
        object.__setattr__(self, "exclude_paths", tuple(self.exclude_paths))

    def with_changes(self, **changes: Any) -> IfcExportOptions:
        """Copy of these options with some fields changed."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Serializable view of the options (used for provenance metadata)."""
        data = asdict(self)
        data["extension_namespaces"] = list(self.extension_namespaces)
        data["exclude_paths"] = list(self.exclude_paths)
        return data
