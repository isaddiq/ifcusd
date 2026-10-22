# IFC extension for robot and simulation data

This page specifies how `ifcusd to-ifc` stores data that has no place in the
IFC model of a building yet: physics, motion, sensor readings, robot
annotations and semantic labels produced in OpenUSD. It is written for people
who consume such files: BIM tool developers, code-checking rule authors and
researchers who query the data.

## Approach

The IFC schema is an ISO standard (ISO 16739); its EXPRESS definition cannot
gain new entities or attributes without a new release. IFC does, however,
define mechanisms for exactly this purpose, and the extension uses only those:

| Need | IFC mechanism |
| --- | --- |
| New, typed, unit-aware values on objects | Property sets (`IfcPropertySet`) with `IfcProperty*` values of IFC measure types |
| A formal definition of those sets | Property set templates (`IfcPropertySetTemplate`) declared in an `IfcProjectLibrary`, linked to the sets by `IfcRelDefinesByTemplate` |
| Values that change over time | Time series (`IfcRegularTimeSeries`, `IfcIrregularTimeSeries`) referenced by `IfcPropertyReferenceValue` |
| Labels from a vocabulary | Classifications (`IfcClassification`, `IfcClassificationReference`) |
| Where data came from | External references (`IfcDocumentReference`, `IfcExternalReferenceRelationship`) |
| Objects that are not building elements | `IfcBuildingElementProxy` and `IfcSensor`, with `ObjectType` |

Files therefore validate against the official schema, open in any IFC tool,
and need no IfcUSD-specific software to be read. The templates make the
extension **self-describing**: a file states the name, measure type and
meaning of every property it adds, and IDS or code-checking rules can be
written against them.

## Naming

* Sets defined by this extension start with `IfcUSD_`. The `Pset_` and `Qto_`
  prefixes are reserved for buildingSMART's standard sets and are not used.
* Data authored in an extension namespace keeps the namespace as prefix:
  `robot:navigation:clearance` → set `Robot_Navigation`, property `clearance`;
  `robot:battery` → set `Robot_Common`, property `battery`; deeper
  namespaces (`robot:arm:joint1:torque`) become `IfcComplexProperty` values.
  A USD `displayGroup` on the attribute overrides the set name.
* Property names keep the USD attribute name (or its `displayName`).

## `IfcUSD_Physics`

Physical simulation properties from OpenUSD `UsdPhysics`. Written for the
element a prim maps to; for an IFC element the prim and its representation
meshes are read, for a new element its whole subtree.

| Property | IFC type | Unit | USD source |
| --- | --- | --- | --- |
| `Mass` | `IfcMassMeasure` | project mass unit (kg) | `physics:mass` (`MassAPI`) on the prim, else the sum over its bodies |
| `Density` | `IfcMassDensityMeasure` | kg/m³ | `physics:density` on the prim, else of the bound physics material |
| `CenterOfMass` | list of `IfcLengthMeasure` | project length unit | `physics:centerOfMass`, in the element's placement frame |
| `RigidBodyEnabled` | `IfcBoolean` | | `physics:rigidBodyEnabled` (`RigidBodyAPI`) |
| `KinematicEnabled` | `IfcBoolean` | | `physics:kinematicEnabled` |
| `CollisionEnabled` | `IfcBoolean` | | any `CollisionAPI` with `physics:collisionEnabled` |
| `CollisionApproximation` | `IfcLabel` | | `physics:approximation` (`MeshCollisionAPI`), e.g. `convexHull` |
| `StaticFriction` | `IfcReal` | | physics material bound to the colliders with purpose `physics` |
| `DynamicFriction` | `IfcReal` | | physics material of the colliders |
| `Restitution` | `IfcReal` | | physics material of the colliders |
| `Articulated` | `IfcBoolean` | | `PhysicsArticulationRootAPI` in a new element |
| `JointCount` | `IfcInteger` | | number of `UsdPhysics` joints in a new element |

Stage `kilogramsPerUnit` and `metersPerUnit` are applied before conversion to
project units. Friction and restitution are only read for elements that
collide, so an opening does not inherit the physics material of its wall.

`ifcusd convert` writes the set back as native `UsdPhysics` schemas:

