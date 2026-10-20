"""Run IfcUSD from a source checkout without installing it.

    python convert_ifc_to_usd.py model.ifc model.usd [options]

is the same as ``ifcusd convert model.ifc model.usd [options]``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    src = Path(__file__).resolve().parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from ifcusd.cli import main as cli_main

    argv = sys.argv[1:]
    if argv and argv[0] not in ("convert", "validate", "info", "schema") and not argv[0].startswith("-"):
        argv = ["convert", *argv]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
