from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from ifcusd.cli import main as cli_main

    argv = sys.argv[1:]
    if argv and argv[0] != "convert" and not argv[0].startswith("-"):
        argv = ["convert", *argv]
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
