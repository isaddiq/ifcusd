"""Validation of converted USD files.

Three levels of checking:

1. OpenUSD's own validators (``UsdValidation``, USD 24.11+), when available.
2. Structural checks that apply to any IfcUSD output: stage metadata, mesh
   topology, geom subsets, relationship targets and GlobalId uniqueness.
3. Optional round-trip completeness against the source IFC file: every
   object definition (by GlobalId) and every property/quantity value must be
   present in the USD file.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ifcusd.dependencies import import_usd

# Findings that are expected in environments without NVIDIA's MDL plugins: the
# OmniPBR.mdl core material is resolved from Omniverse's MDL search path.
_MDL_EXPECTED = ("MissingSourceTypeInRegistry", "MissingReference", "UnresolvableDependency")
_CORE_SHADERS = ("'UsdPreviewSurface'", "'UsdUVTexture'", "'UsdPrimvarReader_")


@dataclass
class ValidationIssue:
    """One finding; ``severity`` is ``error``, ``warning`` or ``info``."""

    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    path: str = ""


@dataclass
class ValidationReport:
    """Result of [`validate_usd`][ifcusd.validate.validate_usd]: issues plus statistics."""

    usd_path: str
    source_path: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, message: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(severity, code, message, path))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    def summary_lines(self, limit: int = 20) -> list[str]:
        counts = Counter(issue.severity for issue in self.issues)
        lines = [
            f"{self.usd_path}: {'OK' if self.ok else 'FAILED'} "
            f"({counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} notes)"
        ]
        for key, value in sorted(self.stats.items()):
            lines.append(f"  {key}: {value}")
        for issue in [i for i in self.issues if i.severity != "info"][:limit]:
            location = f" [{issue.path}]" if issue.path else ""
            lines.append(f"  {issue.severity.upper():7s} {issue.code}: {issue.message}{location}")
        return lines


def validate_usd(
    usd_path: str | Path,
    *,
    source: str | Path | None = None,
    run_usd_validators: bool = True,
) -> ValidationReport:
    """Validate a USD file written by IfcUSD.

    Args:
        usd_path: The ``.usd``/``.usda``/``.usdc``/``.usdz`` file.
        source: The original IFC file. When given, every object definition (by
            GlobalId) and every property and quantity value of the source must
            be present in the USD file.
        run_usd_validators: Run OpenUSD's ``UsdValidation`` validators (USD 24.11+).

    Returns:
        A [`ValidationReport`][ifcusd.validate.ValidationReport]; ``report.ok`` is
        ``False`` when any error was found.

    Example:
        ```python
        from ifcusd import validate_usd

        report = validate_usd("model.usdc", source="model.ifc")
        for line in report.summary_lines():
            print(line)
        ```
    """
    usd = import_usd()
    Usd, UsdGeom = usd.Usd, usd.UsdGeom
    report = ValidationReport(usd_path=str(usd_path), source_path=str(source) if source else None)
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        report.add("error", "OpenFailed", "OpenUSD could not open the file")
        return report

    _check_stage_metadata(stage, report, UsdGeom)
    if run_usd_validators:
        _run_usd_validators(stage, report)
    _check_structure(stage, report, Usd, UsdGeom)
    if source is not None:
        _check_completeness(stage, Path(source), report, Usd)
    return report


# ---------------------------------------------------------------------- checks


def _check_stage_metadata(stage: Any, report: ValidationReport, UsdGeom: Any) -> None:
    default_prim = stage.GetDefaultPrim()
    if not default_prim:
        report.add("error", "MissingDefaultPrim", "stage has no valid defaultPrim")
    if not stage.HasAuthoredMetadata("upAxis"):
        report.add("error", "MissingUpAxis", "stage does not author upAxis")
    if not stage.HasAuthoredMetadata("metersPerUnit"):
        report.add("error", "MissingMetersPerUnit", "stage does not author metersPerUnit")
    report.stats["upAxis"] = str(UsdGeom.GetStageUpAxis(stage))
    report.stats["metersPerUnit"] = float(UsdGeom.GetStageMetersPerUnit(stage))
    data = stage.GetRootLayer().customLayerData.get("ifcusd", {})
    if data:
        report.stats["converterVersion"] = data.get("converterVersion")
        report.stats["schemaVersion"] = data.get("schemaVersion")
        report.stats["ifcSchema"] = data.get("ifcSchema")


def _run_usd_validators(stage: Any, report: ValidationReport) -> None:
    try:
        from pxr import UsdValidation
    except ImportError:
        report.add("info", "UsdValidationUnavailable", "OpenUSD < 24.11: built-in validators skipped")
        return
    try:
        registry = UsdValidation.ValidationRegistry()
        validators = [
            v
            for v in registry.GetOrLoadAllValidators()
            if not v.GetMetadata().name.startswith(("usdPhysicsValidators", "usdSkelValidators"))
            and "Usdz" not in v.GetMetadata().name
            and "PackageEncapsulation" not in v.GetMetadata().name
        ]
        errors = UsdValidation.ValidationContext(validators).Validate(stage)
    except Exception as exc:  # broken plugin setups must not hide our own checks
        report.add("warning", "UsdValidationFailed", f"OpenUSD validators could not run: {exc}".splitlines()[0])
        return
    for error in errors:
        name = error.GetName()
        message = error.GetMessage()
        severity = "error" if error.GetType() == UsdValidation.ValidationErrorType.Error else "warning"
        if any(code in name for code in _MDL_EXPECTED) and "mdl" in message.lower():
            severity = "info"  # MDL is resolved by NVIDIA Omniverse, not by plain OpenUSD
        elif "MissingShaderIdInRegistry" in name and any(s in message for s in _CORE_SHADERS):
            # The core UsdPreviewSurface shaders ship with OpenUSD; if the registry lacks
            # them the local USD installation is incomplete, not the file.
            severity = "warning"
            message = f"{message} (OpenUSD shader plugins are not installed correctly in this environment)"
        sites = error.GetSites()
        path = str(sites[0].GetPrim().GetPath()) if sites and sites[0].GetPrim() else ""
        report.add(severity, f"usd:{name}", message.splitlines()[0][:300], path)


def _check_structure(stage: Any, report: ValidationReport, Usd: Any, UsdGeom: Any) -> None:
    meshes = curves = instances = 0
    global_ids: Counter[str] = Counter()
    entity_prims = 0
    for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate):
        if prim.IsInstance():
            instances += 1
        if prim.IsA(UsdGeom.Mesh):
            meshes += 1
            _check_mesh(prim, report, UsdGeom)
        elif prim.IsA(UsdGeom.BasisCurves):
            curves += 1
        global_id = prim.GetAttribute("ifc:globalId")
        if global_id and global_id.HasAuthoredValue() and not prim.IsInPrototype():
            global_ids[str(global_id.Get())] += 1
        if prim.HasAttribute("ifc:class"):
            entity_prims += 1
        for rel in prim.GetRelationships():
            name = rel.GetName()
            if not (name.startswith("ifc:") or name == "material:binding" or name.startswith("collection:")):
                continue
            for target in rel.GetTargets():
                if not stage.GetPrimAtPath(target.GetPrimPath()):
                    report.add(
                        "error", "DanglingRelationship", f"{name} targets missing prim {target}", str(prim.GetPath())
                    )
    duplicates = {gid: n for gid, n in global_ids.items() if n > 1}
    for gid, count in sorted(duplicates.items()):
        report.add("warning", "DuplicateGlobalId", f"GlobalId {gid} appears on {count} prims (duplicate in source?)")
    report.stats.update({"meshes": meshes, "curves": curves, "instances": instances, "ifcEntityPrims": entity_prims})


def _check_mesh(prim: Any, report: ValidationReport, UsdGeom: Any) -> None:
    mesh = UsdGeom.Mesh(prim)
    path = str(prim.GetPath())
    points = mesh.GetPointsAttr().Get() or []
    counts = mesh.GetFaceVertexCountsAttr().Get() or []
    indices = mesh.GetFaceVertexIndicesAttr().Get() or []
    if sum(counts) != len(indices):
        report.add("error", "MeshTopology", "faceVertexCounts do not add up to faceVertexIndices", path)
    if len(indices) and (max(indices) >= len(points) or min(indices) < 0):
        report.add("error", "MeshIndices", "faceVertexIndices out of range", path)
    normals = mesh.GetNormalsAttr().Get()
    if normals is not None and len(normals):
        interpolation = mesh.GetNormalsInterpolation()
        expected = {"vertex": len(points), "varying": len(points), "faceVarying": len(indices), "uniform": len(counts)}
        if expected.get(interpolation, len(normals)) != len(normals):
            report.add(
                "error", "MeshNormals", f"{len(normals)} normals do not match {interpolation} interpolation", path
            )
    if not mesh.GetExtentAttr().HasAuthoredValue():
        report.add("warning", "MissingExtent", "mesh has no authored extent", path)
    for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh):
        subset_indices = subset.GetIndicesAttr().Get() or []
        if len(subset_indices) and max(subset_indices) >= len(counts):
            report.add("error", "SubsetIndices", "GeomSubset indices exceed face count", str(subset.GetPath()))


def _check_completeness(stage: Any, source: Path, report: ValidationReport, Usd: Any) -> None:
    """Re-read the IFC file and make sure its objects and property values are all present."""
    from ifcusd.ifc import util
    from ifcusd.ifc.properties import PropertyExtractor
    from ifcusd.ifc.reader import open_ifc
    from ifcusd.ifc.units import UnitResolver

    data = stage.GetRootLayer().customLayerData.get("ifcusd", {})
    options = dict(data.get("options", {}))
    include_types = list(options.get("include_types") or [])
    exclude_types = list(options.get("exclude_types") or [])
    if not options.get("include_metadata", True):
        report.add("info", "CompletenessSkipped", "file was written without IFC metadata")
        return

    opened = open_ifc(source, allow_schema_fallback=True)
    model = opened.model
    sha = data.get("sourceSha256")
    if sha and sha != opened.sha256:
        report.add("warning", "SourceChanged", "source file checksum differs from the one recorded at conversion")

    prims: dict[str, Any] = {}
    for prim in Usd.PrimRange.Stage(stage, Usd.PrimAllPrimsPredicate):
        attribute = prim.GetAttribute("ifc:globalId")
        if attribute and attribute.HasAuthoredValue() and not prim.IsInPrototype():
            prims.setdefault(str(attribute.Get()), prim)

    extractor = PropertyExtractor(
        UnitResolver(model),
        include_properties=bool(options.get("include_properties", True)),
        include_quantities=bool(options.get("include_quantities", True)),
    )
    inherit = bool(options.get("inherit_type_properties", True))
    missing_objects = 0
    missing_values = 0
    checked_values = 0
    objects = util.by_type(model, "IfcObjectDefinition")
    for entity in objects:
        is_element = util.is_a(entity, "IfcProduct") and not (
            util.is_a(entity, "IfcSpatialElement") or util.is_a(entity, "IfcSpatialStructureElement")
        )
        if is_element and (
            (include_types and not util.is_any(entity, tuple(include_types)))
            or (exclude_types and util.is_any(entity, tuple(exclude_types)))
        ):
            continue
        global_id = util.attr(entity, "GlobalId")
        prim = prims.get(str(global_id)) if global_id else None
        if prim is None:
            missing_objects += 1
            report.add("error", "MissingObject", f"{entity.is_a()} {global_id} (#{entity.id()}) not found in USD")
            continue
        sets = extractor.for_object(entity, inherit_from_type=inherit)
        expected = sum(len(s.values) for s in sets)
        present = len(prim.GetPropertiesInNamespace("ifc:pset")) + len(prim.GetPropertiesInNamespace("ifc:qto"))
        checked_values += expected
        if present < expected:
            missing_values += expected - present
            report.add(
                "error",
                "MissingPropertyValues",
                f"{entity.is_a()} {global_id}: {expected} property/quantity values expected, {present} found",
                str(prim.GetPath()),
            )
    report.stats.update(
        {
            "sourceObjects": len(objects),
            "missingObjects": missing_objects,
            "sourcePropertyValues": checked_values,
            "missingPropertyValues": missing_values,
        }
    )
