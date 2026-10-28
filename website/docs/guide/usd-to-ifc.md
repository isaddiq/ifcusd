# USD to IFC: round trips and robot data

IfcUSD converts in both directions. `ifcusd convert` takes IFC to OpenUSD;
`ifcusd to-ifc` takes OpenUSD back to IFC, including everything that was
added in USD on the way: changed property values, moved furniture, new
objects, and the data robots and simulations produce.

```text
model.ifc ──ifcusd convert──▶ model.usdc ──▶ NVIDIA Omniverse / Isaac Sim
    │                                         train robots, simulate, inspect;
    │                                         results recorded in robot_run.usda
    │                                                       │
    └──────▶ ifcusd to-ifc model.usdc model.trained.ifc ◀───┘
                 --source model.ifc --layer robot_run.usda
```

The result is an ordinary, schema-valid IFC file. BIM tools, code checkers
and IDS validators read the robot data as property sets, quantities, time
series and classifications, and the
[IFC extension](../reference/ifc-extension.md) that describes them travels
inside the file as property set templates.

!!! tip "Prefer a UI?"
    [IfcUSD Studio](studio.md) runs the same conversion in the browser: swap
    the direction to **OpenUSD → IFC**, add your edit layers, choose the source
    model and convert. The [tutorial](../getting-started/tutorial.md#7-give-the-kitchen-physics-as-a-simulation-would)
    takes a model through the whole loop, with the command line and in the
    Studio.

## Two modes

| | Merge (`--source`) | Standalone |
| --- | --- | --- |
| Starting point | The original IFC file | An empty IFC file |
| Use it for | Writing robot and simulation results back into a real BIM model | Rebuilding IFC from a USD file when the source is gone, or turning a USD scene built in Isaac Sim into IFC |
| Existing elements | Kept exactly, including parametric geometry; only what changed in USD is written | Rebuilt from the USD prims: attributes, property sets, relationships and tessellated geometry |
| New prims | Become new IFC elements | Become new IFC elements |

Merge mode is the recommended workflow: nothing in the source is lost or
re-tessellated, and running the export on an unchanged USD file writes
nothing at all.

## Quick start

```bash
ifcusd convert model.ifc model.usdc --profile omniverse
# ... work in Omniverse / Isaac Sim, saving your edits in robot_run.usda ...
ifcusd to-ifc model.usdc model.trained.ifc --source model.ifc --layer robot_run.usda --validate
```

```text
input   model.usdc  (+1 layer(s))
output  model.trained.ifc  [IFC4, merge]
source  model.ifc: matched 26 | attributes 1 | placements 1 | not in USD 1
entities: created 1 | new objects 4 | duplicates 1 | relationships 3
data: property sets +10 ~2 | values 23 | time series 2 | classifications 4 | templates 7
geometry: representations 3 | triangles 648 | styles 2 | materials 0
validation: 1 error(s), 1 already in the source
completed in 0.16s (1 warning(s))
invalid (already in the source): #255=IfcTask('33SLfgqmf3M9qv5h2$WaGS',$,'Build walls',...) IfcTask.IsMilestone: Attribute not optional
```

From Python:

```python
from ifcusd import IfcExportOptions, convert, convert_usd_to_ifc

convert("model.ifc", "model.usdc")
result = convert_usd_to_ifc(
    "model.usdc",
    "model.trained.ifc",
    IfcExportOptions(time="latest"),
    source="model.ifc",
    layers=["robot_run.usda"],
    validate=True,
)
print("\n".join(result.report.summary_lines()))
assert result.report.introduced_errors == 0
```

Without `--source` the same commands rebuild the IFC model from USD alone:

```bash
ifcusd to-ifc model.usdc rebuilt.ifc
ifcusd to-ifc warehouse_scene.usd warehouse.ifc --schema IFC4X3_ADD2
```

## Recommended workflow in Omniverse and Isaac Sim

1. Convert once and keep the converted file untouched.
2. Do all robot work in **your own layer**: add it as a sublayer in the Layer
   panel (or from Python as below) and make it the edit target. Omniverse's
   Stage Recorder can record simulated motion and sensor values into such a
   layer as time samples.
3. Export with `--layer` (repeatable; later layers win). Several training runs
   can be exported separately or together.

```python
from pxr import Sdf, Usd, UsdPhysics

layer = Sdf.Layer.CreateNew("robot_run.usda")
session = Sdf.Layer.CreateAnonymous()
session.subLayerPaths = [layer.identifier, "model.usdc"]
stage = Usd.Stage.Open(session)
stage.SetEditTarget(Usd.EditTarget(layer))

wall = next(p for p in stage.Traverse() if p.GetAttribute("ifc:globalId").Get() == "3oSlkaUcf4ngQ_$DOA7SUW")

# Data in an extension namespace becomes the property set Robot_Navigation.
wall.CreateAttribute("robot:navigation:traversable", Sdf.ValueTypeNames.Bool).Set(False)
clearance = wall.CreateAttribute("robot:navigation:clearance", Sdf.ValueTypeNames.Double)
clearance.Set(0.85)                                           # SI: metres
clearance.SetCustomDataByKey("ifcValueType", "IfcLengthMeasure")

# Time samples become an IFC time series.
temperature = wall.CreateAttribute("sim:sensors:temperature", Sdf.ValueTypeNames.Double)
for frame, value in enumerate([20.0, 20.5, 21.0, 21.6]):
    temperature.Set(value, Usd.TimeCode(frame))

# IFC data can be edited directly as well.
wall.GetAttribute("ifc:pset:Pset_WallCommon:FireRating").Set("REI120")

# Physics and semantic labels are picked up from the standard USD schemas.
UsdPhysics.MassAPI.Apply(wall).CreateMassAttr(1840.0)
wall.CreateAttribute("semantics:labels:affordance", Sdf.ValueTypeNames.TokenArray).Set(["climbable"])

layer.Save()
```

## What is written back

| In USD | In IFC |
| --- | --- |
| Changed `ifc:<attribute>` (`ifc:name`, `ifc:description`, …) | The entity attribute |
| Changed `ifc:class` | The entity is reclassified (`IfcBuildingElementProxy` → `IfcColumn`) |
| Changed or new `ifc:pset:<Set>:<Property>` | The property. Sets shared by several objects are copied first, so only this object changes. A changed value inherited from the type becomes an occurrence override; the type keeps its value. |
| Changed or new `ifc:qto:<Set>:<Quantity>` | The quantity (`IfcQuantityLength`, `IfcQuantityArea`, …) |
| `robot:<group>:<name>`, `sim:<group>:<name>` | Property `<name>` of the set `Robot_<Group>` / `Sim_<Group>`, converted to project units |
| `UsdPhysics` mass, density, centre of mass, rigid body, collision, physics material, articulation | Property set `IfcUSD_Physics` (and back to `UsdPhysics` by `ifcusd convert`, [see below](#physics-comes-back-as-physics)) |
| Time samples on any exported attribute | `IfcRegularTimeSeries` or `IfcIrregularTimeSeries`, referenced by an `IfcPropertyReferenceValue` |
| Animated transform | `IfcUSD_Simulation`: `Trajectory` (time series of position and orientation), `Duration`, `PathLength` |
| `semantics:labels:<taxonomy>`, legacy Replicator `SemanticsAPI` | `IfcClassificationReference` in an `IfcClassification` named after the taxonomy |
| A moved product | Its `ObjectPlacement` |
| A new prim: robot, sensor, obstacle, any mesh | A new element with geometry, contained in the nearest storey or space ([rules below](#how-new-prims-become-ifc-elements)) |
| A copy of an IFC prim (same `GlobalId` twice) | A new entity with a new `GlobalId`; the original keeps its own |
| A deactivated or deleted prim | Nothing is deleted. The product is listed in the report under `not_in_usd`. |

Robot data on an element's mesh (`…/Wall_1/Body`) counts as data of the
element, because labelling tools such as Replicator often write to meshes.

## How new prims become IFC elements

A prim without `ifc:class` becomes a new element when it is

* an **articulated robot**: it, or its root link, has `PhysicsArticulationRootAPI`
  → `IfcBuildingElementProxy` with `ObjectType` `Robot`;
* a **sensor**: its type name contains `Lidar`, `Radar`, `Sensor`, `Imu`,
  `Sonar` or `Ultrasonic`, or it is a camera with `omni:sensor` attributes
  → `IfcSensor` (IFC4 and later);
* a **mesh or implicit shape** (`Mesh`, `Cube`, `Sphere`, `Cylinder`, `Cone`,
  `Capsule`, `Plane`), a `component` model, or an `Xform` that directly holds
  meshes → `IfcBuildingElementProxy`;
* any prim carrying `robot:*`, `sim:*`, `ifc:pset:*` data or a `UsdPhysics`
  mass or rigid body.

Scene roots (such as `/World`), `assembly` and `group` models and prims that
contain IFC prims are treated as folders. Lights, plain cameras, joints,
materials, render settings, physics scenes and Omniverse's own helper prims
(`/OmniverseKit_*`, `/Render`, `defaultGroundPlane`) are ignored; add your
own patterns with `--exclude`.

Each new element gets a deterministic `GlobalId` derived from its prim path,
so the same robot keeps its identity across exports. Its `Name` is the prim's
display name, `Tag` is the prim path, and its whole subtree (all visible,
render-purpose meshes) is its tessellated geometry, with the colours of its
materials as surface styles. It is contained in the storey or space it sits
under in USD. If it sits under an element (a lidar mounted on a wall), it is
contained in that element's storey and connected to it with
`IfcRelConnectsElements`. Elsewhere it goes to the top-level site (or a
default site, building and storey when the model has none).

To choose the class yourself, author `ifc:class` (and optionally
`ifc:predefinedType`, `ifc:objectType`, `ifc:name`) on the prim.

### Colours and materials

Every render material becomes an `IfcSurfaceStyle` with its colour and
transparency, assigned to the geometry it covers. The colour is read from
the material whatever network it uses:

| Material in USD | Colour taken from |
| --- | --- |
| `UsdPreviewSurface` | `diffuseColor`, `opacity` |
| OmniPBR and other MDL materials | `diffuse_color_constant` (OmniPBR's own 0.2 grey when not set), `opacity_constant` when `enable_opacity` is on |
| OmniSurface | `diffuse_reflection_color`, `geometry_opacity` |
| MaterialX / OpenPBR standard surface | `base_color`, `opacity` |
| OmniGlass | `glass_color` |

Inputs connected to the material's own interface (as Omniverse authors them
when you promote a parameter) are followed to their value. A textured input
has no single colour, so the mesh's `displayColor` is used when it has one.
A material bound per face with `GeomSubset`s gives each part its own style.
A mesh without any material keeps its `displayColor`, one style per face
colour when the colour varies per face.

A new element is also *made of* its USD materials: each one becomes an
`IfcMaterial` of the same name with that surface style, associated with the
element. An element with several materials gets an
`IfcMaterialConstituentSet` (an `IfcMaterialList` in IFC2X3). Elements
sharing a USD material share the `IfcMaterial`. If the model already has a
material of that name, it is reused as it is.

The report's geometry line counts the styles and materials written:
`geometry: representations 3 | triangles 648 | styles 2 | materials 0`.

## Units and time

* `ifc:*` values are in IFC **project units**, exactly as `ifcusd convert`
  wrote them.
* Values in extension namespaces (`robot:*`, `sim:*`) and physics are **SI**,
  with lengths in stage units. They are converted to the project's units, for
  example 0.85 m becomes `IfcLengthMeasure(850.)` in a millimetre project.
  Point and vector attributes (`point3f`, `vector3d`) are treated as lengths.
  Set `customData.ifcValueType` (for example `IfcLengthMeasure`,
  `IfcPressureMeasure`) to choose the IFC type of a scalar.
* Transforms and attribute values are read at `--time`: `earliest` (the
  first time sample, or the default value of static attributes), `latest`,
  `default` or a time code.
* Time codes become date-times from `--time-origin` (ISO 8601, default
  `1970-01-01T00:00:00`) using the stage's `timeCodesPerSecond`. Evenly spaced
  samples are written as an `IfcRegularTimeSeries` with a `TimeStep`.

## Options

| CLI | Python (`IfcExportOptions`) | Default | Meaning |
| --- | --- | --- | --- |
| `--source FILE` | `source=` argument | | Merge into this IFC file |
| `--layer FILE` | `layers=` argument | | USD layers composed on top of the input |
| `--schema` | `schema` | stage's, else `IFC4` | Target schema in standalone mode |
| `--time` | `time` | `earliest` | Stage time to export |
| `--no-attributes` | `update_attributes` | on | Write changed attributes and classes |
| `--no-properties` | `update_properties` | on | Write changed and new property values |
| `--no-placements` | `update_placements` | on | Move products whose transform changed |
| `--no-new-objects` | `include_new_objects` | on | Turn new prims into elements |
| `--no-geometry` | `include_geometry` | on | Tessellate new elements |
| `--no-physics` | `include_physics` | on | Write `IfcUSD_Physics` |
| `--no-semantics` | `include_semantics` | on | Write semantic labels as classifications |
| `--no-time-series` | `include_time_series` | on | Write time series (otherwise the value at `--time`) |
| `--no-trajectories` | `include_trajectories` | on | Write `IfcUSD_Simulation` for animated prims |
| `--no-templates` | `property_templates` | on | Declare new sets as property set templates |
| `--namespace NS` | `extension_namespaces` | `robot`, `sim` | Namespaces collected into property sets |
| `--new-object-class` | `new_object_class` | `IfcBuildingElementProxy` | Class of new elements |
| `--exclude PATTERN` | `exclude_paths` | Omniverse helper prims | `fnmatch` patterns of prim paths to ignore |
| `--time-origin` | `time_origin` | `1970-01-01T00:00:00` | Date and time of time code 0 |
| `--report FILE` | `report_path=` argument | | JSON report |
| `--validate` | `validate=` argument | off | Validate the output against its schema |

## Report and validation

The report (`--report`, `result.report`) lists the counts shown above, every
new element with its `GlobalId` and USD path (`created_objects`), every
source product without a prim (`not_in_usd`), warnings and timings.

`--validate` checks the written file against its IFC schema with
IfcOpenShell. In merge mode the source is validated too, and problems it
already had are marked `inSource`; the command exits with `1` only when the
export introduced an error. Output is reproducible: the same inputs give the
same file, apart from timestamps.

## Round trips

The enriched IFC file converts back to USD like any other. The robot data then
appears as `ifc:pset:Robot_Navigation:clearance` and so on, and exporting that
USD file again, unchanged, writes nothing. You can therefore run IFC → USD →
IFC as often as your training loop needs, with each iteration adding to the
same model.

### Physics comes back as physics

`ifcusd convert` turns the `IfcUSD_Physics` property set back into native
`UsdPhysics` schemas, so the next simulation starts with the masses, rigid
bodies, colliders and friction of the last one:

| `IfcUSD_Physics` property | `UsdPhysics` in the USD output |
| --- | --- |
| `Mass`, `Density`, `CenterOfMass` | `PhysicsMassAPI` on the element prim (`physics:mass`, `physics:density`, `physics:centerOfMass`) |
| `RigidBodyEnabled`, `KinematicEnabled` | `PhysicsRigidBodyAPI` on the element prim |
| `CollisionEnabled` | `PhysicsCollisionAPI` on each of the element's meshes |
| `CollisionApproximation` | `PhysicsMeshCollisionAPI` (`physics:approximation`) on each mesh |
| `StaticFriction`, `DynamicFriction`, `Restitution` | A `Material` with `PhysicsMaterialAPI` in `/World/PhysicsMaterials`, bound to the meshes for the `physics` purpose (identical materials are shared) |
| `Articulated`, `JointCount` | Not applied: a robot's links were merged into one element, so there is no articulation to rebuild |

Values are converted from project units to SI and then to the stage's units
(`kilogramsPerUnit` is set to 1, densities follow `metersPerUnit`). The
property set is also kept as `ifc:pset:IfcUSD_Physics:*` attributes.

On the way back to IFC both forms are read. If you change `physics:mass` in
Isaac Sim, the new mass is written. If you change the
`ifc:pset:IfcUSD_Physics:Mass` attribute instead, that edit is written. When
both changed, the simulation's value is used. `UsdPhysics` stores 32-bit
floats, so values are compared at that precision: a mass of
`1840.123456789` kg survives any number of round trips unchanged.

Removing physics in the simulation is written back too. If you remove the
rigid body from a table in Isaac Sim (*Remove > Physics > Rigid Body*), the
table's `RigidBodyEnabled` becomes `false`; removing the colliders from its
meshes sets `CollisionEnabled` to `false`. This works whether you saved the
edit in a separate layer or in the converted file itself. A removed
`PhysicsMassAPI` keeps the IFC `Mass`, because IFC properties are never
deleted. Set a new mass instead.

A centre of mass authored on a scaled prim, such as a robot asset referenced
at 0.01 scale, is scaled with it, because the scale is baked into the
element's geometry.

The Unity profile and `--no-physics` leave physics out.

## Limitations

* **Geometry of existing elements** is kept from the source in merge mode;
  edits to their meshes in USD are not written back. Only their placement is.
  The same goes for their appearance: they keep the source model's styles
  even if you bind another material in USD.
* **Textures** are not written to IFC; a textured material contributes the
  mesh's `displayColor`, or a neutral colour.
* **Relationship edits** on existing elements (moving a wall prim to another
  storey, retyping it) are not written back in merge mode.
* **Deletions** are reported, never applied.
* **Robots** become one element each; links are not decomposed into an
  assembly.
* **Curves** (`BasisCurves`, such as wall axes) are not rebuilt in standalone
  mode; parametric geometry becomes tessellated geometry there.
* **IFC2X3** has no property set templates and no `IfcSensor`; the data is
  still written as property sets on `IfcBuildingElementProxy` elements.
* **Articulations** are not rebuilt in USD from `IfcUSD_Physics`
  (`Articulated`, `JointCount`), because a robot is one IFC element.
