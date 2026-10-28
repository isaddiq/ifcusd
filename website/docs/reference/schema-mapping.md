# IFC and OpenUSD: schema comparison and the IfcUSD mapping

This document compares the IFC and OpenUSD data models, explains where they
do not line up, and specifies exactly how IfcUSD maps one onto the other. It
is the reference for anyone who reads IfcUSD output programmatically, extends
the mapping, or needs to cite it.

Mapping version: **1.1** (`ifcusd.SCHEMA_VERSION`, also written to every file as
`customLayerData.ifcusd.schemaVersion`).

---

## 1. The two data models side by side

| Aspect | IFC (ISO 16739, IFC2X3 – IFC4X3) | OpenUSD | Consequence for conversion |
| --- | --- | --- | --- |
| Formalism | EXPRESS (ISO 10303-11) entity–relationship schema; STEP physical file | Scene description: prims in a namespace tree, properties, metadata; composed from layers | IFC graphs must be projected onto a tree plus cross-references |
| Identity | `GlobalId` (22-char GUID) on every `IfcRoot`; STEP `#id` per instance | Prim path (location in namespace); no intrinsic GUID | GUIDs are kept as attributes; paths are derived from names and GUIDs |
| Classification of objects | Single-inheritance class hierarchy (~1,300 classes in IFC4X3), e.g. `IfcWall ⊂ IfcBuiltElement ⊂ IfcElement ⊂ IfcProduct ⊂ IfcObject ⊂ IfcRoot` | A small set of concrete *typed schemas* (`Xform`, `Mesh`, `Scope`…) plus *API schemas* applied as mix-ins | IFC class becomes data (`ifc:class`) rather than a USD type, so any IFC version fits without generating 1,300 USD types |
| Spatial / decomposition structure | Objectified relationships (`IfcRelAggregates`, `IfcRelContainedInSpatialStructure`, `IfcRelNests`, `IfcRelVoidsElement`…) forming a graph | Strict namespace tree | One decomposition relation per object becomes parenting; the rest become USD relationships or relationship prims |
| Relationships | First-class entities (`IfcRel*`) with their own GUID, attributes and n-ary ends | `rel` properties: binary, directional, no attributes; collections for sets | Attribute-less relationships map to `rel`; relationships with their own data become prims |
| Properties | Dynamic property sets (`IfcPropertySet`, `IfcElementQuantity`) of typed, unit-bearing values of seven kinds | Statically typed attributes with namespaces and metadata (`displayName`, `displayGroup`, `customData`, `allowedTokens`) | Each property becomes a typed attribute in a `ifc:pset:<set>:<property>` namespace |
| Type / occurrence | `IfcTypeObject` with shared property sets and representation maps, linked by `IfcRelDefinesByType` | Class inheritance, references and instancing as composition arcs | Types become prims; shared geometry becomes native USD instancing |
| Geometry | Implicit, parametric and procedural (extrusions, sweeps, CSG, booleans, B-rep, NURBS, alignment curves); placements are chains of relative coordinate systems; several representation contexts (Body, Axis, FootPrint…) | Explicit, render-ready geometry (`Mesh`, `BasisCurves`, `Points`); `xformOp`s on every `Xformable` | Geometry is tessellated (IfcOpenShell); placements become double-precision matrices; each representation context becomes a child prim |
| Units | A unit assignment per measure type (length, area, volume, angle, currency…), including conversion-based units | `metersPerUnit` (and `kilogramsPerUnit`) at stage level, applying to geometry only | Geometry is converted to stage units; property values keep project units, with the unit stored next to each value |
| Appearance | Semantic materials (layer/profile/constituent sets) and presentation styles that predate PBR (Phong/Blinn shading, reflectance methods) | Shading networks (UsdPreviewSurface, MaterialX, MDL) bound with `material:binding` | Semantic materials and render materials are kept separate and linked |
| Georeferencing | `IfcMapConversion` + `IfcProjectedCRS`, `IfcSite.RefLatitude/RefLongitude`, true north | No core schema (application extensions only) | Stored as typed attributes in an IfcUSD API schema |
| Extension | New schema versions, property-set templates, bSDD dictionaries, model view definitions | Schema plugins (including codeless schemas), namespaces, layers | IfcUSD ships a codeless schema; unknown IFC data still lands in typed, namespaced attributes |
| Collaboration | Whole-file exchange (IFC5 adds layers) | Layer stacks and non-destructive composition | Output is a single self-contained layer that others can reference and override |

