from __future__ import annotations

import argparse
from pathlib import Path
import sys

from ifcusd.bootstrap import DependencyInstallError, ensure_runtime_dependencies
from ifcusd.options import ConversionOptions, default_thread_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ifcusd",
        description="Convert IFC files to OpenUSD with IfcOpenShell geometry and metadata.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Convert an IFC file to USD.")
    convert.add_argument("input", type=Path, help="Input IFC file.")
    convert.add_argument("output", type=Path, help="Output .usd, .usda, or .usdc file.")
    convert.add_argument("--threads", type=int, default=default_thread_count())
    convert.add_argument("--no-metadata", action="store_true", help="Do not write IFC metadata.")
    convert.add_argument("--no-properties", action="store_true", help="Do not write IFC property sets.")
    convert.add_argument("--no-quantities", action="store_true", help="Do not write IFC quantities.")
    convert.add_argument("--no-materials", action="store_true", help="Do not write material metadata.")
    convert.add_argument(
        "--local-coords",
        action="store_true",
        help="Do not ask IfcOpenShell for world-coordinate tessellation.",
    )
    convert.add_argument(
        "--include-type",
        action="append",
        default=[],
        help="Only convert this IFC class. May be passed more than once.",
    )
    convert.add_argument(
        "--exclude-type",
        action="append",
        default=[],
        help="Skip this IFC class. May be passed more than once.",
    )
    convert.add_argument(
        "--fail-on-geometry-error",
        action="store_true",
        help="Stop conversion on the first geometry/write failure.",
    )
    convert.add_argument("--up-axis", choices=("Y", "Z"), default="Z")
    convert.add_argument(
        "--no-auto-install",
        action="store_true",
        help="Do not install missing runtime dependencies before conversion.",
    )
    convert.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "convert":
        try:
            ensure_runtime_dependencies(
                auto_install=not args.no_auto_install,
                verbose=args.verbose,
            )
        except DependencyInstallError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        from ifcusd.converter import convert_file
        from ifcusd.dependencies import MissingUsdDependencyError

        options = ConversionOptions(
            threads=args.threads,
            include_metadata=not args.no_metadata,
            include_properties=not args.no_properties,
            include_quantities=not args.no_quantities,
            include_materials=not args.no_materials,
            use_world_coords=not args.local_coords,
            ignore_geometry_errors=not args.fail_on_geometry_error,
            include_types=tuple(args.include_type),
            exclude_types=tuple(args.exclude_type),
            up_axis=args.up_axis,
        )
        try:
            result = convert_file(args.input, args.output, options)
        except MissingUsdDependencyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        print(
            f"converted {result.meshes_written}/{result.elements_seen} meshed elements "
            f"to {result.output_path}"
        )
        if result.skipped_geometry:
            print(f"skipped {len(result.skipped_geometry)} elements with geometry/write errors")
            if args.verbose:
                for item in result.skipped_geometry:
                    print(f"  - {item}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
