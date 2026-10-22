"""Lazy access to the Pixar USD Python bindings."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ifcusd.errors import MissingUsdDependencyError

__all__ = ["MissingUsdDependencyError", "UsdBindings", "import_usd", "usd_version"]


@dataclass(frozen=True)
class UsdBindings:
    Gf: object
    Sdf: object
    Usd: object
    UsdGeom: object
    UsdShade: object
    Vt: object
    UsdLux: object = None
    UsdPhysics: object = None


@lru_cache(maxsize=1)
def import_usd() -> UsdBindings:
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade, Vt
    except ImportError as exc:
        raise MissingUsdDependencyError(
            "Pixar USD Python bindings are required to write USD files. "
            "Install them with `python -m pip install usd-core`, or run IfcUSD inside an "
            "application that ships them (for example NVIDIA Omniverse Kit)."
        ) from exc

    return UsdBindings(
        Gf=Gf, Sdf=Sdf, Usd=Usd, UsdGeom=UsdGeom, UsdShade=UsdShade, Vt=Vt, UsdLux=UsdLux, UsdPhysics=UsdPhysics
    )


def usd_version() -> str:
    try:
        from pxr import Usd

        return ".".join(str(part) for part in Usd.GetVersion())
    except Exception:
        return "unavailable"
