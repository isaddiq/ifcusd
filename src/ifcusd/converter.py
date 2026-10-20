"""IFC to OpenUSD conversion entry points."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell

from ifcusd._version import SCHEMA_VERSION, __version__
from ifcusd.dependencies import import_usd, usd_version
from ifcusd.errors import GeometryError, InputError, OutputError
from ifcusd.ifc import util
from ifcusd.ifc.geometry import ifcopenshell_log, iterate_shapes, native_stdout_silenced
from ifcusd.ifc.reader import OpenedIfc, georeference_info, header_info, open_ifc
from ifcusd.ifc.structure import ScenePlan, build_plan
from ifcusd.ifc.styles import StyleLibrary
from ifcusd.ifc.units import UnitResolver
from ifcusd.options import ConversionOptions
from ifcusd.report import ConversionReport

log = logging.getLogger(__name__)

OUTPUT_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
IFC_EXTENSIONS = (".ifc", ".ifczip", ".ifcxml")
IFCX_EXTENSIONS = (".ifcx",)


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a conversion.

    Attributes:
        input_path: The converted source file.
        output_path: The written USD file.
        elements_seen: Number of products that received geometry.
        meshes_written: Number of meshes written (inline meshes plus instances).
        spatial_prims_written: Number of spatial structure prims (sites, buildings, storeys, spaces...).
        skipped_geometry: ``"<IfcClass> #<id>: <reason>"`` for each element whose geometry failed.
        report: Full [`ConversionReport`][ifcusd.report.ConversionReport] (counts,
            coverage, warnings, timings, provenance).
    """

    input_path: Path
    output_path: Path
    elements_seen: int = 0
    meshes_written: int = 0
    spatial_prims_written: int = 0
    skipped_geometry: list[str] = field(default_factory=list)
    report: ConversionReport | None = None


