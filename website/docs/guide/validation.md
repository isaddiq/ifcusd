# Reports and validation

IfcUSD treats "did everything arrive?" as a question with a checkable answer.
Every conversion produces a **report**, and every output can be **validated**,
including a round-trip check against the source IFC.

## The conversion report

The summary printed by `ifcusd convert` comes from the report:

```text
coverage of 117 IfcRoot instances: prim 40, native 57, attributes 20, filtered 0, skipped 0, missing 0
completed in 0.47s (complete)
```

Every `IfcRoot` instance in the source (objects, types, property sets,
relationships) is counted in exactly one bucket:

| Bucket | Meaning |
| --- | --- |
| `prim` | written as its own USD prim |
| `native` | a relationship expressed natively: namespace parenting, a USD relationship property or a collection |
| `attributes` | a property or quantity set written as attributes on the objects it belongs to |
| `filtered` | excluded on purpose by `--include-type` / `--exclude-type` |
| `skipped` | excluded on purpose by switching a data group off (`--no-properties`…) |
| `missing` | not represented. Always reported as a warning; expected to be 0. |

A conversion is **complete** (`report.complete`) when nothing is missing, no
geometry failed, and no instances were dropped by a compatibility schema.

Write the full report with `--report report.json`. It also contains
per-class coverage, counts, timings, warnings, products without geometry and
provenance. The format is documented in
[File formats](../reference/file-formats.md#conversion-report).

### Products without geometry

Some products have a representation that IfcOpenShell cannot tessellate, for
example certain IFC4X3 alignment curve types. They are listed in
`products_without_geometry`, with the reason and IfcOpenShell's diagnostics.
Their IFC data is still written; only the geometry is absent.

### Schema fallback

When a file declares a schema identifier the installed IfcOpenShell cannot
parse (`IFC4X1`, `IFC4X2`, `IFC4X3_TC1`, `IFC4_ADD2`…), it is read with the
closest supported schema. The report records `declared_schema` and
`compatibility_schema`. The raw file is scanned, and any instance types that
do not exist in the compatibility schema are listed in `dropped_entities`, so
the fallback never loses data silently. Use `--no-schema-fallback` to refuse
such files instead.

## Validating a file

```bash
ifcusd validate model.usdc --source model.ifc
```

```text
model.usdc: OK (0 errors, 0 warnings, 0 notes)
  ifcEntityPrims: 62
  meshes: 14
  missingObjects: 0
  missingPropertyValues: 0
  sourceObjects: 40
  sourcePropertyValues: 68
```

| Check | Details |
| --- | --- |
| Stage metadata | `defaultPrim`, `upAxis` and `metersPerUnit` are authored |
| OpenUSD validators | `UsdValidation` (OpenUSD 24.11+): shading, subsets, composition, stage metadata… |
| Mesh topology | face counts add up, indices in range, normals match their interpolation, `extent` authored, subset indices in range |
| Relationships | every `ifc:*`, `material:binding` and `collection:*` target exists |
| GlobalIds | duplicates are reported (they usually come from the source file) |
| Completeness (`--source`) | every object definition is present by GlobalId, and every property and quantity value of every object (including inherited type values) is present |

Expected notes are not errors:

* outside Omniverse, OpenUSD cannot resolve `OmniPBR.mdl` (Omniverse profile);
* in a broken OpenUSD installation, the core `UsdPreviewSurface` shader
  definitions may be missing from the shader registry. This is reported as a
  warning about the environment, not about the file.

## In CI

```bash
ifcusd convert model.ifc model.usdc --strict --validate --report report.json
```

`--strict` stops at the first geometry error and makes the command exit with
code `1` unless the conversion is complete; `--validate` exits with `1` on any
validation error. Keep `report.json` as a build artifact.

```python
from ifcusd import convert, validate_usd

result = convert("model.ifc", "model.usdc")
assert result.report.complete, result.report.warnings
assert validate_usd("model.usdc", source="model.ifc").ok
```
