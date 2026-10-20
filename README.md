<p align="center">
  <img src="https://raw.githubusercontent.com/isaddiq/ifcusd/main/website/docs/assets/logo.svg" alt="IfcUSD logo" width="96">
</p>

<h1 align="center">IfcUSD</h1>

<p align="center">
  <strong>Convert IFC building models to OpenUSD without losing the BIM.</strong><br>
  Geometry, spatial structure, every attribute, property and quantity, types, materials,<br>
  classifications, groups and relationships, written as typed, documented USD data.
</p>

<p align="center">
  <a href="https://github.com/isaddiq/ifcusd/actions/workflows/tests.yml"><img src="https://github.com/isaddiq/ifcusd/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://isaddiq.github.io/ifcusd/"><img src="https://github.com/isaddiq/ifcusd/actions/workflows/docs.yml/badge.svg" alt="Website"></a>
  <img src="https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue.svg" alt="Python 3.10 – 3.14">
  <img src="https://img.shields.io/badge/IFC-2X3%20%7C%204%20%7C%204X3%20%7C%205-green.svg" alt="IFC 2X3, 4, 4X3, 5">
  <img src="https://img.shields.io/badge/OpenUSD-24.8%2B-orange.svg" alt="OpenUSD 24.8+">
  <a href="https://github.com/isaddiq/ifcusd/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="MIT license"></a>
</p>

<p align="center">
  <a href="https://isaddiq.github.io/ifcusd/"><b>Documentation</b></a> ·
  <a href="https://isaddiq.github.io/ifcusd/getting-started/quickstart/">Quick start</a> ·
  <a href="https://isaddiq.github.io/ifcusd/getting-started/tutorial/">Tutorial</a> ·
  <a href="https://isaddiq.github.io/ifcusd/reference/schema-mapping/">IFC ↔ USD mapping</a> ·
  <a href="https://isaddiq.github.io/ifcusd/about/changelog/">Changelog</a>
</p>

---

