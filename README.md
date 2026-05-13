# IfcUSD

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![IfcOpenShell](https://img.shields.io/badge/IFC-IfcOpenShell-green.svg)](https://ifcopenshell.org/)
[![OpenUSD](https://img.shields.io/badge/USD-OpenUSD-orange.svg)](https://openusd.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#license)

IfcUSD is a Python converter that turns IFC building models into OpenUSD files. It uses
[IfcOpenShell](https://ifcopenshell.org/) to read and tessellate IFC geometry, then writes
`.usd`, `.usda`, or `.usdc` files through Pixar's OpenUSD Python bindings.

The converter is designed for BIM-to-USD interchange: it writes mesh geometry for USD tools
while preserving IFC identity, hierarchy, materials, properties, quantities, and relationship
metadata.

```text
IFC model  ->  IfcOpenShell geometry + metadata  ->  OpenUSD stage
```

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Line Usage](#command-line-usage)
- [Python API](#python-api)
- [What Gets Written](#what-gets-written)
- [Sample IFC Files](#sample-ifc-files)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Features

| Area          | What IfcUSD writes                                                                           |
| ------------- | -------------------------------------------------------------------------------------------- |
| Geometry      | Tessellated IFC products as `UsdGeom.Mesh` prims                                             |
| Hierarchy     | IFC spatial hierarchy as USD `Xform` prims under `/IFC`                                      |
| Materials     | USD Preview Surface materials, diffuse/specular colors, opacity, and face subsets            |
| Textures      | External texture references from IFC surface-style texture data when IfcOpenShell exposes it |
| Metadata      | STEP id, IFC class, GlobalId, Name, ObjectType, Tag, PredefinedType, and more                |
| Properties    | IFC property sets and quantity takeoffs as USD attributes                                    |
| Relationships | Spatial containers, aggregate parents, types, groups, layers, openings, and references       |
| Units         | USD stage unit metadata from the IFC unit assignment                                         |

IfcUSD is geometry-first. It preserves BIM metadata, but it writes tessellated meshes rather
than native parametric IFC solids.

## Requirements

- Python 3.10 or newer
- `ifcopenshell>=0.8.0`
- `usd-core>=24.8`

The package declares these runtime dependencies in `pyproject.toml`. The CLI also checks for
them before conversion and can install missing runtime packages automatically unless
`--no-auto-install` is used.

## Installation

### Option 1: Install from GitHub

Install directly from the GitHub repository with `pip`.

```powershell
python -m pip install -U pip
python -m pip install "git+https://github.com/isaddiq/ifcusd.git"
```

### Option 2: Install from a local clone

Use this workflow for development or for running the converter from a downloaded copy.

```powershell
git clone https://github.com/isaddiq/ifcusd.git
cd ifcusd

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -U pip
python -m pip install -e .
```

On macOS or Linux, activate the virtual environment with:

```bash
source .venv/bin/activate
```

Then install the project:

```bash
python -m pip install -U pip
python -m pip install -e .
```

### Option 3: Run without installing

From the repository root, use the helper script:

```powershell
python .\convert_ifc_to_usd.py .\ifc\Architecture.ifc .\ifc\Architecture.usd
```

The helper script adds `src` to Python's import path, checks runtime dependencies, and runs
the same conversion workflow as the installed `ifcusd` command.

## Quick Start

Convert the included architectural sample:

```powershell
ifcusd convert .\ifc\Architecture.ifc .\ifc\Architecture.usd --threads 1 --verbose
```

Expected output:

```text
converted 14/14 meshed elements to ifc\Architecture.usd
```

Create a human-readable USD ASCII file:

```powershell
ifcusd convert .\ifc\Architecture.ifc .\ifc\Architecture.usda
```

Create a binary USD crate file:

```powershell
ifcusd convert .\ifc\Architecture.ifc .\ifc\Architecture.usdc
```

## Command Line Usage

```text
ifcusd convert INPUT.ifc OUTPUT.usd [options]
```

Examples:

```powershell
ifcusd convert .\ifc\Architecture.ifc .\ifc\Architecture.usd
ifcusd convert .\ifc\Architecture.ifc .\ifc\Architecture.usda --threads 8 --verbose
ifcusd convert .\ifc\Architecture.ifc .\ifc\WallsOnly.usdc --include-type IfcWall
ifcusd convert .\model.ifc .\model.usd --exclude-type IfcSpace --exclude-type IfcOpeningElement
```

Output formats:

| Extension | Use case                                                              |
| --------- | --------------------------------------------------------------------- |
| `.usd`    | Standard USD file extension                                           |
| `.usda`   | ASCII USD, useful for debugging and metadata inspection               |
| `.usdc`   | Binary USD crate, usually smaller and faster for production workflows |

CLI options:

| Option                     | Description                                                               |
| -------------------------- | ------------------------------------------------------------------------- |
| `--threads N`              | Number of IfcOpenShell geometry threads. Defaults to CPU count minus one. |
| `--verbose`                | Print dependency install commands and skipped geometry/write details.     |
| `--include-type IfcWall`   | Convert only a specific IFC class. Can be passed more than once.          |
| `--exclude-type IfcSpace`  | Skip a specific IFC class. Can be passed more than once.                  |
| `--no-metadata`            | Do not write IFC metadata attributes.                                     |
| `--no-properties`          | Do not write IFC property sets.                                           |
| `--no-quantities`          | Do not write IFC quantity takeoffs.                                       |
| `--no-materials`           | Do not write material metadata.                                           |
| `--local-coords`           | Use local tessellation coordinates instead of world coordinates.          |
| `--up-axis Y`              | Write the USD stage with Y-up. The default is Z-up.                       |
| `--fail-on-geometry-error` | Stop at the first geometry or USD write error.                            |
| `--no-auto-install`        | Do not install missing runtime dependencies automatically.                |

Verify the installed command:

```powershell
ifcusd --help
ifcusd convert --help
```

## Python API

Basic conversion:

```python
from ifcusd import ConversionOptions, convert_file

result = convert_file(
    "ifc/Architecture.ifc",
    "ifc/Architecture.usd",
    ConversionOptions(threads=4),
)

print("input:", result.input_path)
print("output:", result.output_path)
print("meshes:", result.meshes_written)
print("elements:", result.elements_seen)
print("spatial prims:", result.spatial_prims_written)
print("skipped:", len(result.skipped_geometry))
```

Convert only selected IFC classes:

```python
from ifcusd import ConversionOptions, convert_file

options = ConversionOptions(include_types=("IfcWall", "IfcDoor"))
convert_file("model.ifc", "walls_and_doors.usd", options)
```

Disable selected metadata groups:

```python
from ifcusd import ConversionOptions, convert_file

options = ConversionOptions(
    include_properties=False,
    include_quantities=False,
    include_materials=True,
)
convert_file("model.ifc", "geometry_with_materials.usd", options)
```

## What Gets Written

IfcUSD creates a USD stage with `/IFC` as the default prim.

The conversion workflow is:

1. Open the IFC file with IfcOpenShell.
2. Read the IFC unit assignment and configure USD meters-per-unit.
3. Build stable USD paths from IFC hierarchy, names, STEP ids, and GlobalIds.
4. Write spatial hierarchy prims for projects, sites, buildings, storeys, spaces, and facilities.
5. Tessellate selected IFC products with IfcOpenShell.
6. Write `UsdGeom.Mesh` prims, normals, UVs, materials, material subsets, and texture references.
7. Write IFC identity, property, quantity, material, style, and relationship metadata.
8. Save the requested `.usd`, `.usda`, or `.usdc` file.

The USD stage and root `/IFC` prim include:

- `/IFC` as the default prim
- Stage meters-per-unit and up-axis settings
- Root `/IFC` metadata attributes for source IFC file path, IFC schema, and meters-per-unit value

Entity metadata can include:

- IFC class and STEP id
- GlobalId, Name, Description, ObjectType, Tag, PredefinedType, LongName, and Phase
- Property sets and quantity takeoffs
- Type references and material references
- Spatial container and aggregate parent relationships
- Groups, presentation layers, referenced structures, openings, and external references

## Sample IFC Files

The repository includes sample IFC files in the `ifc` folder:

| File                   | Purpose                               |
| ---------------------- | ------------------------------------- |
| `ifc/Architecture.ifc` | Small architectural conversion sample |
| `ifc/Structural.ifc`   | Structural model sample               |
| `ifc/example.ifc`      | Additional test/example IFC file      |

After converting a sample, verify that OpenUSD can read the result:

```powershell
@'
from pxr import Usd

stage = Usd.Stage.Open("ifc/Architecture.usd")
if stage is None:
    raise SystemExit("Could not open USD stage")

mesh_count = sum(1 for prim in stage.Traverse() if prim.GetTypeName() == "Mesh")
metadata_count = sum(1 for prim in stage.Traverse() if prim.HasAttribute("ifc:metadata"))
print(f"meshes={mesh_count} metadata_prims={metadata_count}")
'@ | python -
```

macOS and Linux shell form:

```bash
python - <<'PY'
from pxr import Usd

stage = Usd.Stage.Open("ifc/Architecture.usd")
if stage is None:
    raise SystemExit("Could not open USD stage")

mesh_count = sum(1 for prim in stage.Traverse() if prim.GetTypeName() == "Mesh")
metadata_count = sum(1 for prim in stage.Traverse() if prim.HasAttribute("ifc:metadata"))
print(f"meshes={mesh_count} metadata_prims={metadata_count}")
PY
```

## Development

Install development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Run checks:

```powershell
python -m pytest -q
python -m ruff check .
python -m compileall src tests
```

Project layout:

```text
ifcusd/
+-- convert_ifc_to_usd.py      # Run-from-clone helper
+-- ifc/                       # Sample IFC files
+-- pyproject.toml             # Package metadata and dependencies
+-- src/ifcusd/                # Converter package
+-- tests/                     # Pytest test suite
```

## Troubleshooting

### `ifcusd` is not recognized

Install the package in the active Python environment:

```powershell
python -m pip install -e .
```

Then activate the virtual environment or open a new terminal.

### `ModuleNotFoundError: No module named 'pxr'`

The `pxr` modules come from `usd-core`. The converter installs this automatically before
conversion unless `--no-auto-install` is passed.

Manual install:

```powershell
python -m pip install "usd-core>=24.8"
```

### `ModuleNotFoundError: No module named 'ifcopenshell'`

Manual install:

```powershell
python -m pip install "ifcopenshell>=0.8.0"
```

### Automatic dependency installation fails

Run the printed `pip install` command manually in the same Python environment, then retry
the conversion. If the environment is locked down, use `--no-auto-install` and install
dependencies through your normal package management workflow.

### Some IFC elements are skipped

Some source models contain geometry that IfcOpenShell cannot tessellate. By default, IfcUSD
continues converting the rest of the model and reports skipped items in the summary.

Use:

```powershell
ifcusd convert .\model.ifc .\model.usd --verbose
```

To fail immediately on the first geometry/write problem:

```powershell
ifcusd convert .\model.ifc .\model.usd --fail-on-geometry-error
```

### The USD output is mesh geometry, not parametric BIM solids

That is expected. IfcUSD writes tessellated USD mesh geometry plus IFC metadata. It does not
write native parametric IFC solids.

## License

This project is configured as MIT licensed in `pyproject.toml`.
