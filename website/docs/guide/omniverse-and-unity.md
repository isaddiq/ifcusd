# Using IfcUSD output in NVIDIA Omniverse and Unity

IfcUSD has a *profile* per target. A profile only changes defaults; every
setting can still be overridden on the command line or in
`ConversionOptions.for_profile(...)`.

| Setting | `generic` | `omniverse` | `unity` |
| --- | --- | --- | --- |
| Up axis | Z | Z | **Y** (root `rotateX -90`) |
| Units (`metersPerUnit`) | 1 | 1 | 1 |
| Shared geometry | instanceable prototypes | instanceable prototypes | **inline meshes** |
| Curve representations (axes, alignments) | yes (`guide` purpose) | yes | **no** |
| Space volumes | hidden | hidden | **not written** |
| Materials | UsdPreviewSurface | UsdPreviewSurface **+ OmniPBR (MDL)** | UsdPreviewSurface |
| Semantic labels | `SemanticsLabelsAPI` | `SemanticsLabelsAPI` **+ legacy `SemanticsAPI`** | off |
| IFC data | attributes | attributes | attributes **+ `--metadata-json` sidecar** |

## NVIDIA Omniverse

```bash
ifcusd convert model.ifc model.usdc --profile omniverse --validate
```

* **Open** the `.usdc` in USD Composer or any Kit app, or reference it into a
  larger stage. The default prim is `/World`, so a plain reference works. The
  file declares `metersPerUnit = 1` and Z-up. If your stage uses different
  metrics, let Kit's metrics handling correct the reference.
* **IFC data** is on each prim as `ifc:*` properties. Original IFC names are
  in `displayName`, and the property-set name in `displayGroup`. Groups,
  systems, zones and presentation layers are USD collections
  (`collection:members`), so they can be used for selection and visibility.
* **Materials** carry both a `UsdPreviewSurface` network and an `OmniPBR` MDL
  network on the `mdl` render context, which RTX uses. Transparent IFC styles
  set `enable_opacity`. Outside Omniverse, OpenUSD cannot resolve
  `OmniPBR.mdl`. `ifcusd validate` reports that as a note, not an error.
* **Instancing.** Repeated type geometry (furniture, doors, windows, fixings)
  is written once under `/World/Prototypes` and instanced with
  `instanceable = true`, which reduces memory and load time in RTX.
* **Synthetic data.** Every element has `semantics:labels:class = ["IfcWall"]`
  (or its IFC class). Replicator and Isaac Sim pick these up for semantic and
  instance segmentation by IFC class, and the profile also writes the legacy
  `SemanticsAPI:Semantics` attributes used by older releases.
* **Schema-aware tools (optional).** To make Kit's USD runtime know the IfcUSD
  API schemas, add the schema folder to the plugin path before Kit starts:

  ```bash
  ifcusd schema          # prints the folder
  set PXR_PLUGINPATH_NAME=<that folder>        # Windows (cmd)
  export PXR_PLUGINPATH_NAME=<that folder>     # Linux
  ```

  Files open the same without it. The plugin only adds `HasAPI` queries,
  fallbacks and documentation.
* **Running inside Kit's Python.** Kit already ships `pxr`. To use IfcUSD as a
  library inside Kit, install it without the `usd-core` dependency:
  `pip install --no-deps ifcusd ifcopenshell numpy`, using wheels that match
  Kit's Python version. Converting outside Kit with the CLI avoids this
  entirely.
* **Very large coordinates.** Placements are stored in double precision, so
  georeferenced models keep their accuracy in the file. If a model sits far
  from the origin, add a translation on the prim that references it in your
  stage to keep the rendered geometry near the origin. The original
  georeferencing stays in `ifc:geo:*` on `/World`.

## Unity (Unity 6 / 2023.1+)

Unity reads USD through its USD Importer package (`com.unity.importer.usd`),
which documents support for transforms, meshes, UsdPreviewSurface materials
and textures. `.usdz` files are imported automatically; for `.usd`, `.usda`
and `.usdc`, enable **Import** in the Inspector.

```bash
ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
```

1. Install the USD Importer from the Package Manager (it is pre-release, so
   you may need *Add package by name…* → `com.unity.importer.usd`).
2. Copy `model.usdz` into `Assets/`. Unity converts the meshes, materials and
   hierarchy into sub-assets; drag the model into a scene.
3. Unity's importer does not bring in custom USD attributes. To keep the IFC
   data, copy [`integrations/unity`](https://github.com/isaddiq/ifcusd/tree/main/integrations/unity) into
   `Assets/IfcUSD/`, select the imported root object in the scene, and choose
   **Tools ▸ IfcUSD ▸ Attach IFC Metadata…**, picking `model.ifcusd.json`.
   Every object with IFC data gets an `IfcMetadata` component (GlobalId,
   class, name and all properties with units):

   ```csharp
   var data = GetComponent<IfcUsd.IfcMetadata>();
   string fire = data.Get("Pset_WallCommon", "FireRating");
   ```

Notes:

* The Unity profile writes Y-up geometry. The original IFC Z-up coordinates
  are recovered by one root rotation, so no vertex is altered.
* Prim names default to `<Name>_<GlobalId>`, so GameObject names are unique
  and stable across re-exports. Use `--naming name` for shorter names.
* The metadata sidecar is plain JSON (`format: ifcusd-metadata/1`) keyed by
  USD prim path, so other engines and web viewers can use it as well.
* The C# scripts use only documented Unity APIs (`JsonUtility`, `MenuItem`,
  `Undo`). They are not exercised by this repository's automated tests, which
  have no Unity editor available.

## Other OpenUSD applications

The `generic` profile uses only core OpenUSD schemas (`Xform`, `Scope`,
`Mesh`, `BasisCurves`, `Points`, `GeomSubset`, `UsdShade`), so usdview,
Blender, Houdini, Maya and Apple's tools open it directly. `.usdz` output
packages any texture files that IFC styles reference.
