"""Exception types raised by IfcUSD."""

from __future__ import annotations


class IfcUsdError(Exception):
    """Base class for all IfcUSD errors."""


class InputError(IfcUsdError):
    """The input file is missing, unreadable or in an unsupported format."""


class UnsupportedSchemaError(InputError):
    """The IFC schema of the input cannot be read by the installed IfcOpenShell."""


class OutputError(IfcUsdError):
    """The USD output could not be written."""


class GeometryError(IfcUsdError):
    """Geometry for an element could not be converted (raised only in strict mode)."""


class MissingUsdDependencyError(IfcUsdError, RuntimeError):
    """The Pixar USD Python bindings (``pxr``) are not importable."""