The deepest mismatch is between IFC's *objectified relationship graph* and
USD's *namespace tree*. A product is often related to several objects at once,
for example aggregated into an assembly, typed by a type object, associated
with a material, grouped into a system and connected to other elements. USD
gives a prim exactly one parent. IfcUSD therefore chooses one relationship per
object to drive the tree (section 3.2) and expresses every other relationship
explicitly, so nothing is implied or lost.

---

## 2. Design principles

1. **Nothing is dropped silently.** Every `IfcRoot` instance in the source is
   accounted for in the conversion report (section 6). Data that cannot be
   represented natively is still written, as typed attributes or canonical JSON.
2. **Typed and reversible.** Values keep their IFC types and units. Attribute
   names follow fixed rules that can be inverted, and the original IFC names
   are kept in metadata.
3. **Schema-independent.** The IFC class is data, not a USD type. IFC2X3,
   IFC4, IFC4X3 and IFC5 all use the same USD structure, and future IFC
   versions need no new USD schema.
4. **Renderer-ready without plugins.** Output uses only core OpenUSD types, so
   Omniverse, Unity, usdview and Blender open it as-is. The IfcUSD API schemas
   add documentation and schema-aware queries when registered, but are never
   required.
5. **Reproducible.** The same input, options and tool versions produce
   byte-identical files regardless of thread count. Every file records its
   source checksum and conversion options.

---

## 3. Mapping rules

### 3.1 Stage and file level

| IFC | USD |
| --- | --- |
| — | `defaultPrim = "World"` (configurable), `upAxis` (`Z`; `Y` for Unity), `metersPerUnit` (1.0 by default) |
| Conversion provenance | `customLayerData.ifcusd = {converter, converterVersion, schemaVersion, ifcopenshellVersion, usdVersion, sourceName, sourceSha256, ifcSchema, profile, options}` |
| `FILE_SCHEMA` / schema used to read | `ifc:file:schema` (token), `ifc:file:schemaIdentifier` (as declared), `ifc:file:compatibilitySchema` (when a fallback schema was used) |
| `FILE_NAME`, `FILE_DESCRIPTION` | `ifc:file:name`, `timeStamp`, `author[]`, `organization[]`, `preprocessorVersion`, `originatingSystem`, `authorization`, `description[]` (includes the MVD), `implementationLevel` |
| Source file | `ifc:file:sha256` |
| `IfcUnitAssignment` | `ifc:units:<UNITTYPE>` (e.g. `ifc:units:LENGTHUNIT = "MILLIMETRE"`), `ifc:units:lengthScale` (metres per project length unit) |
| `IfcMapConversion` / `IfcProjectedCRS` | `ifc:geo:conversionClass`, `eastings`, `northings`, `orthogonalHeight`, `xAxisAbscissa`, `xAxisOrdinate`, `scale`, `ifc:geo:crs:name`, `crs:geodeticDatum`, `crs:verticalDatum`, `crs:mapProjection`, `crs:mapZone`, `crs:mapUnit` |
| `IfcSite.RefLatitude/RefLongitude/RefElevation` | `ifc:geo:refLatitude`, `refLongitude` (decimal degrees), `refElevation` (metres) |
| `IfcGeometricRepresentationContext.TrueNorth` | `ifc:geo:trueNorth` (double2) |

All of these live on the default prim (`/World`), which has the `IfcFileAPI`
and `IfcGeoreferenceAPI` schemas applied. IFC is Z-up. For a Y-up stage, a
single `xformOp:rotateX = -90` on `/World` rotates the whole model, so no
coordinate is rewritten.

### 3.2 Namespace layout