| `IfcUSD_Physics` property | `UsdPhysics` in the USD output |
| --- | --- |
| `Mass`, `Density`, `CenterOfMass` | `PhysicsMassAPI` on the element prim (`physics:mass`, `physics:density`, `physics:centerOfMass`) |
| `RigidBodyEnabled`, `KinematicEnabled` | `PhysicsRigidBodyAPI` on the element prim |
| `CollisionEnabled` | `PhysicsCollisionAPI` on each of the element's meshes |
| `CollisionApproximation` | `PhysicsMeshCollisionAPI` (`physics:approximation`) on each mesh |
| `StaticFriction`, `DynamicFriction`, `Restitution` | A `Material` with `PhysicsMaterialAPI` in `/World/PhysicsMaterials`, bound to the meshes for the `physics` purpose (identical materials are shared) |
| `Articulated`, `JointCount` | Not applied: a robot's links were merged into one element, so there is no articulation to rebuild |

## `IfcUSD_Simulation`

Motion of prims with animated transforms (time-sampled `xformOp`s).

| Property | IFC type | Content |
| --- | --- | --- |
| `Trajectory` | `IfcPropertyReferenceValue` → `IfcTimeSeries` | Per USD time sample, the values X, Y, Z (`IfcLengthMeasure`, project units) and the orientation quaternion W, X, Y, Z (`IfcReal`) in the project coordinate system |
| `Duration` | `IfcTimeMeasure` | Time between the first and the last sample |
| `PathLength` | `IfcLengthMeasure` | Distance travelled along the trajectory |

## Value types

Values carry the IFC type in `customData.ifcValueType` when present (every
attribute `ifcusd convert` writes has one). Otherwise:

| USD type | IFC property | IFC value type |
| --- | --- | --- |
| `bool` | `IfcPropertySingleValue` | `IfcBoolean` |
| `int`, `int64`, `uint`, `uchar` | `IfcPropertySingleValue` | `IfcInteger` |
| `float`, `double`, `half` | `IfcPropertySingleValue` | `IfcReal` |
| `string`, `token` | `IfcPropertySingleValue` | `IfcLabel` (`IfcText` above 255 characters) |
| `token` with `allowedTokens` | `IfcPropertyEnumeratedValue` + `IfcPropertyEnumeration` | `IfcLabel` |
| `asset` | `IfcPropertySingleValue` | `IfcURIReference` (`IfcLabel` in IFC2X3) |
| arrays, vectors, quaternions, matrices | `IfcPropertyListValue` | element type; `point*` and `vector*` are `IfcLengthMeasure` |
| relationship to a material, document, person… | `IfcPropertyReferenceValue` | the referenced entity |
| relationship to other objects | `IfcPropertyListValue` | `IfcIdentifier` (their `GlobalId`s) |
| attribute with two or more time samples | `IfcPropertyReferenceValue` | `IfcTimeSeries` |

Values in extension namespaces and physics are SI (lengths in stage units)
and are converted to the project's units for the measure type.

## Time series

| Attribute | Value |
| --- | --- |
| Class | `IfcRegularTimeSeries` when samples are evenly spaced (with `TimeStep` in seconds), else `IfcIrregularTimeSeries` with one time stamp per value |
| `Name` | The property name |
| `StartTime`, `EndTime` | Time origin (`--time-origin`, default `1970-01-01T00:00:00`) plus time code / `timeCodesPerSecond` |
| `TimeSeriesDataType` | `CONTINUOUS` for numbers, `DISCRETE` otherwise |
| `DataOrigin` | `SIMULATED` (override with `customData.ifcDataOrigin`, e.g. `MEASURED` for real sensor logs) |
| `Values` | One `IfcTimeSeriesValue` per sample; vectors become several `ListValues` |

In IFC4 and later each series is linked to its source: an
`IfcExternalReferenceRelationship` named `<prim path>.<attribute>` relates it
to an `IfcDocumentReference` whose `Location` is the USD layer file that holds
the samples.

## Semantic labels

Labels from `UsdSemantics` (`semantics:labels:<taxonomy>`) and from the
legacy Omniverse Replicator `SemanticsAPI` become classifications:

| USD | IFC |
| --- | --- |
| taxonomy (`class`, `affordance`, `material`…) | `IfcClassification` with `Name` = taxonomy and `Source` = `OpenUSD UsdSemantics` |
| label | `IfcClassificationReference` with `Identification` and `Name` = label (`ItemReference` in IFC2X3) |
| labelled prim | `IfcRelAssociatesClassification` to the element |

The `class` label `ifcusd convert` writes (the IFC class) is not exported.

## Templates

Every set the export creates or extends, other than `Pset_*` and `Qto_*`, is
declared as an `IfcPropertySetTemplate` in the `IfcProjectLibrary` named
**IfcUSD Extension**, itself declared on the project (`IfcRelDeclares`).

