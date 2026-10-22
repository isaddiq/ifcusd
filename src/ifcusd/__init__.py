"""IfcUSD: convert IFC building models to OpenUSD, and OpenUSD back to IFC.

Quick start::

    from ifcusd import ConversionOptions, convert, convert_usd_to_ifc

    result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))
    print("\\n".join(result.report.summary_lines()))

    # ... add robot or simulation data in USD, then write it back into the IFC model:
    convert_usd_to_ifc("model.usdc", "model.updated.ifc", source="model.ifc")
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
from ifcusd.options import PROFILES, ConversionOptions, IfcExportOptions

__all__ = [
    "PROFILES",
    "SCHEMA_VERSION",
    "ConversionOptions",
    "ConversionReport",
    "ConversionResult",
    "GeometryError",
    "IfcExportOptions",
    "IfcExportReport",
    "IfcExportResult",
    "IfcToUsdConverter",
    "IfcUsdError",
    "InputError",
    "MissingUsdDependencyError",
    "OutputError",
    "UnsupportedSchemaError",
    "UsdToIfcConverter",
    "__version__",
    "convert",
    "convert_file",
    "convert_usd_to_ifc",
    "validate_usd",
]

_LAZY = {
    "ConversionResult": ("ifcusd.converter", "ConversionResult"),
    "IfcToUsdConverter": ("ifcusd.converter", "IfcToUsdConverter"),
    "convert": ("ifcusd.converter", "convert"),
    "convert_file": ("ifcusd.converter", "convert_file"),
    "ConversionReport": ("ifcusd.report", "ConversionReport"),
    "validate_usd": ("ifcusd.validate", "validate_usd"),
    "UsdToIfcConverter": ("ifcusd.reverse.converter", "UsdToIfcConverter"),
    "IfcExportResult": ("ifcusd.reverse.converter", "IfcExportResult"),
    "convert_usd_to_ifc": ("ifcusd.reverse.converter", "convert_usd_to_ifc"),
    "IfcExportReport": ("ifcusd.reverse.report", "IfcExportReport"),
}


def __getattr__(name: str):
    # Heavy dependencies (IfcOpenShell, OpenUSD) are only imported when needed.
    if name in _LAZY:
        import importlib

        module_name, attribute = _LAZY[name]
        return getattr(importlib.import_module(module_name), attribute)
    raise AttributeError(f"module 'ifcusd' has no attribute {name!r}")