class IfcToUsdConverter:
    """Converts IFC (IFC2X3, IFC4, IFC4X3 and, experimentally, IFC5/IFCX) to OpenUSD.

    Args:
        options: Conversion options; defaults to ``ConversionOptions()``.

    Example:
        ```python
        from ifcusd import ConversionOptions, IfcToUsdConverter

        converter = IfcToUsdConverter(ConversionOptions.for_profile("unity"))
        for name in ("architecture", "structure", "mep"):
            converter.convert(f"{name}.ifc", f"{name}.usdz", report_path=f"{name}.report.json")
        ```
    """

    def __init__(self, options: ConversionOptions | None = None) -> None:
        self.options = options or ConversionOptions()

    def convert(
        self,
        input_path: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        *,
        report_path: str | os.PathLike[str] | None = None,
        metadata_json_path: str | os.PathLike[str] | None = None,
        layers: list[str | os.PathLike[str]] | None = None,
    ) -> ConversionResult:
        """Convert one file.

        Args:
            input_path: ``.ifc``, ``.ifczip`` or ``.ifcx`` file.
            output_path: ``.usd``, ``.usda``, ``.usdc`` or ``.usdz`` file. Parent
                folders are created.
            report_path: Also write the conversion report as JSON here.
            metadata_json_path: Also write the metadata sidecar (used by the
                Unity integration) here.
            layers: Additional IFC5 (``.ifcx``) layers composed on top of the
                input, in order. Only valid for IFCX input.

        Returns:
            The [`ConversionResult`][ifcusd.converter.ConversionResult].

        Raises:
            InputError: The input is missing, unreadable or has an unsupported format.
            UnsupportedSchemaError: The IFC schema cannot be read and fallback is off or impossible.
            OutputError: The output extension is unsupported or the file cannot be written.
            GeometryError: Geometry failed and ``ignore_geometry_errors`` is ``False``.
        """
        source = Path(input_path)
        target = Path(output_path)
        if target.suffix.lower() not in OUTPUT_EXTENSIONS:
            raise OutputError(f"Output must end with one of {', '.join(OUTPUT_EXTENSIONS)}: {target}")
        suffix = source.suffix.lower()
        if suffix in IFCX_EXTENSIONS:
            from ifcusd.ifcx.converter import convert_ifcx

            result = convert_ifcx([source, *[Path(p) for p in layers or []]], target, self.options, metadata_json_path)
        elif suffix in IFC_EXTENSIONS:
            if layers:
                raise InputError("Additional layers are only supported for IFC5 (.ifcx) input")
            result = self._convert_ifc(source, target, metadata_json_path)
        else:
            raise InputError(f"Unsupported input extension {source.suffix!r} for {source}")
        if report_path is not None and result.report is not None:
            result.report.to_json(report_path)
        return result

    # ------------------------------------------------------------------ IFC-SPF

    def _convert_ifc(self, source: Path, target: Path, metadata_json_path: Any) -> ConversionResult:
        options = self.options
        usd = import_usd()
        timings: dict[str, float] = {}
        started = time.perf_counter()
        progress = options.progress or (lambda stage, fraction: None)

        progress("read", 0.0)
        opened = open_ifc(source, allow_schema_fallback=options.schema_fallback)
        model = opened.model
        timings["read"] = time.perf_counter() - started
        units = UnitResolver(model)

        mark = time.perf_counter()
        plan = build_plan(model, options)
        timings["plan"] = time.perf_counter() - mark

        target.parent.mkdir(parents=True, exist_ok=True)
        styles = StyleLibrary(model, source_dir=source.parent, output_path=target)
        layer = usd.Sdf.Layer.CreateAnonymous(target.stem + ".usda")
        sidecar: list[dict[str, Any]] | None = [] if metadata_json_path else None

        from ifcusd.usd.scene import SceneWriter

        writer = SceneWriter(layer, plan, model, options, units, styles, sidecar=sidecar)
        header = header_info(model)
        file_info = {
            "sourceName": source.name,
            "schema": opened.schema,
            "schemaIdentifier": opened.declared_schema,
            "compatibilitySchema": opened.compatibility_schema,
            "sha256": opened.sha256,
            **header,
        }
        georeference = georeference_info(model, units) if options.include_metadata else {}
        writer.write_stage(file_info=file_info, georeference=georeference, layer_data=self._layer_data(opened, source))

        mark = time.perf_counter()
        progress("semantics", 0.0)
        writer.write_semantics()
        timings["semantics"] = time.perf_counter() - mark

        mark = time.perf_counter()
        progress("geometry", 0.0)
        warnings: list[str] = list(opened.warnings)
        try:
            # IfcOpenShell worker threads print diagnostics straight to stdout while
            # shapes are being consumed, so stdout stays silenced for the whole loop.
            with native_stdout_silenced():
                for shape in iterate_shapes(
                    model,
                    options,
                    generate_uvs=options.generate_uvs or styles.has_textures,
                    progress=lambda fraction: progress("geometry", fraction),
                ):
                    writer.add_shape(shape)
        except GeometryError:
            raise
        except Exception as exc:
            if not options.ignore_geometry_errors:
                raise GeometryError(str(exc)) from exc
            warnings.append(f"Geometry processing stopped early: {exc}")
        timings["geometry"] = time.perf_counter() - mark

        mark = time.perf_counter()
        progress("write", 0.0)
        writer.finalize()
        self._save(layer, target)
        timings["write"] = time.perf_counter() - mark
        timings["total"] = time.perf_counter() - started
        progress("done", 1.0)

        warnings.extend(styles.warnings)
        report = self._report(opened, source, target, plan, writer, warnings, timings)
        if metadata_json_path and sidecar is not None:
            from ifcusd.sidecar import write_sidecar

            write_sidecar(metadata_json_path, sidecar, report)
        stats = writer.stats
        return ConversionResult(
            input_path=source,
            output_path=target,
            elements_seen=len(stats.products_with_geometry),
            meshes_written=stats.meshes + stats.instances,
            spatial_prims_written=sum(1 for n in plan.nodes() if n.kind == "spatial"),
            skipped_geometry=[f"{e['ifcClass']} #{e['stepId']}: {e['reason']}" for e in stats.geometry_errors],
            report=report,
        )

    def _layer_data(self, opened: OpenedIfc, source: Path) -> dict[str, Any]:
        return {
            "converter": "IfcUSD",
            "converterVersion": __version__,
            "schemaVersion": SCHEMA_VERSION,
            "ifcopenshellVersion": str(ifcopenshell.version),
            "usdVersion": usd_version(),
            "sourceName": source.name,
            "sourceSha256": opened.sha256,
            "ifcSchema": opened.schema,
            "profile": self.options.profile,
            # Thread count changes speed, not output, so it is left out to keep files reproducible.
            "options": {k: v for k, v in self.options.to_dict().items() if k != "threads"},
        }

    @staticmethod
    def _save(layer: Any, target: Path) -> None:
        usd = import_usd()
        if target.suffix.lower() == ".usdz":
            from pxr import UsdUtils

            temporary = target.with_name(f".{target.stem}.ifcusd-tmp.usdc")
            try:
                if not layer.Export(str(temporary)):
                    raise OutputError(f"Could not write {temporary}")
                if target.exists():
                    target.unlink()
                if not UsdUtils.CreateNewUsdzPackage(usd.Sdf.AssetPath(str(temporary)), str(target)):
                    raise OutputError(f"Could not package {target}")
            finally:
                if temporary.exists():
                    temporary.unlink()
            return
        if not layer.Export(str(target)):
            raise OutputError(f"Could not write {target}")

    # ------------------------------------------------------------------ reporting

    def _report(
        self,
        opened: OpenedIfc,
        source: Path,
        target: Path,
        plan: ScenePlan,
        writer: Any,
        warnings: list[str],
        timings: dict[str, float],
    ) -> ConversionReport:
        model = opened.model
        stats = writer.stats
        kinds = Counter(node.kind for node in plan.nodes())
        report = ConversionReport(
            input_path=str(source),
            output_path=str(target),
            input_format="IFC-SPF" if source.suffix.lower() == ".ifc" else "IFC-ZIP",
            ifc_schema=opened.schema,
            declared_schema=opened.declared_schema,
            compatibility_schema=opened.compatibility_schema,
            source_sha256=opened.sha256,
            converter_version=__version__,
            schema_version=SCHEMA_VERSION,
            ifcopenshell_version=str(ifcopenshell.version),
            usd_version=usd_version(),
            profile=self.options.profile,
            options=self.options.to_dict(),
            dropped_entities=dict(opened.dropped_entities),
            timings={key: round(value, 4) for key, value in timings.items()},
        )
        report.counts = {
            "prims": stats.prims,
            "entityPrims": stats.entity_prims,
            "spatial": kinds.get("spatial", 0),
            "elements": kinds.get("element", 0) + kinds.get("feature", 0) + kinds.get("port", 0),
            "types": kinds.get("type", 0),
            "groups": kinds.get("group", 0),
            "relationships": len(plan.generic_relationships) + len(plan.consumed_relationships),
            "relationshipPrims": len(plan.generic_relationships),
            "propertySets": stats.property_sets,
            "properties": stats.properties,
            "quantities": stats.quantities,
            "meshes": stats.meshes,
            "instances": stats.instances,
            "prototypes": stats.prototypes,
            "curves": stats.curves,
            "materials": stats.materials,
            "points": stats.points,
            "triangles": stats.triangles,
            "productsWithGeometry": len(stats.products_with_geometry),
            "filteredProducts": len(plan.filtered_products),
        }
        report.coverage = _coverage(model, plan, self.options)
        report.geometry_errors = list(stats.geometry_errors)
        report.products_without_geometry = _products_without_geometry(model, plan, stats, self.options)
        if report.missing_count:
            warnings.append(f"{report.missing_count} IfcRoot instance(s) are not represented (see coverage)")
        report.warnings = warnings
        return report