| Template attribute | Value |
| --- | --- |
| `TemplateType` | `PSET_OCCURRENCEDRIVEN`, `PSET_TYPEDRIVENONLY`, `PSET_TYPEDRIVENOVERRIDE`, `PSET_MATERIALDRIVEN` or `QTO_*`, from the objects that carry the set |
| `ApplicableEntity` | The IFC classes that carry the set, e.g. `IfcBuildingElementProxy,IfcWall` |
| `HasPropertyTemplates` | One `IfcSimplePropertyTemplate` per property (`P_SINGLEVALUE`, `P_LISTVALUE`, `P_REFERENCEVALUE`… with `PrimaryMeasureType`), `IfcComplexPropertyTemplate` for complex properties |

`IfcRelDefinesByTemplate` links the template to every set it describes.
Later exports extend existing templates (new properties, new applicable
classes) instead of duplicating them.

## New elements

| Attribute | Value |
| --- | --- |
| `GlobalId` | Deterministic, derived from the USD prim path and the source: the same prim keeps its identity across exports |
| `Name` | Prim `displayName`, else its name |
| `Description` | `Created by IfcUSD from the OpenUSD prim <path>` |
| `Tag` | The prim path |
| `ObjectType` | `Robot`, or the sensor's USD type (`OmniLidar`…); `PredefinedType` is then `USERDEFINED` |
| Containment | `IfcRelContainedInSpatialStructure` in the nearest storey, space or site |
| Mounting | `IfcRelConnectsElements` from the element the prim sits under in USD |

## Example

An IFC4 excerpt of a wall that a robot found non-traversable, and the
declaration of the set:

```text
#357=IFCPROPERTYSET('3v98IMx09SrgwVLEMK4ljW',$,'Robot_Navigation',$,(#355,#356));
#355=IFCPROPERTYSINGLEVALUE('clearance',$,IFCLENGTHMEASURE(850.),$);
#356=IFCPROPERTYSINGLEVALUE('traversable',$,IFCBOOLEAN(.F.),$);
#401=IFCPROJECTLIBRARY('3m6vOPwTfHN8PaW6Fj3Mvj',$,'IfcUSD Extension','Property set templates for data added in OpenUSD (robotics, simulation, ...) by IfcUSD.',$,$,$,$,$);
#426=IFCPROPERTYSETTEMPLATE('152enWXL9OLPgHAiivAlOU',$,'Robot_Navigation','Property set added from OpenUSD by IfcUSD.',.PSET_OCCURRENCEDRIVEN.,'IfcWall',(#427,#428));
#427=IFCSIMPLEPROPERTYTEMPLATE('2tXuzPz6fTrxWev3Js9fvS',$,'clearance',$,.P_SINGLEVALUE.,'IfcLengthMeasure',$,$,$,$,$,.READWRITE.);
#428=IFCSIMPLEPROPERTYTEMPLATE('0xe4DSiUnR0QlgA2US1IHf',$,'traversable',$,.P_SINGLEVALUE.,'IfcBoolean',$,$,$,$,$,.READWRITE.);
#429=IFCRELDEFINESBYTEMPLATE('3w40DoxqPI8BuDirqc2lLy',$,$,$,(#357),#426);
```

A temperature log recorded in simulation:

```text
#383=IFCPROPERTYREFERENCEVALUE('temperature',$,$,#381);
#381=IFCREGULARTIMESERIES('temperature',$,'1970-01-01T00:00:00','1970-01-01T00:00:01',.CONTINUOUS.,.SIMULATED.,$,$,0.1,(#370,...,#380));
#370=IFCTIMESERIESVALUE((IFCREAL(20.)));
#346=IFCDOCUMENTREFERENCE('robot_run.usda',$,'OpenUSD layer','Time-sampled data recorded in OpenUSD',$);
#382=IFCEXTERNALREFERENCERELATIONSHIP('/World/.../Room_1_0JKGIo_3fAyuJxrr9hYcdI.sim:sensors:temperature','USD attribute the time series was read from',#346,(#381));
```

## IFC2X3

IFC2X3 has no property set templates, no `IfcExternalReferenceRelationship`
and no `IfcSensor`. The data is still written: sets without templates,
series without the source link, sensors as `IfcBuildingElementProxy`, and
time stamps as `IfcDateAndTime`. Use IFC4 or IFC4X3 when the formal
definitions matter.
