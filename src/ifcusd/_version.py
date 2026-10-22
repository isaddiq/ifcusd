"""Version information for IfcUSD.

``__version__`` is the package version. ``SCHEMA_VERSION`` is the version of the
IFC-in-USD data mapping (prim layout, attribute names and API schemas). It changes
only when the structure of the written USD changes, so consumers can test it to
decide whether they understand a file.
"""

__version__ = "1.1.0"

SCHEMA_VERSION = "1.0"
