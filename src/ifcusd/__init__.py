"""IfcUSD: convert IFC building models to OpenUSD.

Quick start::

    from ifcusd import ConversionOptions, convert

    result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))
    print("\\n".join(result.report.summary_lines()))
"""

from ifcusd._version import SCHEMA_VERSION, __version__
from ifcusd.errors import (
    GeometryError,
    IfcUsdError,
    InputError,
    MissingUsdDependencyError,
    OutputError,
    UnsupportedSchemaError,
)
from ifcusd.options import PROFILES, ConversionOptions

__all__ = [
    "PROFILES",
    "SCHEMA_VERSION",
    "ConversionOptions",
    "ConversionReport",
    "ConversionResult",
    "GeometryError",
    "IfcToUsdConverter",
    "IfcUsdError",
    "InputError",
    "MissingUsdDependencyError",
    "OutputError",
    "UnsupportedSchemaError",
    "__version__",
    "convert",
    "convert_file",
    "validate_usd",
]

_LAZY = {
    "ConversionResult": ("ifcusd.converter", "ConversionResult"),
    "IfcToUsdConverter": ("ifcusd.converter", "IfcToUsdConverter"),
    "convert": ("ifcusd.converter", "convert"),
    "convert_file": ("ifcusd.converter", "convert_file"),
    "ConversionReport": ("ifcusd.report", "ConversionReport"),
    "validate_usd": ("ifcusd.validate", "validate_usd"),
}


def __getattr__(name: str):
    # Heavy dependencies (IfcOpenShell, OpenUSD) are only imported when needed.
    if name in _LAZY:
        import importlib

        module_name, attribute = _LAZY[name]
        return getattr(importlib.import_module(module_name), attribute)
    raise AttributeError(f"module 'ifcusd' has no attribute {name!r}")
