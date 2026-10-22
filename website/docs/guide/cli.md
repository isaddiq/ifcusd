# Command line

The `ifcusd` command has five subcommands:

| Command | Purpose |
| --- | --- |
| [`ifcusd convert`](#ifcusd-convert) | Convert `.ifc`, `.ifczip` or `.ifcx` to `.usd`, `.usda`, `.usdc` or `.usdz` |
| [`ifcusd to-ifc`](#ifcusd-to-ifc) | Convert USD back to `.ifc`, with the changes and robot data added in USD |
| [`ifcusd validate`](#ifcusd-validate) | Validate a converted file, optionally against its IFC source |
| [`ifcusd info`](#ifcusd-info) | Summarize an IFC or IFCX file |
| [`ifcusd schema`](#ifcusd-schema) | Locate or print the IfcUSD USD schema plugin |

`ifcusd --version` prints the package and mapping versions. `python -m ifcusd`
works the same as `ifcusd`. From a source checkout without installing, use
`python convert_ifc_to_usd.py <input> <output> [options]`.

## `ifcusd convert`

```text
ifcusd convert INPUT OUTPUT [options]
```

| Argument | Description |
| --- | --- |
| `INPUT` | `.ifc`, `.ifczip` or `.ifcx` file |
| `OUTPUT` | `.usd`, `.usda`, `.usdc` or `.usdz` file; folders are created |

### Target and scene

| Option | Default | Description |
| --- | --- | --- |
| `--profile {generic,omniverse,unity}` | `generic` | Target preset; see [Omniverse and Unity](omniverse-and-unity.md) |
| `--up-axis {Y,Z}` | `Z` (`Y` for unity) | Stage up axis. IFC is Z-up; `Y` adds one `rotateX -90` on the root. |
| `--meters-per-unit M` | `1` | Stage unit in metres (`0.01` = centimetres). Geometry is scaled; property values keep IFC units. |
| `--root NAME` | `World` | Name of the root/default prim |
| `--naming {name_guid,guid,name}` | `name_guid` | Prim naming. `name_guid` is stable across re-exports. |
| `--layer FILE` | | Extra IFC5 layer composed on top of an `.ifcx` input (repeatable, in order) |

### Element selection

| Option | Description |
| --- | --- |
| `--include-type IfcWall` | Only convert this class and its subclasses (repeatable) |
| `--exclude-type IfcFurniture` | Skip this class and its subclasses (repeatable) |

Spatial structure (project, sites, buildings, storeys, spaces) is always
kept, so filtered elements still sit in their real context. Excluded
instances are counted as `filtered` in the report, not `missing`.

### Geometry

| Option | Default | Description |
| --- | --- | --- |
| `--threads N` | CPU count − 1 | Tessellation threads. Output is identical for any value. |
| `--no-instancing` | instancing on (off for unity) | Write every mesh inline instead of sharing prototypes |
| `--no-curves` | curves on (off for unity) | Skip axis, alignment and annotation curves |
| `--spaces {hidden,visible,skip}` | `hidden` (`skip` for unity) | Geometry of spaces and zones |
| `--openings` | off | Also tessellate opening elements (written hidden) |
| `--uvs` | automatic for textured models | Generate box-projected UVs |
| `--single-sided` | double-sided | Author single-sided meshes |
| `--world-coords` | local + transforms | Bake transforms into points (disables instancing) |
| `--linear-deflection M` | IfcOpenShell default | Chord tolerance in metres. Smaller is finer. |
| `--angular-deflection RAD` | IfcOpenShell default | Angular tolerance in radians |

### IFC data

| Option | Description |
| --- | --- |
| `--no-metadata` | Geometry only: no IFC attributes, properties or relationships |
| `--no-properties` | Leave out property sets |
| `--no-quantities` | Leave out quantity sets |
| `--no-materials` | Leave out IFC material definitions (render materials are kept) |
| `--no-relationships` | Leave out relationship prims, classifications, documents, libraries, approvals, constraints and layers |
| `--all-relationship-prims` | Also write natively mapped relationships as prims (complete relationship graph with GUIDs) |
| `--no-owner-history` | Leave out `IfcOwnerHistory` records |
| `--no-type-inheritance` | Keep type property sets on the type only |
| `--no-semantic-labels` | Do not author `UsdSemantics` class labels |
| `--no-physics` | Do not re-apply `IfcUSD_Physics` property sets as `UsdPhysics` schemas (off for unity) |
| `--material-model {preview,preview+mdl}` | UsdPreviewSurface only, or with an OmniPBR MDL network (omniverse default) |

Data left out with these switches is reported as `skipped`.

### Outputs and checks

| Option | Description |
| --- | --- |
| `--report FILE` | Write the [conversion report](../reference/file-formats.md#conversion-report) as JSON |
| `--metadata-json FILE` | Write the [metadata sidecar](../reference/file-formats.md#metadata-sidecar) (Unity and other tools) |
| `--validate` | Validate the written file; for IFC input this includes the completeness check against the source |
| `--strict` | Stop at the first geometry error and exit with code 1 unless the conversion is complete |
| `--no-schema-fallback` | Fail instead of reading unknown IFC schema identifiers with the closest supported schema |

### General

| Option | Description |
| --- | --- |
| `-v`, `--verbose` | List warnings, products without geometry and geometry errors |
| `-q`, `--quiet` | Print errors only |
| `--install-deps` | `pip install` missing runtime dependencies before running |

## `ifcusd to-ifc`

```text
ifcusd to-ifc INPUT OUTPUT [--source IFC] [--layer USD]... [options]
```

| Argument | Description |
| --- | --- |
| `INPUT` | `.usd`, `.usda`, `.usdc` or `.usdz` file |
| `OUTPUT` | `.ifc` file; folders are created |
| `--source IFC` | The IFC file `INPUT` was converted from. It is updated with what changed or was added in USD (merge mode). Without it the model is rebuilt from USD (standalone mode). |
| `--layer USD` | Extra USD layer composed on top, e.g. a recorded robot run (repeatable; later layers win) |

| Option | Default | Description |
| --- | --- | --- |
| `--schema` | the stage's, else `IFC4` | IFC schema in standalone mode (`IFC2X3`, `IFC4`, `IFC4X3_ADD2`) |
| `--time` | `earliest` | Stage time exported: `earliest`, `latest`, `default` or a time code |
| `--no-attributes`, `--no-properties`, `--no-placements` | | Do not write changed attributes, property values or transforms |
| `--no-new-objects`, `--no-geometry` | | Ignore prims without IFC identity; or create them without geometry |
| `--no-physics`, `--no-semantics`, `--no-time-series`, `--no-trajectories` | | Skip `UsdPhysics` data, semantic labels, time series or trajectories |
| `--no-templates` | | Do not declare new property sets as `IfcPropertySetTemplate`s |
| `--namespace NS` | `robot`, `sim` | Attribute namespace collected into property sets (repeatable) |
| `--new-object-class` | `IfcBuildingElementProxy` | Class of new elements |
| `--exclude PATTERN` | | `fnmatch` pattern of prim paths to ignore (repeatable) |
| `--time-origin` | `1970-01-01T00:00:00` | Date and time of time code 0 for time series |
| `--report FILE` | | Write the JSON export report |
| `--validate` | | Validate the output against its IFC schema; exit `1` if the export introduced errors |

See [USD to IFC and robot data](usd-to-ifc.md) for what is written and how.

## `ifcusd validate`

```text
ifcusd validate USD [--source IFC] [--json FILE] [--no-usd-validators]
```

| Option | Description |
| --- | --- |
| `--source IFC` | Original IFC file: every object (by GlobalId) and every property and quantity value must be present |
| `--json FILE` | Write the validation report as JSON |
| `--no-usd-validators` | Skip OpenUSD's built-in validators (structural checks still run) |

See [Reports and validation](validation.md) for what is checked.

## `ifcusd info`

```text
ifcusd info INPUT [--json]
```

Prints the schema (declared and used), header, units, georeferencing,
instance counts per product class, number of types, property and quantity
sets, and relationships. Works for `.ifc`, `.ifczip` and `.ifcx`.

## `ifcusd schema`

```text
ifcusd schema [--print]
```

Prints the folder of the USD schema plugin (use it for
`PXR_PLUGINPATH_NAME`), or with `--print` the schema source.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Completed with problems: validation errors, or `--strict` and the conversion was not complete |
| `2` | Usage or environment error (bad option value, missing dependency) |
| `3` | Conversion failed (unreadable input, unsupported schema, unwritable output) |

These make IfcUSD easy to use in CI pipelines:

```bash
ifcusd convert model.ifc model.usdc --strict --validate || exit 1
```

## Examples

```bash
# Omniverse, centimetre stage, 8 threads
ifcusd convert model.ifc model.usdc --profile omniverse --meters-per-unit 0.01 --threads 8

# Structural elements only
ifcusd convert model.ifc frame.usdc --include-type IfcBeam --include-type IfcColumn --include-type IfcSlab

# Everything except furniture, with a JSON report
ifcusd convert model.ifc model.usdc --exclude-type IfcFurnishingElement --report model.report.json

# Lightweight visualization file: geometry only, coarser tessellation
ifcusd convert model.ifc preview.usdz --no-metadata --linear-deflection 0.01

# Full relationship graph for analysis
ifcusd convert model.ifc graph.usdc --all-relationship-prims

# IFC5 with an extra layer
ifcusd convert hello-wall.ifcx hello-wall.usda --layer fire-rating.ifcx

# Check an existing conversion against its source
ifcusd validate model.usdc --source model.ifc --json validation.json

# Write a robot training run back into the original IFC model
ifcusd to-ifc model.usdc model.trained.ifc --source model.ifc --layer robot_run.usda --validate

# Rebuild IFC from a USD file alone
ifcusd to-ifc model.usdc rebuilt.ifc --schema IFC4X3_ADD2
```
