from __future__ import annotations

from dataclasses import dataclass


class MissingUsdDependencyError(RuntimeError):
    """Raised when Pixar USD Python bindings are not installed."""


@dataclass(frozen=True)
class UsdBindings:
    Gf: object
    Sdf: object
    Usd: object
    UsdGeom: object
    UsdShade: object
    Vt: object


def import_usd() -> UsdBindings:
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
    except ImportError as exc:
        raise MissingUsdDependencyError(
            "Pixar USD Python bindings are required to write USD files. "
            "Install them with `python -m pip install usd-core`, or install this "
            "package with `python -m pip install -e .`."
        ) from exc

    return UsdBindings(Gf=Gf, Sdf=Sdf, Usd=Usd, UsdGeom=UsdGeom, UsdShade=UsdShade, Vt=Vt)