```text
/World                                   Xform   kind=assembly   IfcFileAPI, IfcGeoreferenceAPI
  <IfcProject>                           Xform   kind=assembly   IfcEntityAPI
    <IfcSite>/<IfcBuilding>/<Storey>     Xform   kind=group      spatial structure (also IfcFacility, IfcRoad, IfcBridge…)
      <IfcWall>                          Xform   kind=component  element
        Body                             Mesh                    tessellated "Body" representation
        Axis                             BasisCurves purpose=guide  other representation contexts
        <IfcOpeningElement>              Xform   kind=subcomponent  voiding feature (geometry hidden/omitted)
      <IfcStair>                         Xform   kind=assembly   element with aggregated parts
        <IfcStairFlight>                 Xform   kind=component
    Unassigned                           Xform   kind=group      products outside the spatial structure
  IfcData                                Scope                   non-geometric IFC data
    Types | Groups | Processes | Resources | Controls | Actors | Objects
    Materials | Classifications | Documents | Libraries | Approvals | Constraints | Profiles
    Layers | OwnerHistory | PropertySets | PropertyTemplates
    Relationships/<IfcRel class>/<relationship>
  Looks                                  Scope                   UsdShade render materials
  Prototypes                             Scope                   class prims shared by instanceable meshes
  PhysicsMaterials                       Scope                   UsdPhysics materials (3.10), when present
```

**Choosing the parent.** For each object, the first applicable relationship
in this priority order becomes its namespace parent:

| Priority | Relationship | `ifc:parentRelationship` |
| --- | --- | --- |
| 1 | `IfcRelAggregates` | `aggregates` |
| 2 | `IfcRelNests` | `nests` |
| 3 | `IfcRelVoidsElement` (opening → host) | `voids` |
| 4 | `IfcRelProjectsElement` | `projects` |
| 5 | `IfcRelAdheresToElement` (IFC4X3) | `adheres` |
| 6 | `IfcRelConnectsPortToElement` (IFC2X3 ports) | `port` |
| 7 | `IfcRelContainedInSpatialStructure` | `contains` |
| — | none (product) | `unassigned` |
| — | none (non-product) | `none` |

Parenting is read from the relationship entities, not from inverse
attributes, so files that relate entity types the schema's inverses do not
declare (common in practice) still nest correctly. Products only nest under
products or the project, and non-products only under objects of the same
category. Cycles are broken deterministically. A parenting relationship is
marked *native* only when every one of its related objects actually took it
as parent. Otherwise it is also written as a relationship prim, so an element
that is both aggregated and contained keeps both facts.

**Prim names** are `<Name>_<GlobalId>` sanitized to USD identifiers
(`--naming name_guid`, the default, which is stable across re-exports), or
`<GlobalId>` / `<Name>` with the other naming modes. Resources without a
GlobalId use their STEP id. The original name is always kept as `displayName`
prim metadata. Children are sorted deterministically: spatial structure
first, storeys by elevation, then by IFC class, name and GlobalId.

### 3.3 Entity attributes

Every prim that represents an IFC entity has `IfcEntityAPI` applied and
carries:

| Attribute | Type | Content |
| --- | --- | --- |
| `ifc:class` | `token` | Exact IFC class name, e.g. `IfcWall` |
| `ifc:stepId` | `int` | STEP instance id (file-specific, for traceability) |
| `ifc:parentRelationship` | `token` | See 3.2 |
| `ifc:<lowerFirst(Name)>` | typed | **Every explicit EXPRESS attribute**: `GlobalId → ifc:globalId`, `PredefinedType → ifc:predefinedType`, `Elevation → ifc:elevation`, `OverallHeight → ifc:overallHeight`… |

The naming rule is reversible: upper-case the first letter to recover the
EXPRESS name, which is also stored as `displayName`. Value types follow this
table:

| EXPRESS value | USD type |
| --- | --- |
| BOOLEAN | `bool` |
| LOGICAL `UNKNOWN` | `token` |
| INTEGER | `int` (`int64` on overflow) |
| REAL, NUMBER, measures | `double` |
| STRING, labels, identifiers, text | `string` |
| ENUMERATION | `token` with `allowedTokens` from the schema |
| homogeneous LIST/SET/ARRAY of the above | the matching array type |
| nested or mixed aggregates | `string` holding canonical JSON (`customData.ifcEncoding = "json"`) |
| entity reference to an entity that has a prim | `rel` to that prim (e.g. `ifc:ownerHistory`, `ifc:relatingElement`) |
| entity reference to a resource without a prim | `string` holding canonical JSON of the entity, three levels deep |

Defined types are recorded in `customData.ifcValueType` (e.g.
`IfcLengthMeasure`), and the project unit of measure-typed values in
`customData.ifcUnit`.

Some explicit attributes are represented by other USD constructs instead:

| EXPRESS attribute | Represented by |
| --- | --- |
| `ObjectPlacement` | `xformOp:transform` (section 3.7) |
| `Representation`, `RepresentationMaps` | geometry child prims and prototypes |
| `HasPropertySets` (types) | `ifc:pset` / `ifc:qto` namespaces |
| `UnitsInContext`, `RepresentationContexts` (project) | `ifc:units:*` on `/World` |
| `IfcPresentationLayerAssignment.AssignedItems` | `collection:members` |
| `IfcRelDefinesByProperties.RelatingPropertyDefinition` | attributes on the related objects (GlobalIds on the relationship prim in `--all-relationship-prims` mode) |

### 3.4 Property and quantity sets

A property `P` in set `S` becomes the attribute `ifc:pset:<S>:<P>`, and a
quantity becomes `ifc:qto:<S>:<Q>`. `S` and `P` are sanitized identifiers,
made unique with `_2`, `_3`… on collision. Each set applies a multiple-apply
API schema instance: `IfcPropertySetAPI:<S>` or `IfcQuantitySetAPI:<S>`.

Original names and IFC typing travel as metadata on every attribute:

| Metadata | Content |
| --- | --- |
| `displayName` | Original property name, e.g. `Area (m²)` |
| `displayGroup` | Original set name, e.g. `Custom Data (Ext)`. Property editors that support `displayGroup` group by it. |
| `customData.ifcType` | Property class, e.g. `IfcPropertySingleValue`, `IfcQuantityLength` |
| `customData.ifcValueType` | Measure or defined type, e.g. `IfcLengthMeasure` |
| `customData.ifcUnit` | Explicit unit, or the project unit for the measure, e.g. `MILLIMETRE` |
| `customData.ifcSource` | `type` when inherited from the type object |
| `customData.ifcDescription`, `ifcFormula`, `ifcUsageName`, `ifcEnumeration`, `ifcComplexProperty`… | Remaining IFC attributes of the property |

Set-level data (original name, class, GlobalId, description, source,
`MethodOfMeasurement`) is stored in prim metadata at
`customData.ifc.propertySets.<S>` and `customData.ifc.quantitySets.<S>`.

| IFC property kind | USD representation |
| --- | --- |
| `IfcPropertySingleValue` | one attribute; a null value is declared without a value |
| `IfcPropertyEnumeratedValue` | `token` (single) or `token[]`, with `allowedTokens` from the `IfcPropertyEnumeration` |
| `IfcPropertyBoundedValue` | `<P>:upperBoundValue`, `<P>:lowerBoundValue`, `<P>:setPointValue` |
| `IfcPropertyListValue` | array attribute |
| `IfcPropertyTableValue` | `<P>:definingValues`, `<P>:definedValues` (+ expression / interpolation in `customData`) |
| `IfcPropertyReferenceValue` | `rel` when the referenced entity has a prim (e.g. an `IfcMaterial`), otherwise JSON |
| `IfcComplexProperty` | nested namespace `<P>:<SubProperty>` (any depth) |
| `IfcPhysicalSimpleQuantity` (`Length`, `Area`, `Volume`, `Count`, `Weight`, `Time`, `Number`) | `double` (`int` for counts) in project units |
| `IfcPhysicalComplexQuantity` | nested namespace |
| Predefined property sets (`IfcDoorLiningProperties`…) | one attribute per explicit attribute |
| Material property sets (`IfcMaterialProperties`, IFC2X3 subtypes) | `ifc:pset` attributes on the material prim |
| Property sets attached to nothing | their own prims under `IfcData/PropertySets` |