def convert_file(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    options: ConversionOptions | None = None,
    **kwargs: Any,
) -> ConversionResult:
    """Convert an IFC file to USD in one call.

    Equivalent to ``IfcToUsdConverter(options).convert(input_path, output_path, **kwargs)``;
    also available as ``ifcusd.convert``.

    Args:
        input_path: ``.ifc``, ``.ifczip`` or ``.ifcx`` file.
        output_path: ``.usd``, ``.usda``, ``.usdc`` or ``.usdz`` file.
        options: Conversion options; defaults to ``ConversionOptions()``.
        **kwargs: ``report_path``, ``metadata_json_path`` and ``layers``, see
            [`IfcToUsdConverter.convert`][ifcusd.converter.IfcToUsdConverter.convert].

    Returns:
        The [`ConversionResult`][ifcusd.converter.ConversionResult].

    Example:
        ```python
        from ifcusd import convert

        result = convert("model.ifc", "model.usdc")
        assert result.report.complete
        ```
    """
    return IfcToUsdConverter(options).convert(input_path, output_path, **kwargs)


convert = convert_file


# ---------------------------------------------------------------------- coverage


def _coverage(model: Any, plan: ScenePlan, options: ConversionOptions) -> dict[str, dict[str, int]]:
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    filtered = {entity.id() for entity in plan.filtered_products}
    attached: set[int] = set()
    attached_to_filtered: set[int] = set()
    if options.include_metadata:
        objects = [node.entity for node in plan.nodes() if node.entity is not None and node.kind in _OBJECT_KINDS]
        for entity in objects:
            attached.update(_property_definition_ids(entity))
        for entity in plan.filtered_products:
            attached_to_filtered.update(_property_definition_ids(entity))
    for entity in util.by_type(model, "IfcRoot"):
        identifier = entity.id()
        cls = entity.is_a()
        entry = coverage[cls]
        entry["inFile"] += 1
        if identifier in plan.by_id:
            entry["prim"] += 1
        elif identifier in plan.consumed_relationships:
            entry["native"] += 1
        elif identifier in plan.skipped_relationships:
            entry["skipped"] += 1
        elif identifier in filtered:
            entry["filtered"] += 1
        elif util.is_a(entity, "IfcPropertyDefinition"):
            is_quantity = util.is_a(entity, "IfcElementQuantity")
            enabled = options.include_metadata and (
                options.include_quantities if is_quantity else options.include_properties
            )
            if not enabled:
                entry["skipped"] += 1
            elif identifier in attached:
                entry["attributes"] += 1
            elif identifier in attached_to_filtered:
                entry["filtered"] += 1
            else:
                entry["missing"] += 1
        elif not options.include_metadata:
            entry["skipped"] += 1
        else:
            entry["missing"] += 1
    return {cls: dict(sorted(values.items())) for cls, values in sorted(coverage.items())}


