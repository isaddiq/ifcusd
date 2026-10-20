# File formats

Besides the USD file itself, IfcUSD can write three JSON documents. All are
UTF-8, use stable key order, and are safe to keep in version control or CI
artifacts.

## Conversion report

Written by `--report FILE` or `result.report.to_json(path)`.

| Key | Type | Content |
| --- | --- | --- |
| `input_path`, `output_path` | string | files involved |
| `input_format` | string | `IFC-SPF`, `IFC-ZIP` or `IFCX` |
| `ifc_schema` | string | schema used to read the file |
| `declared_schema` | string | schema identifier in the file header |
| `compatibility_schema` | string \| null | set when a fallback schema was used |
| `source_sha256` | string | SHA-256 of the input |
| `converter_version`, `schema_version` | string | IfcUSD and mapping versions |
| `ifcopenshell_version`, `usd_version` | string | library versions |
| `profile`, `options` | string, object | all conversion options |
| `counts` | object | see below |
| `coverage` | object | per IFC class: `inFile` and the [buckets](../guide/validation.md#the-conversion-report) that are non-zero |
| `coverageTotals` | object | buckets summed over all classes |
| `complete` | bool | nothing missing, failed or dropped |
| `products_without_geometry` | array | `stepId`, `globalId`, `ifcClass`, `name`, `representations`, `reason`, optional `log` |
| `geometry_errors` | array | `stepId`, `globalId`, `ifcClass`, `name`, `reason` |
| `dropped_entities` | object | instances per class not representable in the compatibility schema |
| `warnings` | array of string | human-readable warnings |
| `timings` | object | seconds for `read`, `plan`, `semantics`, `geometry`, `write`, `total` |

`counts` keys: `prims`, `entityPrims`, `spatial`, `elements`, `types`,
`groups`, `relationships`, `relationshipPrims`, `propertySets`, `properties`,
`quantities`, `meshes`, `instances`, `prototypes`, `curves`, `materials`,
`points`, `triangles`, `productsWithGeometry`, `filteredProducts`.

```json title="report.json (abridged)"
{
  "complete": true,
  "ifc_schema": "IFC4",
  "source_sha256": "3ff9b10bd00c7b96dded51e7ca5a6b69efbea38b049adcdd05fcd247de7e70d5",
  "counts": { "prims": 84, "elements": 15, "meshes": 14, "triangles": 1167, "propertySets": 20, "properties": 43, "quantities": 25 },
  "coverage": {
    "IfcBuilding": { "inFile": 1, "prim": 1 },
    "IfcRelAggregates": { "inFile": 6, "native": 6 },
    "IfcPropertySet": { "attributes": 13, "inFile": 13 }
  },
  "coverageTotals": { "inFile": 117, "prim": 40, "native": 57, "attributes": 20, "filtered": 0, "skipped": 0, "missing": 0 },
  "timings": { "read": 0.013, "plan": 0.0144, "semantics": 0.0338, "geometry": 0.2066, "write": 0.0155, "total": 0.2991 }
}
```

## Metadata sidecar

Written by `--metadata-json FILE`. It carries the IFC identity and properties
of every object, keyed by USD prim path, for applications that drop custom
USD attributes (Unity's importer, web viewers). The flat layout can be parsed
by Unity's built-in `JsonUtility`.

```json title="model.ifcusd.json (abridged)"
{
  "format": "ifcusd-metadata/1",
  "converterVersion": "1.0.0",
  "schemaVersion": "1.0",
  "source": "Architecture.ifc",
  "sourceSha256": "3ff9b10b…",
  "ifcSchema": "IFC4",
  "usdFile": "Architecture.usdz",
  "objects": [
    {
      "path": "/World/…/house_outer_wall_house_left_0OfZwWc8j9QP5uX8xPTxDH",
      "globalId": "0OfZwWc8j9QP5uX8xPTxDH",
      "ifcClass": "IfcWall",
      "name": "house - outer wall - house left",
      "properties": [
        { "set": "Pset_WallCommon", "name": "IsExternal", "value": "True", "type": "IfcBoolean", "unit": "", "quantity": false },
        { "set": "Qto_WallBaseQuantities", "name": "Length", "value": "6000.000000000036", "type": "IfcLengthMeasure", "unit": "MILLIMETRE", "quantity": true }
      ]
    }
  ]
}
```

Values are strings, so every consumer can read them; `type` and `unit` give
their meaning. Objects are sorted by path.

## Validation report

Written by `ifcusd validate … --json FILE` or `report.to_json(path)`.

| Key | Content |
| --- | --- |
| `usd_path`, `source_path` | files checked |
| `ok` | `true` when there are no errors |
| `issues` | array of `{severity, code, message, path}`; severity is `error`, `warning` or `info` |
| `stats` | `upAxis`, `metersPerUnit`, versions, `meshes`, `curves`, `instances`, `ifcEntityPrims` and, with a source, `sourceObjects`, `missingObjects`, `sourcePropertyValues`, `missingPropertyValues` |

## Provenance inside the USD file

Every output records its origin in the root layer's `customLayerData`:

```usda
#usda 1.0
(
    customLayerData = {
        dictionary ifcusd = {
            string converter = "IfcUSD"
            string converterVersion = "1.0.0"
            string schemaVersion = "1.0"
            string ifcSchema = "IFC4"
            string ifcopenshellVersion = "0.9.0.post1"
            string usdVersion = "0.26.8"
            string sourceName = "Architecture.ifc"
            string sourceSha256 = "3ff9b10b…"
            string profile = "generic"
            dictionary options = { … }
        }
    }
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
```

```python
from pxr import Usd

data = Usd.Stage.Open("model.usdc").GetRootLayer().customLayerData["ifcusd"]
data["sourceSha256"], data["schemaVersion"]
```
