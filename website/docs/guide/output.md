# What gets written

This page is a practical tour of a converted file. The complete rules are in
the [IFC ↔ USD mapping specification](../reference/schema-mapping.md).

## Layout

```text
/World                                Xform  kind=assembly   IfcFileAPI, IfcGeoreferenceAPI
  <Project>_<guid>                    Xform  kind=assembly   IfcEntityAPI
    <Site>/<Building>/<Storey>        Xform  kind=group      spatial structure, real placements
      <Element>_<guid>                Xform  kind=component  IFC attributes, property sets, relationships
        Body                          Mesh                   tessellated body representation
        Axis                          BasisCurves            other representations (purpose = guide)
        <Opening>_<guid>              Xform  kind=subcomponent
    Unassigned                        Xform  kind=group      products outside the spatial structure
  IfcData                             Scope                  non-geometric IFC data
    Types  Groups  Materials  Classifications  Documents  Libraries  Approvals
    Constraints  Profiles  Layers  OwnerHistory  PropertySets  PropertyTemplates
    Processes  Resources  Controls  Actors  Objects
    Relationships/<IfcRel class>/<relationship>
  Looks                               Scope                  render materials
  Prototypes                          Scope                  shared meshes for instancing
  PhysicsMaterials                    Scope                  UsdPhysics materials, for models with IfcUSD_Physics
```

* **Default prim** `/World`, so the file can be referenced as-is.
* **Stage metadata:** `upAxis`, `metersPerUnit`, and `customLayerData.ifcusd`
  (provenance: source SHA-256, versions, options).
* **Model kinds** follow USD conventions: project `assembly`, spatial
  structure `group`, elements `component` (or `assembly` when they aggregate
  parts), features and ports `subcomponent`.

## On every IFC prim

| Property | Example | Notes |
| --- | --- | --- |
| `ifc:class` | `"IfcWall"` | exact IFC class |
| `ifc:globalId` | `"0OfZwWc8j9QP5uX8xPTxDH"` | |
| `ifc:stepId` | `315` | STEP `#id` in the source file |
| `ifc:name`, `ifc:description`, `ifc:objectType`, `ifc:tag`, `ifc:predefinedType`, … | | every explicit IFC attribute, `ifc:<lowerFirst(Name)>` |
| `ifc:parentRelationship` | `"contains"` | which IFC relationship the USD parent represents |
| `rel ifc:isTypedBy` | `</World/IfcData/Types/…>` | type object |
| `rel ifc:hasMaterial`, `ifc:hasClassification`, `ifc:hasDocument`, … | | associations |
| `rel ifc:ownerHistory` | | owner history record |
| `ifc:pset:<Set>:<Property>` | `ifc:pset:Pset_WallCommon:IsExternal = true` | property values |
| `ifc:qto:<Set>:<Quantity>` | `ifc:qto:Qto_WallBaseQuantities:Length = 6000` | quantities in project units |
| `semantics:labels:class` | `["IfcWall"]` | for Replicator / Isaac Sim |
| `displayName` (prim metadata) | `"house - outer wall - house left"` | original name, any characters |

Property attributes carry their IFC context as metadata:

```usda
custom uniform double ifc:qto:Qto_WallBaseQuantities:Length = 6000 (
    customData = {
        string ifcType = "IfcQuantityLength"
        string ifcUnit = "MILLIMETRE"
        string ifcValueType = "IfcLengthMeasure"
    }
    displayGroup = "Qto_WallBaseQuantities"
    displayName = "Length"
)
```

## Reading the data

=== "One attribute"

    ```python
    wall.GetAttribute("ifc:pset:Pset_WallCommon:FireRating").Get()
    ```

=== "All property sets of a prim"

    ```python
    def property_sets(prim):
        """{set name: {property name: value}} using the original IFC names."""
        sets = {}
        for attribute in prim.GetAuthoredAttributes():
            if attribute.GetNamespace().startswith(("ifc:pset", "ifc:qto")):
                group = attribute.GetMetadata("displayGroup")
                sets.setdefault(group, {})[attribute.GetMetadata("displayName")] = attribute.Get()
        return sets
    ```

=== "Unit and type of a value"

    ```python
    length = wall.GetAttribute("ifc:qto:Qto_WallBaseQuantities:Length")
    length.Get()                              # 6000.0
    length.GetCustomDataByKey("ifcUnit")      # "MILLIMETRE"
    length.GetCustomDataByKey("ifcValueType") # "IfcLengthMeasure"
    ```

=== "Following relationships"

    ```python
    type_prim = stage.GetPrimAtPath(wall.GetRelationship("ifc:isTypedBy").GetTargets()[0])
    material = stage.GetPrimAtPath(wall.GetRelationship("ifc:hasMaterial").GetTargets()[0])
    ```

=== "Group members"

    ```python
    from pxr import Usd

    members = Usd.CollectionAPI(group_prim, "members").GetIncludesRel().GetTargets()
    ```

## Units

* Geometry is in stage units: metres × `1 / metersPerUnit`.
* Property and quantity values keep the IFC project units, with the unit in
  `customData.ifcUnit`. The project units are listed on `/World` as
  `ifc:units:<UNITTYPE>`, and `ifc:units:lengthScale` gives metres per project
  length unit.

## Geometry

* Meshes are triangulated, with `subdivisionScheme = none`, per-corner
  normals and an authored `extent`.
* Transforms are double-precision `xformOp:transform` matrices relative to the
  parent, so georeferenced models keep millimetre accuracy.
* Shared type geometry is written once under `/World/Prototypes` and
  instanced (`instanceable = true`) unless `--no-instancing` is set.
* Spaces, zones and openings are hidden (`visibility = invisible`).
* Per-face styles are `GeomSubset`s bound to separate materials.

## Materials

`/World/Looks` holds one `UsdShade.Material` per IFC surface style used, with
a `UsdPreviewSurface` network. The Omniverse profile adds OmniPBR. The original
IFC style values are kept as `ifc:style:*` attributes on the material, and each
IFC material definition in `/World/IfcData/Materials` links to its render
material with `rel ifc:surfaceStyle`. Meshes also carry
`primvars:displayColor` for viewers that ignore materials.