_OBJECT_KINDS = (
    "context",
    "spatial",
    "element",
    "feature",
    "port",
    "type",
    "group",
    "process",
    "resource",
    "control",
    "actor",
    "object",
)


def _property_definition_ids(entity: Any) -> set[int]:
    """Ids of the property set definitions attached to an object or type."""
    ids: set[int] = set()
    for rel in util.inverse(entity, "IsDefinedBy"):
        if util.is_a(rel, "IfcRelDefinesByProperties"):
            relating = util.attr(rel, "RelatingPropertyDefinition")
            for definition in relating if isinstance(relating, (list, tuple)) else (relating,):
                if definition is not None:
                    ids.add(definition.id())
    for definition in util.attr(entity, "HasPropertySets", ()) or ():
        ids.add(definition.id())
    return ids


def _products_without_geometry(
    model: Any, plan: ScenePlan, stats: Any, options: ConversionOptions
) -> list[dict[str, Any]]:
    log_text = ifcopenshell_log()
    messages: dict[int, list[str]] = defaultdict(list)
    for line in log_text.splitlines():
        for match in re.finditer(r"#(\d+)\s*=", line):
            messages[int(match.group(1))].append(line.strip()[:300])
    result = []
    for node in plan.nodes():
        entity = node.entity
        if entity is None or node.kind not in ("spatial", "element", "feature", "port"):
            continue
        if entity.id() in stats.products_with_geometry:
            continue
        representation = util.attr(entity, "Representation")
        if representation is None or not util.attr(representation, "Representations"):
            continue
        reason = "no tessellation produced by IfcOpenShell"
        if util.is_a(entity, "IfcOpeningElement") and not options.include_openings:
            continue
        if util.is_a(entity, "IfcSpace") and options.spaces == "skip":
            continue
        if options.exclude_types and util.is_any(entity, options.exclude_types):
            continue  # geometry intentionally excluded
        identifiers = {
            str(util.attr(r, "RepresentationIdentifier") or "")
            for r in util.attr(representation, "Representations", ())
        }
        if not options.include_curves and identifiers <= {"Axis", "FootPrint", "Annotation", "Reference", "Box", "CoG"}:
            reason = "curve-only representation (curves disabled by profile/options)"
        record = {
            "stepId": entity.id(),
            "globalId": util.attr(entity, "GlobalId"),
            "ifcClass": entity.is_a(),
            "name": util.attr(entity, "Name"),
            "representations": sorted(i for i in identifiers if i),
            "reason": reason,
        }
        if messages.get(entity.id()):
            record["log"] = messages[entity.id()][:3]
        result.append(record)
    return result
