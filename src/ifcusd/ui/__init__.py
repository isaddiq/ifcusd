"""IfcUSD Studio, a browser UI for converting IFC to USD and back and browsing the data.

Start it with ``ifcusd ui``, ``ifcusd-studio`` or, from a source checkout,
``python app.py``. It needs nothing beyond IfcUSD's own dependencies.
"""

from __future__ import annotations

from ifcusd.ui.server import DEFAULT_PORT, Studio, make_server, serve

__all__ = ["DEFAULT_PORT", "Studio", "main", "make_server", "serve"]


def main() -> int:
    """``ifcusd-studio``: the same as ``ifcusd ui``."""
    import sys

    from ifcusd.cli import main as cli_main

    return cli_main(["ui", *sys.argv[1:]])
