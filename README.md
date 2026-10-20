# IfcUSD

[![Tests](https://github.com/isaddiq/ifcusd/actions/workflows/tests.yml/badge.svg)](https://github.com/isaddiq/ifcusd/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.13-blue.svg)](https://www.python.org/)
[![IFC](https://img.shields.io/badge/IFC-2X3%20%7C%204%20%7C%204X3%20%7C%205-green.svg)](https://technical.buildingsmart.org/standards/ifc/)
[![OpenUSD](https://img.shields.io/badge/OpenUSD-24.8%2B-orange.svg)](https://openusd.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

**IfcUSD converts IFC building models to OpenUSD without losing the BIM.**
Geometry, spatial structure, every attribute, property set and quantity, types,
materials, classifications, groups, layers and relationships are written as
typed, documented USD data. The output is reproducible byte for byte and opens
directly in NVIDIA Omniverse, Unity, usdview, Blender and other OpenUSD tools.

```text
IFC2X3 / IFC4 / IFC4X3 / IFC5 ─▶ IfcOpenShell (read + tessellate) ─▶ IfcUSD mapping ─▶ .usd / .usda / .usdc / .usdz
                                                                        └─▶ report.json, metadata.json
```

## Why IfcUSD

| | |
| --- | --- |
| **Complete** | Every `IfcRoot` instance is accounted for in a coverage report (`missing: 0` is checked). Seven property kinds, quantities, types, materials (layer, profile and constituent sets), classifications, documents, groups, systems, zones, layers, owner history and *all* `IfcRel*` relationships are converted. |
| **Typed and lossless** | IFC values keep their types (`bool`, `int`, `double`, `token` with allowed values…), defined types (`IfcLengthMeasure`) and units (`MILLIMETRE`). Original names stay in `displayName`/`displayGroup`. Attribute naming is reversible. |
| **Any IFC version** | IFC2X3, IFC4, IFC4X3_ADD2 natively. IFC4X1, IFC4X2, IFC4X3 (final/TC1/ADD1), IFC4_ADD1/ADD2 and IFC2X3_TC1 identifiers are read with the closest supported schema, and anything that schema cannot represent is reported. IFC5 (`.ifcx`, alpha) is composed with buildingSMART's layering rules. `.ifczip` is supported. |
| **A real USD schema** | A versioned, codeless USD schema (`IfcEntityAPI`, `IfcPropertySetAPI`, `IfcQuantitySetAPI`, `IfcFileAPI`, `IfcGeoreferenceAPI`) documents the mapping and enables schema-aware queries. It is optional at runtime, so files work everywhere. |
| **Correct geometry** | Local-coordinate meshes with double-precision relative transforms (accurate for georeferenced sites), USD instancing for shared type geometry, per-face materials as `GeomSubset`s, axes and alignments as curves. Verified against IfcOpenShell's world-space tessellation to within 5 × 10⁻⁵ m. |
| **Reproducible** | Byte-identical output for any thread count. Every file records the source SHA-256, tool versions and options. `ifcusd validate --source` proves nothing was lost. |
| **Target profiles** | `omniverse` (OmniPBR MDL, Replicator semantic labels, instancing), `unity` (Y-up, importer-safe content, metadata sidecar plus an editor script), `generic`. |

How this compares with IfcOpenShell's USD serializer, Omniverse's HOOPS
converter and glTF-based routes is covered in
[docs/related-work.md](docs/related-work.md). The IFC ↔ USD schema analysis and
the full mapping specification are in
[docs/schema-mapping.md](docs/schema-mapping.md).

## Installation

Requires Python 3.10 or newer. Dependencies (`ifcopenshell`, `usd-core`, `numpy`)
install automatically.

```bash
python -m pip install "git+https://github.com/isaddiq/ifcusd.git"
```

From a clone, for development:

```bash
git clone https://github.com/isaddiq/ifcusd.git
cd ifcusd
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
python -m pip install -e ".[dev]"
```

Inside NVIDIA Omniverse Kit, which already ships `pxr`, install with
`--no-deps` and add `ifcopenshell numpy`.

## Quick start

```bash
ifcusd convert model.ifc model.usdc                         # generic OpenUSD
ifcusd convert model.ifc model.usdc --profile omniverse     # NVIDIA Omniverse
ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
ifcusd convert model.ifc model.usda --validate --report report.json
```

```text
input   model.ifc  [IFC4]
output  model.usdc  [profile: omniverse]
prims 84 | elements 15 | spatial 7 | types 16 | relationships 57
geometry: meshes 14 | instances 0 (prototypes 0) | curves 0 | triangles 1167 | materials 10
data: property sets 20 | properties 43 | quantities 25
coverage of 117 IfcRoot instances: prim 40, native 57, attributes 20, filtered 0, skipped 0, missing 0
completed in 0.31s (complete)
```

```python
from ifcusd import ConversionOptions, convert, validate_usd

result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))
print("\n".join(result.report.summary_lines()))
assert result.report.complete

check = validate_usd("model.usdc", source="model.ifc")   # re-reads the IFC and compares
assert check.ok
```

Reading IFC data back from USD:

```python
from pxr import Usd

stage = Usd.Stage.Open("model.usdc")
for prim in stage.Traverse():
    if prim.GetAttribute("ifc:class").Get() == "IfcWall":
        fire = prim.GetAttribute("ifc:pset:Pset_WallCommon:FireRating")
        print(prim.GetAttribute("ifc:globalId").Get(), fire.Get() if fire else None)
```

## What gets written

```text
/World                                Xform  assembly   IfcFileAPI, IfcGeoreferenceAPI (header, units, CRS)
  My_Project_<guid>                   Xform  assembly   IfcEntityAPI
    Site/Building/Level_1             Xform  group      spatial structure with real placements
      Basic_Wall_<guid>               Xform  component  ifc:* attributes, ifc:pset:*, ifc:qto:*, rel ifc:isTypedBy…
        Body                          Mesh              triangles, normals, extent, material binding / subsets
        Axis                          BasisCurves       guide purpose
        Opening_<guid>                Xform  subcomponent  ifc:parentRelationship = "voids"
  IfcData                             Scope             types, groups (collections), materials, classifications,
                                                        documents, layers, owner history, other relationships
  Looks                               Scope             UsdPreviewSurface (+ OmniPBR) materials from IFC styles
  Prototypes                          Scope             shared meshes for instanceable bodies
```

A property is one typed attribute:

```usda
custom uniform double ifc:qto:Qto_WallBaseQuantities:Length = 5000 (
    customData = { string ifcType = "IfcQuantityLength"; string ifcUnit = "MILLIMETRE"; string ifcValueType = "IfcLengthMeasure" }
    displayGroup = "Qto_WallBaseQuantities"
    displayName = "Length"
)
```

See [docs/schema-mapping.md](docs/schema-mapping.md) for every rule.

## Command reference

| Command | Purpose |
| --- | --- |
| `ifcusd convert IN OUT [options]` | Convert `.ifc`, `.ifczip` or `.ifcx` to `.usd`, `.usda`, `.usdc` or `.usdz` |
| `ifcusd validate FILE.usd [--source IN.ifc] [--json R.json]` | OpenUSD validators, structural checks and, with `--source`, a completeness check against the IFC |
| `ifcusd info IN [--json]` | Schema, header, units, georeferencing and contents of an IFC/IFCX file |
| `ifcusd schema [--print]` | Path of the USD schema plugin (for `PXR_PLUGINPATH_NAME`) |

Main `convert` options (run `ifcusd convert --help` for all):

| Option | Effect |
| --- | --- |
| `--profile generic\|omniverse\|unity` | Target preset ([details](docs/omniverse-and-unity.md)) |
| `--up-axis Y\|Z`, `--meters-per-unit 0.01` | Stage orientation and units (geometry is scaled; properties keep IFC units) |
| `--naming name_guid\|guid\|name` | Prim naming (default `name_guid`, stable across re-exports) |
| `--include-type IfcWall`, `--exclude-type IfcFurniture` | Element filters (subclasses included; spatial structure is kept) |
| `--threads N` | Tessellation threads; output does not depend on it |
| `--no-instancing`, `--no-curves`, `--spaces hidden\|visible\|skip`, `--openings`, `--uvs`, `--world-coords` | Geometry choices |
| `--linear-deflection M`, `--angular-deflection RAD` | Tessellation tolerance |
| `--no-properties`, `--no-quantities`, `--no-materials`, `--no-relationships`, `--no-owner-history`, `--no-type-inheritance`, `--no-metadata` | Leave out data groups (reported as `skipped`) |
| `--all-relationship-prims` | Also write natively mapped relationships as prims (complete relationship graph with GUIDs) |
| `--material-model preview\|preview+mdl` | UsdPreviewSurface only, or with OmniPBR |
| `--layer extra.ifcx` | Additional IFC5 layers composed on top of the input |
| `--report R.json`, `--metadata-json M.json` | Write the conversion report / metadata sidecar |
| `--validate`, `--strict` | Validate after writing; with `--strict`, exit code 1 unless conversion was complete |

Exit codes: `0` success, `1` completed with problems, `2` usage or environment
error, `3` conversion failed.

## Python API

```python
from ifcusd import ConversionOptions, IfcToUsdConverter, convert, validate_usd
import ifcusd.schema

options = ConversionOptions.for_profile(
    "unity",
    meters_per_unit=1.0,
    include_types=("IfcWall", "IfcSlab"),
    progress=lambda stage, fraction: print(stage, fraction),
)
result = IfcToUsdConverter(options).convert(
    "model.ifc", "model.usdz", report_path="report.json", metadata_json_path="model.ifcusd.json"
)
result.report.coverage          # per IFC class: inFile / prim / native / attributes / filtered / skipped / missing
result.report.geometry_errors   # products whose geometry failed
result.report.products_without_geometry

convert("hello-wall.ifcx", "hello-wall.usda", layers=["hello-wall-add-fire-rating-30.ifcx"])  # IFC5 layers

ifcusd.schema.register()        # before opening stages, for HasAPI("IfcEntityAPI") etc.
```

## Reproducibility and verification

* Same input + options + tool versions ⇒ byte-identical `.usda`/`.usdc`, for any
  thread count (covered by tests).
* `customLayerData.ifcusd` records the source SHA-256, IFC schema, IfcUSD,
  IfcOpenShell and OpenUSD versions, the mapping version and all options.
* `ifcusd validate out.usd --source model.ifc` checks that every object
  definition (by GlobalId) and every property and quantity value of the source
  is present, in addition to OpenUSD's validators and mesh and relationship
  integrity checks.

## Performance

On a laptop-class Windows machine with IfcOpenShell 0.9 and OpenUSD 26.08: the
bundled samples convert in under a second; the 5.5 MB *IFC Hero Model* in about
9 s (tessellation-bound); a generated model with 3,000 walls and 36,000
property and quantity values in 5.4 s. Semantic data is written at about
1 ms per element, so models with 100,000 elements convert in a few minutes
plus tessellation time. Tessellation runs on all cores by default.

## Limitations

* USD receives tessellated geometry. Parametric IFC geometry is not
  round-tripped; the output links back to the source by checksum, GlobalId and
  STEP id.
* Geometry IfcOpenShell cannot tessellate (e.g. some IFC4X3 alignment curve
  types) is listed in the report, not converted; the product's data is still
  written.
* IFC presentation styles are mapped to PBR approximately; the original values
  are kept on each material.
* ifcXML input is not supported by current IfcOpenShell releases.

Details in [docs/schema-mapping.md § 7](docs/schema-mapping.md#7-known-limitations).

## Documentation

* [IFC and OpenUSD: schema comparison and mapping specification](docs/schema-mapping.md)
* [Existing IFC → USD methods and comparison](docs/related-work.md)
* [Using the output in NVIDIA Omniverse and Unity](docs/omniverse-and-unity.md)
* [Changelog](CHANGELOG.md)

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q          # 89 tests: IFC2X3/IFC4/IFC4X3 fixtures, IFC5, CLI, determinism, geometry accuracy
python -m ruff check . && python -m ruff format --check .
```

```text
src/ifcusd/
  converter.py      orchestration, report and coverage
  ifc/              reading IFC: schema fallback, units, values, properties, styles, structure planning, tessellation
  ifcx/             IFC5: layer composition and conversion
  usd/              Sdf-level authoring: scene, geometry, materials
  schema/           codeless USD schema plugin (plugInfo.json, generatedSchema.usda, schema.usda)
  validate.py       validation and completeness checks
  cli.py            command line
integrations/unity/ Unity editor script and runtime component for the metadata sidecar
tests/              pytest suite (models are generated with IfcOpenShell's API)
ifc/                sample IFC files
```

## Upgrading from 0.1

* The root prim is now `/World` (use `--root IFC` for the old name).
* `metersPerUnit` now describes the geometry. 0.1 wrote the IFC length unit
  (e.g. 0.001 for millimetre projects) while the geometry was in metres, so
  models appeared 1,000 times too small.
* Meshes are in local coordinates under relative transforms, instead of baked
  world coordinates (`--world-coords` restores the old layout).
* The `ifc:metadata` JSON blob is replaced by typed attributes and USD
  relationships. Property and quantity names keep the `ifc:pset:<Set>:<Prop>` /
  `ifc:qto:<Set>:<Qty>` pattern.
* Missing dependencies are no longer pip-installed automatically; pass
  `--install-deps` to opt in. The 0.1 flags `--local-coords`,
  `--fail-on-geometry-error` and `--no-auto-install` are still accepted.

## Citation

If you use IfcUSD in research, please cite it using [CITATION.cff](CITATION.cff).

## License

MIT, see [LICENSE](LICENSE).
