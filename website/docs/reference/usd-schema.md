# USD schema

IfcUSD defines a small, versioned **codeless USD schema** for IFC data. It
ships inside the package (`src/ifcusd/schema/resources`) as `plugInfo.json`,
`generatedSchema.usda` and the commented source `schema.usda`.

| Schema | Kind | Applied to | Provides |
| --- | --- | --- | --- |
| `IfcFileAPI` | single-apply | default prim (`/World`) | `ifc:file:*` header and provenance, `ifc:units:lengthScale` |
| `IfcGeoreferenceAPI` | single-apply | default prim | `ifc:geo:*` map conversion, CRS, site location, true north |
| `IfcEntityAPI` | single-apply | every prim representing an IFC entity | `ifc:class`, `ifc:globalId`, `ifc:stepId`, `ifc:name`, `ifc:description`, `ifc:objectType`, `ifc:tag`, `ifc:longName`, `ifc:predefinedType`, `ifc:parentRelationship`; relationships `ifc:isTypedBy`, `ifc:hasMaterial`, `ifc:hasClassification`, `ifc:hasDocument`, `ifc:hasLibrary`, `ifc:hasApproval`, `ifc:hasConstraint`, `ifc:hasProfile`, `ifc:fills`, `ifc:ownerHistory` |
| `IfcPropertySetAPI` | multiple-apply | objects with property sets | instance per set; properties in `ifc:pset:<instance>:*` |
| `IfcQuantitySetAPI` | multiple-apply | objects with quantity sets | instance per set; quantities in `ifc:qto:<instance>:*` |

## Is the plugin required?

No. Every attribute in an IfcUSD file has an explicit type, and OpenUSD keeps
unknown API schema names without complaint. Files therefore open identically
in Omniverse, Unity, usdview and Blender whether or not the plugin is
registered. Registering it adds:

* `prim.HasAPI("IfcEntityAPI")` and `prim.HasAPI("IfcPropertySetAPI", "Pset_WallCommon")`;
* `prim.GetAppliedSchemas()` listing the IFC schemas;
* fallback values and documentation for the declared attributes, shown by
  property editors that support them.

## Registering

=== "Environment variable"

    ```bash
    ifcusd schema   # prints the plugin folder
    ```

    Set `PXR_PLUGINPATH_NAME` to that folder before starting usdview,
    Omniverse Kit or your own OpenUSD application. Separate several folders
    with `;` on Windows and `:` elsewhere.

=== "Python"

    ```python
    import ifcusd.schema

    ifcusd.schema.register()   # before the first Usd.Stage is opened in the process
    ```

## Why API schemas rather than one USD type per IFC class

Generating a typed USD schema for each of IFC's ~1,300 classes would tie every
file to one IFC release, and would require the plugin before a file could
even be read correctly. It would also clash with the USD types renderers need
(`Xform`, `Mesh`, `Scope`). Applied API schemas add IFC identity on top of any
USD type; the IFC class is data (`ifc:class`), so one schema serves IFC2X3,
IFC4, IFC4X3 and IFC5.

## Extending

* New IFC classes, attributes and property sets need no schema change: the
  mapping rules place them in the `ifc:` namespaces automatically.
* Put your own data in your own namespace (for example `myorg:`) and, if you
  want schema semantics, define your own codeless API schema next to this one.
* Prefer override layers to editing converted files, so conversions remain
  reproducible.

## Versioning

`ifcusd.SCHEMA_VERSION` (written to every file as
`customLayerData.ifcusd.schemaVersion`) changes only when the written
structure changes. A minor bump adds attributes or namespaces compatibly; a
major bump renames or removes something.

## Source

The complete schema definition (`schema.usda`):

```usda
--8<-- "src/ifcusd/schema/resources/schema.usda"
```