**Type inheritance.** An occurrence carries its own sets merged with its
type's sets. The occurrence value wins per property, as IFC specifies, and
inherited values are tagged `ifcSource = "type"`. Use
`--no-type-inheritance` to keep type data on the type prim only.

### 3.5 Relationships

| IFC relationship | USD representation | Report bucket |
| --- | --- | --- |
| `IfcRelAggregates`, `IfcRelNests`, `IfcRelContainedInSpatialStructure`, `IfcRelVoidsElement`, `IfcRelProjectsElement`, `IfcRelAdheresToElement`, `IfcRelConnectsPortToElement` | namespace parenting (3.2) | native |
| `IfcRelDefinesByType` | `rel ifc:isTypedBy` → type prim | native |
| `IfcRelDefinesByProperties` | `ifc:pset` / `ifc:qto` attributes | native |
| `IfcRelAssociatesMaterial` | `rel ifc:hasMaterial` → `IfcData/Materials/…` | native |
| `IfcRelAssociatesClassification` | `rel ifc:hasClassification` | native |
| `IfcRelAssociatesDocument` / `Library` / `Approval` / `Constraint` | `rel ifc:hasDocument` / `hasLibrary` / `hasApproval` / `hasConstraint` | native |
| `IfcRelAssociatesProfileDefinition` / `ProfileProperties` | `rel ifc:hasProfile` | native |
| `IfcRelAssignsToGroup` (groups, systems, zones) | `CollectionAPI:members` on the group prim | native |
| `IfcPresentationLayerAssignment` | `CollectionAPI:members` on the layer prim | prim |
| `IfcRelFillsElement` | `rel ifc:fills` on the filling element → opening | native |
| Everything else (`IfcRelConnects*`, `IfcRelSpaceBoundary`, `IfcRelAssignsTo*`, `IfcRelSequence`, `IfcRelServicesBuildings`, `IfcRelReferencedInSpatialStructure`, `IfcRelDeclares`, `IfcRelPositions`, `IfcRelInterferesElements`, …) | prim `IfcData/Relationships/<class>/<name>` with all attributes; ends as `rel ifc:relating*` / `ifc:related*` | prim |

USD relationships have no attributes of their own. A relationship's own data
(GlobalId, owner history, `ConnectionGeometry`, sequence lags…) is therefore
preserved only on relationship prims. Pass `--all-relationship-prims`
(`relationship_prims="all"`) to write *every* relationship as a prim as well.
That gives a complete relationship graph, with each relationship's GlobalId,
for graph analytics and archival.

### 3.6 Materials and styles

IFC distinguishes *what an element is made of* (semantic materials) from *how
it looks* (presentation styles). IfcUSD keeps both, and links them.

| IFC | USD |
| --- | --- |
| `IfcMaterial`, `IfcMaterialLayerSet(Usage)`, `IfcMaterialProfileSet(Usage)`, `IfcMaterialConstituentSet`, `IfcMaterialList`, layers, profiles, constituents | `Scope` prims under `IfcData/Materials`, with all attributes (layer order is kept by `rel ifc:materialLayers`) and material properties as `ifc:pset` |
| `IfcMaterial` → `IfcMaterialDefinitionRepresentation` → `IfcSurfaceStyle` | `rel ifc:surfaceStyle` → `/World/Looks/<style>` |
| `IfcSurfaceStyle` (as used by geometry) | `UsdShade.Material` in `/World/Looks` with `UsdPreviewSurface` (+ `OmniPBR` MDL in the Omniverse profile) |

The presentation-to-PBR mapping (`ifcusd.ifc.styles`) is an approximation,
because IFC styles predate physically based rendering. The original IFC
values are therefore stored on the material as `ifc:style:*` attributes:

| IFC | UsdPreviewSurface |
| --- | --- |
| `SurfaceColour` × `DiffuseColour` factor, or `DiffuseColour` | `diffuseColor` |
| `Transparency` | `opacity = 1 − Transparency` |
| `ReflectanceMethod = METAL` | `metallic = 1` |
| `IfcSpecularRoughness r` | `roughness = r` |
| `IfcSpecularExponent e` | `roughness = √(2 / (e + 2))` |
| `ReflectanceMethod` without highlight | roughness preset (MIRROR 0.05 … MATT 0.9) |
| `IfcSurfaceStyleRefraction.RefractionIndex` / `GLASS` | `ior` (1.5 for glass) |
| `IfcImageTexture` / `IfcBlobTexture` / `IfcPixelTexture` | `UsdUVTexture` → `diffuseColor`. Embedded textures are written next to the output as PNG. |

Meshes also carry `primvars:displayColor` (and `displayOpacity`) for viewers
that ignore materials. Per-face styles become `GeomSubset`s in the
`materialBind` family, with type `partition`.

### 3.7 Geometry, placement and instancing

* Geometry is tessellated by IfcOpenShell in each product's **local**
  coordinate system. Points are `float32`. Every transform is a
  double-precision `matrix4d xformOp:transform`, relative to the parent prim:
  `local = inverse(parent_world) · world`. Georeferenced models with large
  coordinates therefore keep millimetre precision, and moving a storey moves
  its contents.
* Each representation context becomes a child prim named after it. `Body`
  becomes a `Mesh`. `Axis`, `FootPrint` and similar become `BasisCurves`
  (linear); they are `purpose = guide` when the product also has a body.
* Meshes are triangulated, with `subdivisionScheme = none` (BIM geometry must
  not be smoothed), per-corner normals, `extent`, and `doubleSided = true`
  (configurable).
* Spaces, spatial zones and openings are written with `visibility =
  invisible` (spaces can be shown or skipped).
* **Instancing.** Products whose tessellation shares a geometry id
  (representation maps, type geometry) reference one `class` prim under
  `/World/Prototypes` from an `instanceable` `Body`. This is the USD-native
  equivalent of IFC's type/occurrence geometry sharing.
* Geometry is in metres × `1 / metersPerUnit`. Property values keep IFC
  project units (3.4).

### 3.8 Semantic labels

Elements get `SemanticsLabelsAPI:class` with `semantics:labels:class =
["<IfcClass>"]` (OpenUSD's `UsdSemantics`). The Omniverse profile also writes
the legacy `SemanticsAPI:Semantics` attributes. Omniverse Replicator and
Isaac Sim can therefore produce segmentation ground truth by IFC class, for
example for machine-learning research on building data, without any extra
labelling.

### 3.9 IFC5 (IFCX)

IFC5 is itself modelled on USD, so its mapping is direct. Layers are federated
and composed with buildingSMART's reference algorithm (`children` instantiate,
`inherits` copy, later layers win, `null` deletes).

| IFCX | USD |
| --- | --- |
| composed node / child name | prim / prim name |
| `usd::xformop.transform` | `xformOp:transform` |
| `usd::usdgeom::mesh`, `basiscurves`, `visibility` | `Mesh` (with flat normals), `BasisCurves`, `visibility` |
| `points::array`, `points::base64`, `pcd::base64` | `Points` with per-point `displayColor` |
| `bsi::ifc::class {code, uri}` | `ifc:class`, `ifc:classUri` (`IfcEntityAPI`) |
| `bsi::ifc::presentation::diffuseColor/opacity` | bound `UsdPreviewSurface` material + `displayColor` |
| `{"ref": "<node>"}` | `rel` to the referenced node's prim(s) |
| any other `ns::a::b` | attribute `ns:a:b` (`-` → `_`), typed. Enum schemas become `token` + `allowedTokens`; objects become nested namespaces. `customData.ifcxKey` keeps the exact key. |

### 3.10 Physics

Physics has no IFC counterpart, but `ifcusd to-ifc` stores the `UsdPhysics`
data of a simulation as the `IfcUSD_Physics` property set (see the
[IFC extension](ifc-extension.md)). When an element carries that set, it is
written both as `ifc:pset:IfcUSD_Physics:*` attributes and as native schemas:

| `IfcUSD_Physics` property | `UsdPhysics` in the USD output |
| --- | --- |
| `Mass`, `Density`, `CenterOfMass` | `PhysicsMassAPI` on the element prim (`physics:mass`, `physics:density`, `physics:centerOfMass`) |
| `RigidBodyEnabled`, `KinematicEnabled` | `PhysicsRigidBodyAPI` on the element prim |
| `CollisionEnabled` | `PhysicsCollisionAPI` on each of the element's meshes |
| `CollisionApproximation` | `PhysicsMeshCollisionAPI` (`physics:approximation`) on each mesh |
| `StaticFriction`, `DynamicFriction`, `Restitution` | A `Material` with `PhysicsMaterialAPI` in `/World/PhysicsMaterials`, bound to the meshes for the `physics` purpose (identical materials are shared) |
| `Articulated`, `JointCount` | Not applied: a robot's links were merged into one element, so there is no articulation to rebuild |

Masses are in kilograms (`kilogramsPerUnit = 1` is authored), lengths and
densities in stage units. The centre of mass is not written with
`--world-coords`, where prims no longer carry the element's frame. The Unity
profile and `--no-physics` leave the schemas out.

The element prim's `customData.ifc.physicsSchemas` lists the schemas applied
(for example `["PhysicsMassAPI", "PhysicsRigidBodyAPI", "PhysicsCollisionAPI"]`).
`ifcusd to-ifc` uses it to tell a schema removed in the simulation from one
that was never there.

---

## 4. The IfcUSD USD schema

The schema is *codeless*: no compiled code, just `plugInfo.json`,
`generatedSchema.usda` and the source `schema.usda` in
`src/ifcusd/schema/resources`. Run `ifcusd schema` to print the directory.

| Schema | Kind | Applied to | Declares |
| --- | --- | --- | --- |
| `IfcFileAPI` | single-apply | default prim | `ifc:file:*`, `ifc:units:lengthScale` |
| `IfcGeoreferenceAPI` | single-apply | default prim | `ifc:geo:*` |
| `IfcEntityAPI` | single-apply | every IFC entity prim | `ifc:class`, `globalId`, `stepId`, `name`, `description`, `objectType`, `tag`, `longName`, `predefinedType`, `parentRelationship`, and the relationships `isTypedBy`, `hasMaterial`, `hasClassification`, `hasDocument`, `hasLibrary`, `hasApproval`, `hasConstraint`, `hasProfile`, `fills`, `ownerHistory` |
| `IfcPropertySetAPI` | multiple-apply | objects with property sets | namespace `ifc:pset:<instance>:*` |
| `IfcQuantitySetAPI` | multiple-apply | objects with quantity sets | namespace `ifc:qto:<instance>:*` |

Register it with `PXR_PLUGINPATH_NAME=<path printed by ifcusd schema>`, or
call `ifcusd.schema.register()` before opening stages. Schema-aware queries
then work:

```python
prim.HasAPI("IfcEntityAPI")
prim.HasAPI("IfcPropertySetAPI", "Pset_WallCommon")
Usd.SchemaRegistry().FindAppliedAPIPrimDefinition("IfcEntityAPI").GetPropertyNames()
```

**Why API schemas instead of one USD type per IFC class?** Generating ~1,300
typed schemas per IFC version would tie every file to one IFC release. It
would also require the plugin to open files correctly, and it clashes with
the fact that a prim can have only one USD type (here `Xform`, `Mesh` or
`Scope`, which renderers need). Applied API schemas add IFC identity on top of
any USD type. The IFC class stays queryable data, and files stay readable
everywhere.

**Extending the mapping.** The rules are generic, so new IFC classes,
attributes and property sets need no code changes: they appear in the `ifc:`
namespaces automatically. To add your own data on top of a converted file:

* write an override layer (USD `sublayers` or `references`) instead of editing
  the output, so the converted IFC data stays reproducible;
