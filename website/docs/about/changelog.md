# Changelog

All notable changes to IfcUSD are documented here. The project follows
[Semantic Versioning](https://semver.org/). The IFC-in-USD mapping has its own
version (`SCHEMA_VERSION`), recorded in every output file.

## [1.1.0] — 2026-10-23

Bidirectional conversion: OpenUSD back to IFC, including robot and simulation
data. The IFC-in-USD mapping is unchanged (version 1.0).

### Added

- **`ifcusd to-ifc`** and `convert_usd_to_ifc()` / `UsdToIfcConverter`
  convert a USD stage to IFC, with extra USD layers composed on top
  (`--layer`).
- **Merge mode** (`--source model.ifc`): the source IFC is updated with what
  changed or was added in USD: attributes and classes, property and quantity
  values (shared sets are copied on write, changed type values become
  occurrence overrides), placements of moved products, copies of IFC prims
  (new `GlobalId`s), and new elements. Exporting an unchanged stage writes
  nothing; products without a prim are reported, never deleted.
- **Standalone mode**: the IFC model is rebuilt from an IfcUSD stage
  (spatial structure, every attribute, all property kinds, quantities,
  types, materials, classifications, groups, layers, generic relationships,
  units, georeferencing, tessellated geometry with `IfcMappedItem`
  instancing), or created from any USD scene, such as one built in Isaac Sim.
- **New elements from USD prims**: articulated robots, sensors (`IfcSensor`),
  meshes and implicit shapes become elements with geometry, surface styles,
  spatial containment and host connections, with stable `GlobalId`s.
- **IFC extension for robot and simulation data**, built only from standard
  IFC mechanisms: `IfcUSD_Physics` (from `UsdPhysics`),
  `IfcUSD_Simulation` (trajectories), `Robot_*` / `Sim_*` property sets from
  the `robot:` and `sim:` namespaces (SI values converted to project units),
  time-sampled values as `IfcRegularTimeSeries` / `IfcIrregularTimeSeries`
  linked to their USD source, semantic labels as classifications, and
  `IfcPropertySetTemplate` declarations in the `IfcUSD Extension` project
  library.
- `IfcExportOptions`, `IfcExportReport` (counts, new elements, products not in
  USD, warnings) and schema validation that separates errors the export
  introduced from those the source already had (`--validate`).
- Documentation: [USD to IFC and robot data](../guide/usd-to-ifc.md) and the
  [IFC extension](../reference/ifc-extension.md) specification.

### Fixed

- The test model factory no longer leaks IfcOpenShell owner settings from the
  IFC2X3 model into the IFC4 and IFC4X3 models.

## [1.0.0] — 2026-10-20

A rewrite focused on complete, typed and reproducible IFC → OpenUSD conversion.
Mapping version 1.0.

### Added

- **Complete IFC data mapping**: every explicit EXPRESS attribute as a typed
  `ifc:*` attribute; all seven property kinds (single, enumerated, bounded,
  list, table, reference, complex), predefined and material property sets,
  quantities with units; type objects with property inheritance; semantic
  materials (layer, profile and constituent sets); classifications, documents,
  libraries, approvals, constraints, profiles, owner history and presentation
  layers; groups, systems and zones as USD collections; every other `IfcRel*`
  as a relationship prim. Optionally every relationship becomes a prim
  (`--all-relationship-prims`).
- **Coverage report**: each `IfcRoot` instance is classified as prim, native,
  attributes, filtered, skipped or missing.
- **Codeless USD schema** (`IfcEntityAPI`, `IfcPropertySetAPI`,
  `IfcQuantitySetAPI`, `IfcFileAPI`, `IfcGeoreferenceAPI`) with
  `ifcusd.schema.register()` and `ifcusd schema`.
- **Any IFC version**: schema fallback for identifiers the installed
  IfcOpenShell cannot parse (IFC4X1, IFC4X2, IFC4X3 final/TC1/ADD1,
  IFC4_ADD1/ADD2, IFC2X3_TC1…), with dropped instances reported; `.ifczip`
  input; experimental **IFC5 / IFCX** input with layer composition,
  references and point clouds.
- **Geometry**: local meshes with double-precision relative transforms;
  instancing of shared type geometry; each representation context as its own
  prim (`Body`, `Axis`, …); curves as `BasisCurves`; per-face materials as
  `GeomSubset`s; hidden spaces and openings; embedded textures written to disk.
- **Materials**: IFC surface styles mapped to `UsdPreviewSurface` (PBR
  approximation, original values kept), plus `OmniPBR` MDL in the Omniverse
  profile.
- **Target profiles** `generic`, `omniverse` and `unity`; `.usdz` output;
  metadata sidecar JSON plus a Unity editor script and runtime component.
- **Georeferencing and units** on the default prim (`ifc:geo:*`,
  `ifc:units:*`).
- `UsdSemantics` class labels for Omniverse Replicator / Isaac Sim.
- `ifcusd validate` (OpenUSD validators, structural checks, completeness
  against the source IFC) and `ifcusd info`.
- Deterministic, byte-identical output for any thread count; provenance
  (source SHA-256, tool versions, options) in `customLayerData`.
- Test suite generating IFC2X3/IFC4/IFC4X3 fixtures, geometry accuracy and
  determinism tests; CI on Linux and Windows for Python 3.10–3.14.
- Documentation website (static, MkDocs Material) in `website/`, published to
  GitHub Pages by a workflow: installation, quick start, tutorial, CLI and
  Python guides, recipes, troubleshooting, the IFC ↔ USD mapping
  specification, the USD schema, file formats, an API reference generated
  from docstrings, and a survey of existing methods.

### Changed

- Default root prim is `/World` (was `/IFC`).
- Meshes are written in local coordinates under transforms (was baked world
  coordinates; `--world-coords` keeps the old behaviour).
- USD files are authored through the Sdf API, which is much faster for large
  models.
- Missing dependencies are reported instead of being installed automatically
  (`--install-deps` to opt in).

### Fixed

- `metersPerUnit` was set to the IFC project length unit while geometry was in
  metres, so millimetre models appeared 1,000 times too small.
- Material bindings now apply `MaterialBindingAPI`, as required by current
  OpenUSD.
- Element names and properties with non-ASCII characters are preserved in
  `displayName` instead of being stripped.

### Removed

- The `ifc:metadata` JSON attribute (replaced by typed attributes and
  relationships).
- Modules `ifcusd.geometry`, `ifcusd.hierarchy`, `ifcusd.metadata` and
  `ifcusd.usd_writer` (replaced by `ifcusd.ifc.*` and `ifcusd.usd.*`).

## [0.1.0]

- Initial release: tessellated meshes, spatial hierarchy and IFC metadata as
  attributes.
