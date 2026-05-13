"""IFC to OpenUSD conversion tools."""

from ifcusd.options import ConversionOptions

__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "IfcToUsdConverter",
    "convert_file",
]


def __getattr__(name: str):
    if name in {"ConversionResult", "IfcToUsdConverter", "convert_file"}:
        from ifcusd.converter import ConversionResult, IfcToUsdConverter, convert_file

        values = {
            "ConversionResult": ConversionResult,
            "IfcToUsdConverter": IfcToUsdConverter,
            "convert_file": convert_file,
        }
        return values[name]
    raise AttributeError(f"module 'ifcusd' has no attribute {name!r}")
