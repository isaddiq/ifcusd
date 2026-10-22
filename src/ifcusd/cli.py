"""Command line interface: ``ifcusd convert | to-ifc | validate | info | schema``.

Exit codes: 0 success, 1 completed with problems (validation errors, or
``--strict`` and the conversion was not complete), 2 usage or environment
error, 3 conversion failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from ifcusd._version import SCHEMA_VERSION, __version__
from ifcusd.bootstrap import DependencyInstallError, ensure_runtime_dependencies
from ifcusd.options import (
    DEFAULT_EXCLUDE_PATHS,
    MATERIAL_MODELS,
    NAMING_MODES,
    PROFILES,
    SPACE_MODES,
    ConversionOptions,
    IfcExportOptions,
    default_thread_count,
)

EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_FAILED = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ifcusd",
        description="Convert IFC (IFC2X3, IFC4, IFC4X3, IFC5/IFCX) building models to OpenUSD, and back to IFC.",
    )
    parser.add_argument("--version", action="version", version=f"ifcusd {__version__} (schema {SCHEMA_VERSION})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser(
        "convert",
        help="Convert an IFC file to USD.",
        description="Convert an IFC file to .usd, .usda, .usdc or .usdz.",
    )
    convert.add_argument("input", type=Path, help="Input .ifc, .ifczip or .ifcx file.")
    convert.add_argument("output", type=Path, help="Output .usd, .usda, .usdc or .usdz file.")
    convert.add_argument(
        "--profile", choices=PROFILES, default="generic", help="Target application preset (default: generic)."
    )
    convert.add_argument(
        "--layer", action="append", default=[], type=Path, help="Additional IFCX layer, applied on top (repeatable)."
    )

    scene = convert.add_argument_group("scene")
    scene.add_argument("--up-axis", choices=("Y", "Z"), help="Stage up axis (profile default: Z; unity: Y).")
    scene.add_argument(
        "--meters-per-unit", type=float, help="Stage linear unit in metres, e.g. 0.01 for centimetres (default 1)."
    )
    scene.add_argument("--root", dest="root_prim_name", help="Name of the root/default prim (default: World).")
    scene.add_argument("--naming", choices=NAMING_MODES, help="Prim naming: name_guid (default), guid or name.")

    selection = convert.add_argument_group("element selection")
    selection.add_argument(
        "--include-type",
        action="append",
        default=[],
        help="Only convert this IFC class and its subclasses (repeatable).",
    )
    selection.add_argument(
        "--exclude-type", action="append", default=[], help="Skip this IFC class and its subclasses (repeatable)."
    )

    geometry = convert.add_argument_group("geometry")
    geometry.add_argument(
        "--threads",
        type=int,
        default=default_thread_count(),
        help="Tessellation threads (output does not depend on it).",
    )
    geometry.add_argument(
        "--no-instancing", action="store_true", help="Write every mesh inline instead of sharing prototypes."
    )
    geometry.add_argument(
        "--no-curves", action="store_true", help="Skip curve representations (axes, alignments, annotations)."
    )
    geometry.add_argument("--openings", action="store_true", help="Also tessellate opening elements (hidden).")
    geometry.add_argument("--spaces", choices=SPACE_MODES, help="Space geometry: hidden (default), visible or skip.")
    geometry.add_argument(
        "--uvs", action="store_true", help="Generate box-projected UVs (automatic when the model has textures)."
    )
    geometry.add_argument("--single-sided", action="store_true", help="Author meshes as single-sided.")
    geometry.add_argument(
        "--world-coords", action="store_true", help="Bake transforms into points (disables instancing)."
    )
    geometry.add_argument("--linear-deflection", type=float, help="Tessellation chord tolerance in metres.")
    geometry.add_argument("--angular-deflection", type=float, help="Tessellation angular tolerance in radians.")

    data = convert.add_argument_group("IFC data")
    data.add_argument(
        "--no-metadata", action="store_true", help="Geometry only: no IFC attributes, properties or relationships."
    )
    data.add_argument("--no-properties", action="store_true", help="Skip property sets.")
    data.add_argument("--no-quantities", action="store_true", help="Skip quantity sets.")
    data.add_argument(
        "--no-materials", action="store_true", help="Skip IFC material definitions (render materials are kept)."
    )
    data.add_argument(
        "--no-relationships",
        action="store_true",
        help="Skip relationship prims, classifications, documents and layers.",
    )
    data.add_argument(
        "--all-relationship-prims",
        action="store_true",
        help="Also write natively mapped relationships (aggregation, types, materials...) as prims (full graph).",
    )
    data.add_argument("--no-owner-history", action="store_true", help="Skip IfcOwnerHistory records.")
    data.add_argument(
        "--no-type-inheritance", action="store_true", help="Do not copy type property sets onto occurrences."
    )
    data.add_argument("--no-semantic-labels", action="store_true", help="Do not author UsdSemantics class labels.")
    data.add_argument(
        "--material-model", choices=MATERIAL_MODELS, help="preview (UsdPreviewSurface) or preview+mdl (adds OmniPBR)."
    )

    outputs = convert.add_argument_group("outputs and checks")
    outputs.add_argument("--report", type=Path, help="Write the JSON conversion report here.")
    outputs.add_argument("--metadata-json", type=Path, help="Write a metadata sidecar (for Unity and other tools).")
    outputs.add_argument(
        "--validate", action="store_true", help="Validate the written file, including completeness against the source."
    )
    outputs.add_argument(
        "--strict",
        "--fail-on-geometry-error",  # 0.1.x spelling
        action="store_true",
        help="Stop on geometry errors; exit 1 unless the conversion is complete.",
    )
    # Accepted for compatibility with 0.1.x command lines; local coordinates and
    # no automatic installation are now the defaults.
    outputs.add_argument("--local-coords", action="store_true", help=argparse.SUPPRESS)
    outputs.add_argument("--no-auto-install", action="store_true", help=argparse.SUPPRESS)
    outputs.add_argument(
        "--no-schema-fallback",
        action="store_true",
        help="Fail instead of reading unknown IFC schema versions with the closest supported one.",
    )
    _add_common(convert)

    validate = subparsers.add_parser("validate", help="Validate a USD file written by IfcUSD.")
    validate.add_argument("usd", type=Path, help="USD file to validate.")
    validate.add_argument("--source", type=Path, help="Original IFC file, to verify nothing was lost.")
    validate.add_argument("--json", type=Path, help="Write the validation report as JSON.")
    validate.add_argument("--no-usd-validators", action="store_true", help="Skip OpenUSD's built-in validators.")
    _add_common(validate)

    info = subparsers.add_parser("info", help="Summarize an IFC file (schema, header, units, contents).")
    info.add_argument("input", type=Path, help="Input .ifc, .ifczip or .ifcx file.")
    info.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    _add_common(info)

    to_ifc = subparsers.add_parser(
        "to-ifc",
        help="Convert USD back to IFC, including data added in USD (robots, simulation...).",
        description=(
            "Convert a .usd, .usda, .usdc or .usdz stage to IFC. With --source, the original IFC file is "
            "updated with what changed or was added in USD (merge mode); without it the IFC model is "
            "rebuilt from USD alone (standalone mode)."
        ),
    )
    to_ifc.add_argument("input", type=Path, help="Input .usd, .usda, .usdc or .usdz file.")
    to_ifc.add_argument("output", type=Path, help="Output .ifc file.")
    to_ifc.add_argument("--source", type=Path, help="The IFC file the USD was converted from (merge mode).")
    to_ifc.add_argument(
        "--layer",
        action="append",
        default=[],
        type=Path,
        help="Additional USD layer composed on top, e.g. a recorded robot run (repeatable; later layers win).",
    )
    to_ifc.add_argument("--schema", help="IFC schema in standalone mode (default: the stage's, else IFC4).")
    to_ifc.add_argument(
        "--time", default="earliest", help="Stage time to export: earliest (default), latest, default or a time code."
    )
    export_data = to_ifc.add_argument_group("data")
    export_data.add_argument("--no-attributes", action="store_true", help="Do not write changed IFC attributes.")
    export_data.add_argument("--no-properties", action="store_true", help="Do not write changed or new properties.")
    export_data.add_argument(
        "--no-placements", action="store_true", help="Do not move products whose transform changed."
    )
    export_data.add_argument(
        "--no-new-objects",
        action="store_true",
        help="Ignore prims without IFC identity (robots, sensors, obstacles...).",
    )
    export_data.add_argument("--no-geometry", action="store_true", help="Do not write geometry for new elements.")
    export_data.add_argument("--no-physics", action="store_true", help="Do not write UsdPhysics data (IfcUSD_Physics).")
    export_data.add_argument(
        "--no-semantics", action="store_true", help="Do not write semantic labels as classifications."
    )
    export_data.add_argument("--no-time-series", action="store_true", help="Write time-sampled values as plain values.")
    export_data.add_argument(
        "--no-trajectories", action="store_true", help="Do not write trajectories of animated prims."
    )
    export_data.add_argument(
        "--no-templates", action="store_true", help="Do not declare new property sets as IfcPropertySetTemplates."
    )
    export_data.add_argument(
        "--namespace",
        action="append",
        default=[],
        help="Extension namespace collected into property sets (repeatable; default: robot and sim).",
    )
    export_data.add_argument("--new-object-class", help="IFC class for new prims (default: IfcBuildingElementProxy).")
    export_data.add_argument(
        "--exclude", action="append", default=[], help="fnmatch pattern of prim paths to ignore (repeatable)."
    )
    export_data.add_argument("--time-origin", help="ISO 8601 date and time of USD time code 0, for time series.")
    export_outputs = to_ifc.add_argument_group("outputs and checks")
    export_outputs.add_argument("--report", type=Path, help="Write the JSON export report here.")
    export_outputs.add_argument(
        "--validate", action="store_true", help="Validate the written IFC file against its schema (exit 1 on errors)."
    )
    export_outputs.add_argument(
        "--no-schema-fallback",
        action="store_true",
        help="Fail instead of reading a source with an unknown IFC schema version using the closest supported one.",
    )
    _add_common(to_ifc)

    schema = subparsers.add_parser("schema", help="Locate or print the IfcUSD USD schema plugin.")
    schema.add_argument("--print", dest="print_schema", action="store_true", help="Print schema.usda.")
    _add_common(schema)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed progress and warnings.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only print errors.")
    parser.add_argument("--install-deps", action="store_true", help="pip-install missing runtime dependencies first.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.WARNING),
        format="%(levelname)s: %(message)s",
    )
    if args.command == "schema":
        return _schema(args)
    try:
        ensure_runtime_dependencies(auto_install=args.install_deps, verbose=args.verbose)
    except DependencyInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    from ifcusd.errors import IfcUsdError

    try:
        if args.command == "convert":
            return _convert(args)
        if args.command == "to-ifc":
            return _to_ifc(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "info":
            return _info(args)
    except IfcUsdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    parser.error(f"unknown command: {args.command}")
    return EXIT_USAGE


# ---------------------------------------------------------------------- convert


def _options(args: argparse.Namespace) -> ConversionOptions:
    overrides: dict[str, Any] = {"threads": max(1, args.threads)}
    for name in (
        "up_axis",
        "meters_per_unit",
        "root_prim_name",
        "naming",
        "spaces",
        "material_model",
        "linear_deflection",
        "angular_deflection",
    ):
        value = getattr(args, name)
        if value is not None:
            overrides[name] = value
    flags = {
        "no_instancing": ("instancing", False),
        "no_curves": ("include_curves", False),
        "openings": ("include_openings", True),
        "uvs": ("generate_uvs", True),
        "single_sided": ("double_sided", False),
        "world_coords": ("use_world_coords", True),
        "no_metadata": ("include_metadata", False),
        "no_properties": ("include_properties", False),
        "no_quantities": ("include_quantities", False),
        "no_materials": ("include_materials", False),
        "no_relationships": ("include_relationships", False),
        "no_owner_history": ("include_owner_history", False),
        "no_type_inheritance": ("inherit_type_properties", False),
        "no_semantic_labels": ("semantic_labels", False),
        "strict": ("ignore_geometry_errors", False),
        "no_schema_fallback": ("schema_fallback", False),
    }
    for flag, (name, value) in flags.items():
        if getattr(args, flag):
            overrides[name] = value
    if args.all_relationship_prims:
        overrides["relationship_prims"] = "all"
    if args.world_coords:
        overrides["instancing"] = False
    if args.include_type:
        overrides["include_types"] = tuple(args.include_type)
    if args.exclude_type:
        overrides["exclude_types"] = tuple(args.exclude_type)
    if not args.quiet and sys.stderr.isatty():
        overrides["progress"] = _Progress()
    return ConversionOptions.for_profile(args.profile, **overrides)


class _Progress:
    def __init__(self) -> None:
        self._last = ""

    def __call__(self, stage: str, fraction: float) -> None:
        text = f"\r{stage:<10s} {int(fraction * 100):3d}%"
        if text != self._last:
            sys.stderr.write(text)
            sys.stderr.flush()
            self._last = text
        if stage == "done":
            sys.stderr.write("\r" + " " * 20 + "\r")


def _convert(args: argparse.Namespace) -> int:
    from ifcusd.converter import convert_file

    if args.input.suffix.lower() in (".usd", ".usda", ".usdc", ".usdz"):
        print(f"error: {args.input.name} is a USD file; use `ifcusd to-ifc` to convert USD to IFC", file=sys.stderr)
        return EXIT_USAGE

    try:
        options = _options(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    result = convert_file(
        args.input,
        args.output,
        options,
        report_path=args.report,
        metadata_json_path=args.metadata_json,
        layers=args.layer or None,
    )
    report = result.report
    status = EXIT_OK
    if report is not None and not args.quiet:
        for line in report.summary_lines():
            print(line)
        if args.verbose:
            for warning in report.warnings:
                print(f"warning: {warning}")
            for item in report.products_without_geometry:
                print(f"no geometry: {item['ifcClass']} #{item['stepId']} {item.get('name') or ''} - {item['reason']}")
            for item in report.geometry_errors:
                print(f"geometry error: {item['ifcClass']} #{item['stepId']} - {item['reason']}")
    if args.strict and report is not None and not report.complete:
        status = EXIT_PROBLEMS
    if args.validate:
        from ifcusd.validate import validate_usd

        source = args.input if args.input.suffix.lower() in (".ifc", ".ifczip") else None
        validation = validate_usd(args.output, source=source)
        if not args.quiet:
            for line in validation.summary_lines():
                print(line)
        if not validation.ok:
            status = EXIT_PROBLEMS
    return status


# ---------------------------------------------------------------------- to-ifc


def _export_options(args: argparse.Namespace) -> IfcExportOptions:
    overrides: dict[str, Any] = {"time": args.time, "schema_fallback": not args.no_schema_fallback}
    if args.schema:
        overrides["schema"] = args.schema
    flags = {
        "no_attributes": "update_attributes",
        "no_properties": "update_properties",
        "no_placements": "update_placements",
        "no_new_objects": "include_new_objects",
        "no_geometry": "include_geometry",
        "no_physics": "include_physics",
        "no_semantics": "include_semantics",
        "no_time_series": "include_time_series",
        "no_trajectories": "include_trajectories",
        "no_templates": "property_templates",
    }
    for flag, name in flags.items():
        if getattr(args, flag):
            overrides[name] = False
    if args.namespace:
        overrides["extension_namespaces"] = tuple(args.namespace)
    if args.new_object_class:
        overrides["new_object_class"] = args.new_object_class
    if args.exclude:
        overrides["exclude_paths"] = DEFAULT_EXCLUDE_PATHS + tuple(args.exclude)
    if args.time_origin:
        overrides["time_origin"] = args.time_origin
    return IfcExportOptions(**overrides)


def _to_ifc(args: argparse.Namespace) -> int:
    from ifcusd.reverse.converter import convert_usd_to_ifc

    try:
        options = _export_options(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    result = convert_usd_to_ifc(
        args.input,
        args.output,
        options,
        source=args.source,
        layers=args.layer or None,
        report_path=args.report,
        validate=args.validate,
    )
    report = result.report
    if report is not None and not args.quiet:
        for line in report.summary_lines():
            print(line)
        if args.verbose:
            for warning in report.warnings:
                print(f"warning: {warning}")
            for item in report.created_objects:
                print(f"new {item['ifcClass']} {item['globalId']} {item['name']!r} from {item['usdPath']}")
            for item in report.not_in_usd:
                print(f"not in USD (kept): {item['ifcClass']} {item['globalId']} {item.get('name') or ''}")
        if args.validate and report.validation and not report.validation.get("ok"):
            limit = 100 if args.verbose else 10
            for issue in report.validation.get("issues", [])[:limit]:
                origin = " (already in the source)" if issue.get("inSource") else ""
                print(
                    f"invalid{origin}: {issue.get('instance', '')[:120]} "
                    f"{issue.get('attribute', '')}: {issue.get('message', '')}"
                )
    if args.validate and report is not None and report.introduced_errors:
        return EXIT_PROBLEMS
    return EXIT_OK


# ---------------------------------------------------------------------- validate / info / schema


def _validate(args: argparse.Namespace) -> int:
    from ifcusd.validate import validate_usd

    report = validate_usd(args.usd, source=args.source, run_usd_validators=not args.no_usd_validators)
    if args.json:
        report.to_json(args.json)
    if not args.quiet:
        for line in report.summary_lines(limit=100 if args.verbose else 20):
            print(line)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def _info(args: argparse.Namespace) -> int:
    from collections import Counter

    from ifcusd.ifc import util
    from ifcusd.ifc.reader import available_schemas, georeference_info, header_info, open_ifc
    from ifcusd.ifc.units import UnitResolver

    if args.input.suffix.lower() == ".ifcx":
        from ifcusd.ifcx.compose import find_roots, flatten, load_layer

        layer = load_layer(args.input)
        nodes = flatten([layer])
        classes = Counter(
            str(n.attributes["bsi::ifc::class"].get("code"))
            for n in nodes.values()
            if isinstance(n.attributes.get("bsi::ifc::class"), dict)
        )
        data = {
            "format": "IFCX",
            "header": layer.header,
            "imports": layer.imports,
            "nodes": len(nodes),
            "roots": find_roots(nodes),
            "classes": dict(classes.most_common()),
            "sha256": layer.sha256,
        }
    else:
        opened = open_ifc(args.input)
        model = opened.model
        units = UnitResolver(model)
        products = Counter(e.is_a() for e in util.by_type(model, "IfcProduct"))
        relationships = Counter(e.is_a() for e in util.by_type(model, "IfcRelationship"))
        data = {
            "format": "IFC-SPF",
            "schema": opened.schema,
            "declaredSchema": opened.declared_schema,
            "compatibilitySchema": opened.compatibility_schema,
            "availableSchemas": list(available_schemas()),
            "sha256": opened.sha256,
            "header": header_info(model),
            "units": units.project_units(),
            "lengthScale": units.length_scale,
            "georeference": georeference_info(model, units),
            "instances": len(list(model)),
            "products": dict(products.most_common()),
            "types": len(util.by_type(model, "IfcTypeObject")),
            "propertySets": len(util.by_type(model, "IfcPropertySet")),
            "quantitySets": len(util.by_type(model, "IfcElementQuantity")),
            "relationships": dict(relationships.most_common()),
            "droppedEntities": opened.dropped_entities,
        }
    if args.json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        for key, value in data.items():
            if isinstance(value, dict) and value:
                print(f"{key}:")
                for sub_key, sub_value in value.items():
                    print(f"  {sub_key}: {sub_value}")
            else:
                print(f"{key}: {value}")
    return EXIT_OK


def _schema(args: argparse.Namespace) -> int:
    from ifcusd.schema import plugin_path

    path = plugin_path()
    if args.print_schema:
        print((path / "schema.usda").read_text(encoding="utf-8"))
    else:
        print(path)
        if not args.quiet:
            print(
                f"Set PXR_PLUGINPATH_NAME={path} to register the IfcUSD schema (version {SCHEMA_VERSION}).",
                file=sys.stderr,
            )
    return EXIT_OK