IfcUSD is a Python package and command-line tool for researchers and
practitioners who need building models in NVIDIA Omniverse, Unity or any
OpenUSD pipeline, *with* their building information. It reads every IFC
version through [IfcOpenShell](https://ifcopenshell.org/), writes OpenUSD
through Pixar's `usd-core`, and proves that nothing was lost.

```text
IFC2X3 · IFC4 · IFC4X3 · IFC5 ──▶ IfcUSD ──▶ .usd · .usda · .usdc · .usdz   (+ report.json, metadata.json)
```

## Highlights

- **Complete.** All attributes; every property kind (single, enumerated, bounded, list, table,
  reference, complex); quantities with units; types; material layer, profile and constituent
  sets; classifications, documents, groups, systems, zones and layers; and *all* `IfcRel*`
  relationships. A coverage report accounts for every `IfcRoot` instance (`missing: 0`).
- **Typed and reversible.** Values keep their IFC types, defined types and units. Original
  names are kept in `displayName`, and attribute naming is invertible.
- **Any IFC version.** IFC2X3, IFC4 and IFC4X3 natively. IFC4X1, IFC4X2, IFC4X3-TC1/ADD1,
  IFC4-ADD2 and similar identifiers are read with the closest supported schema, with any
  losses reported. IFC5 (`.ifcx`) with layer composition, and `.ifczip`.
- **A real USD schema.** A versioned, codeless USD schema (`IfcEntityAPI`,
  `IfcPropertySetAPI`, `IfcQuantitySetAPI`, …) that is optional at runtime.
- **Accurate geometry.** Local meshes under double-precision transforms, instancing for shared
  type geometry, per-face materials, axes and alignments as curves. Verified against
  IfcOpenShell to 5 × 10⁻⁵ m.
- **Reproducible.** Byte-identical output for any thread count, with provenance (source
  SHA-256, versions, options) in every file. `ifcusd validate --source` re-checks the IFC.
- **Ready for your target.** Profiles for NVIDIA Omniverse (OmniPBR, Replicator semantic
  labels), Unity 6 (Y-up, importer-safe, metadata sidecar plus editor script) and generic
  OpenUSD tools.

## Install

```bash
python -m pip install "git+https://github.com/isaddiq/ifcusd.git"
```

Python 3.10 – 3.14 on Windows, macOS or Linux. The dependencies (`ifcopenshell`, `usd-core`,
`numpy`) install automatically as binary wheels. See
[Installation](https://isaddiq.github.io/ifcusd/getting-started/installation/) for virtual
environments, NVIDIA Omniverse Kit and the optional USD schema plugin.

## Quick start

```bash
ifcusd convert model.ifc model.usdc                          # any OpenUSD tool
ifcusd convert model.ifc model.usdc --profile omniverse      # NVIDIA Omniverse
ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
ifcusd convert model.ifc model.usdc --validate --report report.json
```

```text
input   model.ifc  [IFC4]
output  model.usdc  [profile: generic]
prims 84 | elements 15 | spatial 7 | types 16 | relationships 57
geometry: meshes 14 | instances 0 (prototypes 0) | curves 0 | triangles 1167 | materials 10
data: property sets 20 | properties 43 | quantities 25
coverage of 117 IfcRoot instances: prim 40, native 57, attributes 20, filtered 0, skipped 0, missing 0
completed in 0.47s (complete)
model.usdc: OK (0 errors, 0 warnings, 0 notes)
```

From Python:

```python
from ifcusd import ConversionOptions, convert, validate_usd

result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))
assert result.report.complete
assert validate_usd("model.usdc", source="model.ifc").ok
```

Reading the IFC data back with OpenUSD:

```python
from pxr import Usd

stage = Usd.Stage.Open("model.usdc")
for prim in stage.Traverse():
    if prim.GetAttribute("ifc:class").Get() == "IfcWall":
        print(prim.GetAttribute("ifc:globalId").Get(),
              prim.GetAttribute("ifc:pset:Pset_WallCommon:IsExternal").Get(),
              prim.GetAttribute("ifc:qto:Qto_WallBaseQuantities:Length").Get())
```

## What gets written

```text
/World                                Xform      IfcFileAPI, IfcGeoreferenceAPI (header, units, CRS)
  <Project>/<Site>/<Building>/<Storey>  Xform    spatial structure with real placements
    <Element>_<GlobalId>              Xform      ifc:* attributes, ifc:pset:*, ifc:qto:*, rel ifc:isTypedBy …
      Body                            Mesh       triangles, normals, materials (or an instance of a prototype)
      Axis                            BasisCurves
  IfcData                             Scope      types, groups (collections), materials, classifications,
                                                 documents, layers, owner history, other relationships
  Looks                               Scope      UsdPreviewSurface (+ OmniPBR) from IFC surface styles
  Prototypes                          Scope      shared meshes for instancing
```

The full rules, including a side-by-side comparison of the IFC and OpenUSD data models, are in
the [IFC ↔ USD mapping specification](https://isaddiq.github.io/ifcusd/reference/schema-mapping/).

## Compatibility

| | Supported | Tested |
| --- | --- | --- |
| Python | 3.10 – 3.14 | 3.13 and 3.14 locally; the CI workflow runs 3.10 – 3.14 on Linux and Windows |
| IFC | IFC2X3, IFC4, IFC4X3 (+ fallback for other identifiers), IFC5 alpha | buildingSMART samples and generated models in all three main schemas |
| IfcOpenShell | ≥ 0.8.0 | 0.8.4, 0.9.0 |
| OpenUSD (`usd-core`) | ≥ 24.8 | 25.8, 26.8 |
| Targets | NVIDIA Omniverse, Unity 6 (USD Importer), usdview, Blender, any OpenUSD tool | USD validators, geometry and completeness checks |

## Documentation

The website at **<https://isaddiq.github.io/ifcusd/>** covers:

- [Installation](https://isaddiq.github.io/ifcusd/getting-started/installation/) ·
  [Quick start](https://isaddiq.github.io/ifcusd/getting-started/quickstart/) ·
  [Tutorial](https://isaddiq.github.io/ifcusd/getting-started/tutorial/)
- [Command line](https://isaddiq.github.io/ifcusd/guide/cli/) ·
  [Python API](https://isaddiq.github.io/ifcusd/guide/python-api/) ·
  [Recipes](https://isaddiq.github.io/ifcusd/guide/recipes/) ·
  [Troubleshooting](https://isaddiq.github.io/ifcusd/guide/troubleshooting/)
- [Omniverse and Unity](https://isaddiq.github.io/ifcusd/guide/omniverse-and-unity/) ·
  [Reports and validation](https://isaddiq.github.io/ifcusd/guide/validation/) ·
  [IFC5](https://isaddiq.github.io/ifcusd/guide/ifc5/)
- [IFC ↔ USD mapping](https://isaddiq.github.io/ifcusd/reference/schema-mapping/) ·
  [USD schema](https://isaddiq.github.io/ifcusd/reference/usd-schema/) ·
  [File formats](https://isaddiq.github.io/ifcusd/reference/file-formats/) ·
  [API reference](https://isaddiq.github.io/ifcusd/reference/api/)
- [How IfcUSD compares with existing IFC → USD methods](https://isaddiq.github.io/ifcusd/about/related-work/)

## Repository layout

```text
src/ifcusd/            the Python package (the only code pip installs)
tests/                 test suite; IFC models are generated with IfcOpenShell's API
ifc/                   sample IFC files
integrations/unity/    Unity editor script and component for the metadata sidecar
website/               the documentation website (static site, MkDocs Material); not part of the package
.github/workflows/     CI: tests, website deployment to GitHub Pages, PyPI release
```

## Development

```bash
git clone https://github.com/isaddiq/ifcusd.git
cd ifcusd
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check . && python -m ruff format --check .
```

Contribution guidelines, the architecture overview and how to build the website are on the
[Contributing](https://isaddiq.github.io/ifcusd/about/contributing/) page.

## Upgrading from 0.1

- The root prim is now `/World` (`--root IFC` keeps the old name).
- `metersPerUnit` now matches the geometry. 0.1 wrote the IFC length unit (for example 0.001)
  while geometry was in metres, so models appeared 1,000 times too small. Re-convert old files.
- Meshes are local, under transforms; `--world-coords` restores baked coordinates.
- The `ifc:metadata` JSON blob is replaced by typed attributes and relationships; property names
  keep the `ifc:pset:<Set>:<Prop>` pattern.
- Dependencies are no longer pip-installed automatically (`--install-deps` opts in). The 0.1
  flags `--local-coords`, `--fail-on-geometry-error` and `--no-auto-install` are still accepted.

## Citation and license

If IfcUSD supports your research, please cite it with [`CITATION.cff`](https://github.com/isaddiq/ifcusd/blob/main/CITATION.cff) (GitHub's
*Cite this repository* button). IfcUSD is released under the [MIT License](https://github.com/isaddiq/ifcusd/blob/main/LICENSE).