* use your own namespace (e.g. `myorg:`) and, if you need schema semantics,
  your own codeless API schema next to IfcUSD's;
* never reuse the `ifc:` namespace for non-IFC data.

**Versioning.** `SCHEMA_VERSION` changes only when the written structure
changes. A minor version adds namespaces or attributes in a backward-compatible
way; a major version renames or removes something. Read
`customLayerData.ifcusd.schemaVersion` to dispatch.

---

## 5. Reproducibility

* **Deterministic output.** Prims are planned before geometry is processed.
  Tessellated shapes are collected and written in sorted order, materials and
  prototypes have content-derived names, and the thread count is excluded from
  the file. Converting with 1 or 16 threads yields byte-identical `.usda` and
  `.usdc` files (tested).
* **Provenance.** Every file records the source SHA-256, IFC schema, converter,
  IfcOpenShell and OpenUSD versions, and all options.
* **Verification.** `ifcusd validate out.usd --source model.ifc` re-reads the IFC
  and checks that every object definition (by GlobalId) and every
  property/quantity value is present. It also runs OpenUSD's validators and
  checks mesh topology and relationship targets.

---

## 6. Completeness accounting

The conversion report (`--report report.json`) lists, for every `IfcRoot`
class in the file, how its instances were represented:

| Bucket | Meaning |
| --- | --- |
| `prim` | written as a prim |
| `native` | a relationship expressed natively (3.5) |
| `attributes` | a property/quantity set written as attributes on the objects it is attached to |
| `filtered` | excluded by `--include-type` / `--exclude-type` |
| `skipped` | excluded because a data group was switched off (e.g. `--no-properties`) |
| `missing` | not represented. Always reported as a warning; 0 for every file in the test corpus. |

Resources (materials, classifications, documents, owner history, layers…) are
written as prims. Geometry resources are represented by the tessellation.
Products that have a representation but produced no tessellation are listed
under `products_without_geometry` with IfcOpenShell's diagnostics.

When a file declares a schema that the installed IfcOpenShell cannot parse
(e.g. `IFC4X1`, `IFC4X3_TC1`, `IFC4_ADD2`), it is read with the closest
supported schema. Any instance types that schema does not know are counted
from the raw file and reported in `dropped_entities`.

---

## 7. The reverse direction

`ifcusd to-ifc` applies these rules backwards. `ifc:class` and `ifc:globalId`
(or `ifc:stepId` for resources) identify the entity; `ifc:<attribute>` names
map back to EXPRESS names (`ifc:globalId` → `GlobalId`); `customData`
(`ifcType`, `ifcValueType`, `ifcUnit`, `ifcComplexProperty`, `ifcSource`)
restores the exact property kind, defined type and inheritance;
`ifc:parentRelationship` restores the decomposition relationship; USD
relationships restore entity references and associations; transforms become
placements and meshes become `IfcTriangulatedFaceSet` (shared prototypes
become `IfcMappedItem`s). Data added in USD that has no IFC counterpart is
written with the [IFC extension for robot and simulation data](ifc-extension.md).
The user-facing description is in [USD to IFC and robot data](../guide/usd-to-ifc.md).

## 8. Known limitations

* **Tessellation, not parametric round-trip.** USD output is explicit geometry.
  The parametric definition stays in the IFC file, which the output links to by
  checksum, GlobalId and STEP id. Tessellation quality is adjustable
  (`--linear-deflection`, `--angular-deflection`).
* **Geometry IfcOpenShell cannot process** (for example some IFC4X3 alignment
  curve types such as `IfcGradientCurve`) is reported, not converted. The
  product's semantic data is still written.
* **Appearance** is approximated as described in 3.6. IFC texture coordinates
  (`IfcTextureCoordinate`) are not read; textured styles use box-projected UVs.
* **Relationship-level data** (GlobalIds and attributes of natively mapped
  relationships) is kept only with `--all-relationship-prims`.
* **Connection geometry** of relationships (e.g. `IfcRelSpaceBoundary`) is
  stored as JSON, not tessellated.
* **ifcXML** cannot be read with current IfcOpenShell releases; convert it to
  STEP first.
