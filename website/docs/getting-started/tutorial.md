# Tutorial: from IFC to simulation and back

This walk-through takes `ifc/Architecture.ifc`, a small house shipped in the
repository, through the whole IfcUSD loop:

1. inspect the IFC file, convert it to OpenUSD, read the data back and prove
   nothing was lost;
2. give an object physics in USD, as a simulation would, and write that back
   into the IFC model;
3. convert again and see the physics arrive in USD as native `UsdPhysics`.

Every output below was produced by running these exact commands. Each step
can also be done without the command line in
[IfcUSD Studio](#10-do-it-all-in-ifcusd-studio).

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
sha256: a2e088d6c7a07ea7d3498ed494ae4070e9989a5ceac9eb6e2d69184a81e78e41
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
completed in 0.32s (complete)
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
            living_room_<guid>                   IfcSpace
              kitchen_<guid>                     IfcFurniture
                Body                             Mesh
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
  converterVersion: 1.1.0
  curves: 0
  ifcEntityPrims: 62
  ifcSchema: IFC4
  instances: 0
  meshes: 14
  metersPerUnit: 1.0
  missingObjects: 0
  missingPropertyValues: 0
  schemaVersion: 1.1
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

[Omniverse and Unity](../guide/omniverse-and-unity.md) explains how to load
the results.

## 7. Give the kitchen physics, as a simulation would

In a robot simulation you would open the stage in Isaac Sim, make the
kitchen unit a rigid body with a mass, give its mesh a collider and a
surface friction, and save your changes to a layer of their own. This script
does the same with the OpenUSD API, so you can follow along without Isaac
Sim. The converted file stays untouched; the edits go to `out/sim.usda`:

```python title="simulate.py"
from pxr import Sdf, Usd, UsdPhysics, UsdShade

stage = Usd.Stage.Open("out/Architecture.usda")
edits = Sdf.Layer.CreateNew("out/sim.usda")
stage.GetSessionLayer().subLayerPaths.append(edits.identifier)
stage.SetEditTarget(edits)  # everything below goes into sim.usda, not the converted file

kitchen = next(p for p in stage.Traverse() if p.GetAttribute("ifc:name").Get() == "kitchen")
body = kitchen.GetChild("Body")
UsdPhysics.RigidBodyAPI.Apply(kitchen)
UsdPhysics.MassAPI.Apply(kitchen).CreateMassAttr(180.0)  # kilograms
UsdPhysics.CollisionAPI.Apply(body)
UsdPhysics.MeshCollisionAPI.Apply(body).CreateApproximationAttr("convexHull")

wood = UsdShade.Material.Define(stage, "/World/PhysicsMaterials/Wood")
friction = UsdPhysics.MaterialAPI.Apply(wood.GetPrim())
friction.CreateStaticFrictionAttr(0.6)
friction.CreateDynamicFrictionAttr(0.5)
friction.CreateRestitutionAttr(0.1)
UsdShade.MaterialBindingAPI.Apply(body).Bind(wood, UsdShade.Tokens.weakerThanDescendants, "physics")

edits.Save()
```

```bash
python simulate.py
```

## 8. Write the changes back into the IFC model

`ifcusd to-ifc` composes the edit layer on top of the converted stage and
updates a copy of the source model with what changed:

```bash
ifcusd to-ifc out/Architecture.usda out/Architecture.updated.ifc --source Architecture.ifc --layer out/sim.usda
```

```text
input   Architecture.usda  (+1 layer(s))
output  Architecture.updated.ifc  [IFC4, merge]
source  Architecture.ifc: matched 52 | attributes 0 | placements 0 | not in USD 0
entities: created 0 | new objects 0 | duplicates 0 | relationships 0
data: property sets +1 ~0 | values 7 | time series 0 | classifications 0 | templates 1
geometry: representations 0 | triangles 0 | styles 0 | materials 0
completed in 0.17s (0 warning(s))
```

All 52 IFC objects in the stage matched the source, and nothing else
changed. The kitchen gained one property set with 7 values, the
`IfcUSD_Physics` set of the [IFC extension](../reference/ifc-extension.md).
A template declares that set inside the file, so any IFC tool can read it:

```python title="physics_in_ifc.py"
import ifcopenshell
import ifcopenshell.util.element

model = ifcopenshell.open("out/Architecture.updated.ifc")
kitchen = next(e for e in model.by_type("IfcFurniture") if e.Name == "kitchen")
for name, value in ifcopenshell.util.element.get_psets(kitchen)["IfcUSD_Physics"].items():
    if name != "id":
        print(f"{name:24s} {value}")
```

```text
Mass                     180.0
RigidBodyEnabled         True
CollisionEnabled         True
CollisionApproximation   convexHull
StaticFriction           0.6
DynamicFriction          0.5
Restitution              0.1
```

The physics material itself did not become an IFC element: it is a property
of the kitchen's surfaces.

## 9. Convert again: the physics comes back

Convert the updated model for Omniverse:

```bash
ifcusd convert out/Architecture.updated.ifc out/Architecture.next.usda --profile omniverse
```

```text
input   out/Architecture.updated.ifc  [IFC4]
output  out/Architecture.next.usda  [profile: omniverse]
prims 104 | elements 15 | spatial 7 | types 16 | relationships 61
geometry: meshes 14 | instances 0 (prototypes 0) | curves 0 | triangles 1167 | materials 10
data: property sets 21 | properties 50 | quantities 25 | physics prims 1
coverage of 131 IfcRoot instances: prim 52, native 58, attributes 21, filtered 0, skipped 0, missing 0
completed in 0.33s (complete)
```

`physics prims 1` is the kitchen. Its `IfcUSD_Physics` set was turned back
into native `UsdPhysics` schemas, so Isaac Sim simulates it straight away:

```python title="physics_in_usd.py"
from pxr import Usd, UsdPhysics, UsdShade

stage = Usd.Stage.Open("out/Architecture.next.usda")
kitchen = next(p for p in stage.Traverse() if p.GetAttribute("ifc:name").Get() == "kitchen")
body = kitchen.GetChild("Body")
material, _ = UsdShade.MaterialBindingAPI(body).ComputeBoundMaterial("physics")
friction = UsdPhysics.MaterialAPI(material)

print("kitchen:", [s for s in kitchen.GetAppliedSchemas() if s.startswith("Physics")])
print("Body:   ", [s for s in body.GetAppliedSchemas() if s.startswith("Physics")])
print("mass:   ", UsdPhysics.MassAPI(kitchen).GetMassAttr().Get(), "kg")
print("shape:  ", UsdPhysics.MeshCollisionAPI(body).GetApproximationAttr().Get())
print("surface:", material.GetPath().name, f"static friction {friction.GetStaticFrictionAttr().Get():.2g}")
```

```text
kitchen: ['PhysicsMassAPI', 'PhysicsRigidBodyAPI']
Body:    ['PhysicsCollisionAPI', 'PhysicsMeshCollisionAPI']
mass:    180.0 kg
shape:   convexHull
surface: Physics_sf0_6_df0_5_r0_1 static friction 0.6
```

The loop is stable. Writing the new stage back without any edits changes
nothing, however many times you go round:

```bash
ifcusd to-ifc out/Architecture.next.usda out/check.ifc --source out/Architecture.updated.ifc
```

```text
input   Architecture.next.usda
output  check.ifc  [IFC4, merge]
source  Architecture.updated.ifc: matched 66 | attributes 0 | placements 0 | not in USD 0
entities: created 0 | new objects 0 | duplicates 0 | relationships 0
data: property sets +0 ~0 | values 0 | time series 0 | classifications 0 | templates 0
geometry: representations 0 | triangles 0 | styles 0 | materials 0
completed in 0.18s (0 warning(s))
```

Change the mass in the next simulation and only that value is written. If
you remove the kitchen's rigid body there, `RigidBodyEnabled` becomes
`false`. [USD to IFC and robot data](../guide/usd-to-ifc.md#physics-comes-back-as-physics)
has the details, including robots, sensors and trajectories.

## 10. Do it all in IfcUSD Studio

Every step above also works in the browser UI. Start it from the folder with
your files:

```bash
ifcusd ui
```

(or `python app.py` in a clone of the repository). Then:

1. Under **Convert**, drop `Architecture.ifc` on the left, keep *Any OpenUSD
   tool* and *USDA text*, and select **Convert to OpenUSD**. Leave
   **Validate the result** on to run step 5 as well.
2. Select **Inspect the result**. The tree opens down to the storeys of the
   house. Type `kitchen` in the search box and select it to see its IFC
   attributes and property sets.
3. Back under **Convert**, select **Set up the way back to IFC** in the
   result. The Studio swaps the direction, with `Architecture.usda` as the
   stage to read and `Architecture.ifc` as the model to update. Add
   `out/sim.usda` as an edit layer: paste its path, or choose the file. Then
   select **Convert to IFC**. The summary matches step 8: `property sets +1`,
   `values 7`.
4. Inspect `Architecture.updated.ifc` and find the kitchen again. It now has
   an `IfcUSD_Physics` set, shown in amber with what each property means.
5. Select **Convert this to OpenUSD** in the result, pick *Omniverse and
   Isaac Sim*, convert, and inspect the kitchen. `IfcUSD_Physics` and the
   native **Physics (UsdPhysics)** values appear side by side.

The **Schema** tab explains the mapping you have just used. See
[IfcUSD Studio](../guide/studio.md) for everything the UI does.

## Where next

* [Recipes](../guide/recipes.md): batch conversion, filtering and data extraction.
* [USD to IFC and robot data](../guide/usd-to-ifc.md): robots, sensors,
  trajectories and time series in IFC.
* [Python API](../guide/python-api.md): the same steps in code.
