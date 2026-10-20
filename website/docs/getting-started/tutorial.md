# Tutorial: your first conversion

This walk-through converts `ifc/Architecture.ifc`, a small house shipped in
the repository, and shows how to inspect, convert, read back and verify a
model. Every output below was produced by running these exact commands.

!!! info "Before you start"
    [Install IfcUSD](installation.md) and download
    [`Architecture.ifc`](https://github.com/isaddiq/ifcusd/raw/main/ifc/Architecture.ifc)
    into an empty folder, or use any IFC file of your own.

## 1. Inspect the IFC file

`ifcusd info` summarizes a file without converting it:

```bash
ifcusd info Architecture.ifc
```

```text
format: IFC-SPF
schema: IFC4
declaredSchema: IFC4
availableSchemas: ['IFC2X3', 'IFC4', 'IFC4X3_ADD2']
sha256: 3ff9b10bd00c7b96dded51e7ca5a6b69efbea38b049adcdd05fcd247de7e70d5
header:
  description: ['ViewDefinition [ReferenceView_V1.2]']
  originatingSystem: SketchUp 2024 (24.0.594)
units:
  AREAUNIT: SQUARE_METRE
  LENGTHUNIT: MILLIMETRE
  VOLUMEUNIT: CUBIC_METRE
georeference:
  conversionClass: IfcMapConversion
  crs:name: EPSG:32760
products:
  IfcBuildingElementProxy: 5
  IfcWall: 4
  IfcSlab: 3
  ...
propertySets: 13
quantitySets: 7
```

The model is IFC4, in millimetres, and georeferenced in UTM zone 60S. Add
`--json` to get the same information as JSON for scripts.

## 2. Convert

```bash
ifcusd convert Architecture.ifc out/Architecture.usda --report out/report.json
```

```text
input   Architecture.ifc  [IFC4]
output  out/Architecture.usda  [profile: generic]
prims 84 | elements 15 | spatial 7 | types 16 | relationships 57
geometry: meshes 14 | instances 0 (prototypes 0) | curves 0 | triangles 1167 | materials 10
data: property sets 20 | properties 43 | quantities 25
coverage of 117 IfcRoot instances: prim 40, native 57, attributes 20, filtered 0, skipped 0, missing 0
completed in 0.47s (complete)
```

How to read the summary:

* **prims** are all USD prims written. **elements** are building elements;
  **spatial** counts site, building, storey and space prims.
* The **coverage** line accounts for all 117 `IfcRoot` instances: 40 became
  prims, 57 relationships were expressed natively (hierarchy, type links,
  material links, collections), and 20 property/quantity sets became
  attributes. **missing 0** means nothing was left out.
* `out/report.json` holds the same information per IFC class, plus timings
  and any warnings. See [Reports and validation](../guide/validation.md).

## 3. Look at the result

Open `out/Architecture.usda` in a text editor, or open it in NVIDIA
Omniverse, Blender (*File ▸ Import ▸ Universal Scene Description*) or usdview.
The hierarchy mirrors the IFC spatial structure:

```text
/World
  ifc_silly_sample_scene_project_<guid>          IfcProject
    environment_site_<guid>                      IfcSite
      house_site_<guid>                          IfcSite
        Single_family_house_<guid>               IfcBuilding
          _00_groundfloor_<guid>                 IfcBuildingStorey
            house_outer_wall_house_left_<guid>   IfcWall
              Body                               Mesh
            ...
  IfcData                                        types, materials, classifications, groups…
  Looks                                          render materials
```

Each wall carries its IFC data as typed attributes:

```usda
def Xform "house_outer_wall_house_left_0OfZwWc8j9QP5uX8xPTxDH" (
    prepend apiSchemas = ["IfcEntityAPI", "IfcPropertySetAPI:Pset_WallCommon",
                          "IfcQuantitySetAPI:Qto_WallBaseQuantities", "SemanticsLabelsAPI:class"]
    displayName = "house - outer wall - house left"
    kind = "component"
)
{
    uniform token ifc:class = "IfcWall"
    uniform string ifc:globalId = "0OfZwWc8j9QP5uX8xPTxDH"
    rel ifc:hasMaterial = </World/IfcData/Materials/stone_sand_lime_271>
    rel ifc:isTypedBy = </World/IfcData/Types/house_outer_wall_house_left_3zoiwePjTDQAyswDZcvJRA>
    custom uniform bool ifc:pset:Pset_WallCommon:IsExternal = 1
    custom uniform double ifc:qto:Qto_WallBaseQuantities:Length = 6000.000000000036 (
        customData = { string ifcUnit = "MILLIMETRE"; string ifcType = "IfcQuantityLength" }
    )
    matrix4d xformOp:transform = ( ... )
    def Mesh "Body" ( ... ) { ... }
}
```

## 4. Read the data back in Python

```python title="read_back.py"
from pxr import Usd

stage = Usd.Stage.Open("out/Architecture.usda")

for prim in stage.Traverse():
    if prim.GetAttribute("ifc:class").Get() != "IfcWall":
        continue
    name = prim.GetAttribute("ifc:name").Get()
    external = prim.GetAttribute("ifc:pset:Pset_WallCommon:IsExternal").Get()
    length = prim.GetAttribute("ifc:qto:Qto_WallBaseQuantities:Length")
    unit = length.GetCustomDataByKey("ifcUnit")
    wall_type = prim.GetRelationship("ifc:isTypedBy").GetTargets()[0]
    print(f"{name:40s} external={external!s:5s} length={length.Get():8.1f} {unit}  type={wall_type.name}")
```

```text
house - outer wall - house left          external=True  length=  6000.0 MILLIMETRE  type=house_outer_wall_house_left_3zoiwePjTDQAyswDZcvJRA
house - outer wall - house right back    external=True  length=  4200.0 MILLIMETRE  type=house_outer_wall_house_right_back_1t790TYSH528FFF32MjoC5
house - outer wall - house right front   external=True  length=  1800.0 MILLIMETRE  type=house_outer_wall_house_right_front_2YJwrhcCv9v8UXU8cWK40m
plumbing wall                            external=False length=  3800.0 MILLIMETRE  type=plumbing_wall_2S9_r4C7nDjgFt_4z9uegR
```

Values keep their IFC types and project units. Geometry, by contrast, is in
stage units (metres here).

## 5. Verify that nothing was lost

```bash
ifcusd validate out/Architecture.usda --source Architecture.ifc
```

```text
out/Architecture.usda: OK (0 errors, 0 warnings, 0 notes)
  ifcEntityPrims: 62
  meshes: 14
  metersPerUnit: 1.0
  missingObjects: 0
  missingPropertyValues: 0
  sourceObjects: 40
  sourcePropertyValues: 68
  upAxis: Z
```

`validate` re-reads the IFC file and checks that all 40 object definitions
(by GlobalId) and all 68 property and quantity values are present in the USD
file. It also runs OpenUSD's validators and checks mesh topology and
relationship targets.

## 6. Convert for your target

```bash
ifcusd convert Architecture.ifc out/Architecture.usdc --profile omniverse
ifcusd convert Architecture.ifc out/Architecture.usdz --profile unity --metadata-json out/Architecture.ifcusd.json
```

Continue with [Omniverse and Unity](../guide/omniverse-and-unity.md) to load
the results, or with [Recipes](../guide/recipes.md) for batch conversion,
filtering and data extraction.
