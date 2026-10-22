# Python API

Everything the command line does is available from Python. The public API is
small and importable from the top-level package:

```python
from ifcusd import (
    ConversionOptions,     # all settings, with target presets
    convert,               # one-call conversion (alias: convert_file)
    IfcToUsdConverter,     # reusable converter
    ConversionResult,      # what convert() returns
    ConversionReport,      # counts, coverage, warnings, provenance
    validate_usd,          # validation and completeness check
    convert_usd_to_ifc,    # USD back to IFC (merge into a source, or rebuild)
    IfcExportOptions,      # settings of the USD to IFC direction
    IfcUsdError,           # base class of all IfcUSD errors
)
```

Importing `ifcusd` is cheap; IfcOpenShell and OpenUSD load on first use.
The full signatures are in the [API reference](../reference/api.md).

## Converting

```python
from ifcusd import ConversionOptions, convert

result = convert("model.ifc", "model.usdc")                                       # defaults
result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("unity"))
result = convert(
    "model.ifc",
    "model.usdc",
    ConversionOptions(threads=8),
    report_path="model.report.json",          # also write the JSON report
    metadata_json_path="model.ifcusd.json",   # also write the metadata sidecar
)
```

To convert many files with the same settings, create one converter:

```python
from pathlib import Path
from ifcusd import ConversionOptions, IfcToUsdConverter

converter = IfcToUsdConverter(ConversionOptions.for_profile("omniverse"))
for ifc in sorted(Path("models").glob("*.ifc")):
    converter.convert(ifc, Path("usd") / f"{ifc.stem}.usdc")
```

## Converting USD back to IFC

`convert_usd_to_ifc` writes a USD stage back to IFC. With `source=` the
original IFC file is updated with what changed or was added in USD; without it
the model is rebuilt from USD. See [USD to IFC and robot data](usd-to-ifc.md).

```python
from ifcusd import IfcExportOptions, UsdToIfcConverter, convert_usd_to_ifc

result = convert_usd_to_ifc(
    "model.usdc",
    "model.trained.ifc",
    source="model.ifc",
    layers=["robot_run_01.usda"],      # composed on top of model.usdc
    report_path="export.json",
    validate=True,
)
report = result.report
print(report.counts["newObjects"], report.counts["propertySetsCreated"])
for item in report.created_objects:
    print(item["ifcClass"], item["globalId"], item["usdPath"])
model = result.model                    # the ifcopenshell.file that was written

# Several runs against the same source, latest state of each:
converter = UsdToIfcConverter(IfcExportOptions(time="latest", extension_namespaces=("robot", "sim", "nav")))
for run in ("run_01", "run_02"):
    converter.convert("model.usdc", f"model.{run}.ifc", source="model.ifc", layers=[f"{run}.usda"])
```

## Options

`ConversionOptions` is an immutable dataclass. `for_profile()` starts from a
target preset; keyword arguments override individual fields.

```python
options = ConversionOptions.for_profile("omniverse", meters_per_unit=0.01, threads=8)
walls = options.with_changes(include_types=("IfcWall", "IfcCurtainWall"))
print(options.to_dict())          # serializable view (also stored in each file)
```

| Field | Default | Meaning |
| --- | --- | --- |
| `profile` | `"generic"` | Preset the options came from |
| `up_axis` | `"Z"` | `"Z"` or `"Y"` |
| `meters_per_unit` | `1.0` | Stage unit in metres |
| `root_prim_name` | `"World"` | Root/default prim name |
| `naming` | `"name_guid"` | `"name_guid"`, `"guid"` or `"name"` |
| `include_types` / `exclude_types` | `()` | Element class filters (subclasses included) |
| `threads` | CPU count − 1 | Tessellation threads |
| `use_world_coords` | `False` | Bake transforms into points |
| `instancing` | `True` | Instanceable prototypes for shared geometry |
| `include_curves` | `True` | Curve representations as `BasisCurves` |
| `include_openings` | `False` | Tessellate openings (hidden) |
| `spaces` | `"hidden"` | `"hidden"`, `"visible"` or `"skip"` |
| `generate_uvs` | `False` | Box-projected UVs (automatic with textures) |
| `double_sided` | `True` | Double-sided meshes |
| `linear_deflection` / `angular_deflection` | `None` | Tessellation tolerances (metres / radians) |
| `include_metadata` | `True` | Write IFC data at all |
| `include_properties` / `include_quantities` | `True` | Property / quantity sets |
| `include_materials` | `True` | IFC material definitions |
| `include_relationships` | `True` | Relationship prims, classifications, documents, layers… |
| `relationship_prims` | `"generic"` | `"all"` also writes natively mapped relationships as prims |
| `include_owner_history` | `True` | `IfcOwnerHistory` records |
| `inherit_type_properties` | `True` | Copy type property sets onto occurrences |
| `semantic_labels` | `True` | `SemanticsLabelsAPI` class labels |
| `legacy_semantics` | `False` | Legacy Omniverse `SemanticsAPI` labels |
| `physics` | `True` | Re-apply `IfcUSD_Physics` as `UsdPhysics` schemas |
| `material_model` | `"preview"` | `"preview"` or `"preview+mdl"` |
| `ignore_geometry_errors` | `True` | Continue past elements whose geometry fails |
| `schema_fallback` | `True` | Read unknown schema identifiers with the closest supported schema |
| `progress` | `None` | Callback `progress(stage: str, fraction: float)` |

