from __future__ import annotations

from dataclasses import dataclass, field
import os


def default_thread_count() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count - 1)


@dataclass(frozen=True)
class ConversionOptions:
    """Options controlling IFC to USD conversion."""

    threads: int = field(default_factory=default_thread_count)
    include_metadata: bool = True
    include_properties: bool = True
    include_quantities: bool = True
    include_materials: bool = True
    use_world_coords: bool = True
    ignore_geometry_errors: bool = True
    include_types: tuple[str, ...] = ()
    exclude_types: tuple[str, ...] = ()
    up_axis: str = "Z"
    meters_per_unit: float | None = None
