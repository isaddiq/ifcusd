"""Open IfcUSD Studio, the browser UI for IFC <-> USD conversion, from a source checkout.

    python app.py [--port 8765] [--no-browser] [--workspace DIR] [--install-deps]

is the same as ``ifcusd ui``. It needs IfcUSD's dependencies (IfcOpenShell,
usd-core and NumPy) but not an installed IfcUSD. When one is missing and the
script runs in a terminal, it offers to install it with pip first.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    src = Path(__file__).resolve().parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from ifcusd.bootstrap import missing_runtime_dependencies
    from ifcusd.cli import main as cli_main

    argv = ["ui", *sys.argv[1:]]
    missing = missing_runtime_dependencies()
    if missing and "--install-deps" not in argv and sys.stdin.isatty():
        names = ", ".join(f"{d.label} ({d.package_spec})" for d in missing)
        print(f"IfcUSD Studio needs {names}, which is not installed for {sys.executable}.")
        try:
            answer = input("Install it now with pip? [Y/n] ")
        except EOFError:
            answer = "n"
        if answer.strip().lower() in ("", "y", "yes"):
            argv.append("--install-deps")
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