Profile presets:

| Field | `generic` | `omniverse` | `unity` |
| --- | --- | --- | --- |
| `up_axis` | `Z` | `Z` | `Y` |
| `instancing` | on | on | off |
| `include_curves` | on | on | off |
| `spaces` | hidden | hidden | skip |
| `material_model` | preview | preview+mdl | preview |
| `semantic_labels` | on | on | off |
| `legacy_semantics` | off | on | off |
| `physics` | on | on | off |

Invalid values raise `ValueError` when the options are created.

## Results and reports

`convert()` returns a `ConversionResult`:

```python
result.input_path, result.output_path
result.elements_seen           # products that received geometry
result.meshes_written          # inline meshes + instances
result.skipped_geometry        # ["IfcBeam #123: reason", ...]
report = result.report         # ConversionReport
```

The report is the authoritative record of the conversion:

```python
report.complete                      # nothing dropped, failed or missing
report.counts["properties"]          # prims, meshes, instances, triangles, propertySets, ...
report.coverage["IfcWall"]           # {"inFile": 4, "prim": 4}
report.coverage_totals()             # {"inFile": 117, "prim": 40, "native": 57, ...}
report.products_without_geometry     # with IfcOpenShell diagnostics
report.geometry_errors
report.dropped_entities              # when a compatibility schema was used
report.warnings
report.timings                       # seconds per phase
report.to_json("report.json")
print("\n".join(report.summary_lines()))
```

## Progress

```python
def show(stage: str, fraction: float) -> None:
    print(f"\r{stage:<10} {fraction:6.1%}", end="", flush=True)

convert("big.ifc", "big.usdc", ConversionOptions(progress=show))
```

Stages are `read`, `semantics`, `geometry` (with fractions), `write` and `done`.

!!! note
    While geometry is processed, IfcUSD redirects the process's standard
    output at file-descriptor level to silence IfcOpenShell's native
    diagnostics. Print progress to `sys.stderr` if you need it visible during
    that phase.

## Validation

```python
from ifcusd import validate_usd

check = validate_usd("model.usdc", source="model.ifc")
check.ok                        # False if any error
check.errors, check.warnings    # lists of ValidationIssue(severity, code, message, path)
check.stats                     # meshes, instances, sourceObjects, missingPropertyValues, ...
check.to_json("validation.json")
```

## Errors

All exceptions derive from `ifcusd.IfcUsdError`:

| Exception | Raised when |
| --- | --- |
| `InputError` | the input is missing, unreadable or of an unsupported format |
| `UnsupportedSchemaError` | the IFC schema cannot be read (subclass of `InputError`) |
| `OutputError` | the output extension is unsupported or the file cannot be written |
| `GeometryError` | geometry failed and `ignore_geometry_errors=False` |
| `MissingUsdDependencyError` | `pxr` (OpenUSD) is not importable |

```python
from ifcusd import InputError, convert

try:
    convert("model.ifc", "model.usdc")
except InputError as error:
    print(f"cannot read the model: {error}")
```

## The USD schema

```python
import ifcusd.schema

ifcusd.schema.plugin_path()     # folder with plugInfo.json
ifcusd.schema.register()        # register with OpenUSD (before opening stages)
ifcusd.schema.ENTITY_API        # "IfcEntityAPI"
ifcusd.SCHEMA_VERSION           # "1.0"
```
