# Existing IFC → USD methods and how IfcUSD relates to them

This is a survey of the routes available in late 2025 for getting IFC data
into OpenUSD, what each preserves, and which ideas IfcUSD adopted or
extended. Sources are listed at the end; claims are limited to what those
sources and the cited code show.

## Routes

A 2025 study of IFC-to-USD pipelines (*Buildings*, 15(24):4503) groups
existing approaches into three families:

* **connector-based** (a plug-in in the authoring tool writes USD),
* **platform-mediated** (a platform such as Omniverse imports IFC), and
* **transcoding through intermediate formats** (IFC → glTF/OBJ/FBX → USD).

The study also observes that most approaches focus on geometry, and that a
systematic semantic mapping of IFC into USD is still missing. IfcUSD is a
direct transcoder (IFC → USD, no intermediate format) whose main goal is
exactly that semantic mapping.

## Tools and approaches

### IfcOpenShell `USDSerializer` (IfcConvert, C++, v0.8)

IfcOpenShell ships the beginnings of a USD serializer
(`src/serializers/USDSerializer.cpp`) when built with USD support. From the
v0.8.0 source:

* writes one `Xform` per product, with a relative `xformOp:transform`
  computed as `inverse(parent) · world`, and a `Mesh` child named after the
  representation context;
* creates `UsdPreviewSurface` materials named after the style (diffuse,
  opacity from transparency, optional specular workflow) and binds
  per-material `GeomSubset`s;
* sets `metersPerUnit = 1` and an optional Y-up axis, and adds a default
  distant light;
* writes **no IFC attributes, property sets, quantities, types, materials or
  relationships**, no instancing, and no provenance.

*Adopted in IfcUSD:* relative transforms and context-named geometry children
(also used by IFC5). *Extended:* full semantics, instancing, deterministic
naming, no scene lights in an asset file, and a validation and completeness
report.

### NVIDIA Omniverse HOOPS converter (`omni.kit.converter.hoops`)

Omniverse's CAD importer accepts `.ifc` and `.ifczip` through the commercial
HOOPS Exchange SDK. When *Convert Metadata* is enabled, IFC metadata and BIM
relationships are imported as USD attributes or relationships under the
`omni:hoops:metadata` namespace, gathered by a generic attribute traversal of
the HOOPS PRC tree.

*Characteristics:* runs only inside Omniverse, is closed source, and puts IFC
data in a vendor namespace, as generic attributes, without IFC typing, units
or a published schema.
*IfcUSD instead:* open source, runs anywhere Python and OpenUSD run, writes a
documented and versioned `ifc:` namespace with IFC value types, units and
original names, and registers a USD schema for it.

### Bonsai (BlenderBIM) + Blender's USD exporter

IfcOpenShell's documentation lists USD among the formats reachable through
Bonsai, which imports IFC into Blender and then uses Blender's exporter.
Fidelity is that of a Blender scene: geometry, hierarchy and materials, with
IFC semantics limited to what the exporter maps from Blender data. It is an
interactive, application-mediated route.

### Transcoding through glTF / OBJ / FBX

IfcConvert can write glTF (`.glb`), OBJ, DAE and others, which can then be
turned into USD with general converters. These formats have no place for IFC
property sets, types or relationships, so the result is geometry, materials
and names only. Unit and up-axis conversions add further ways to lose
information.

### Authoring-tool connectors

Vendor connectors (e.g. Omniverse connectors for Revit and other BIM tools)
write USD from the native model rather than from IFC. They are not an
open-standard route and are tied to the tool and its licence. The Omniverse
Unity connector does not support Unity 6; NVIDIA points users to Unity's own
USD packages instead.

### Unity's USD packages

Unity's USD Importer (`com.unity.importer.usd`, pre-release) imports
transforms, meshes, animation, cameras, a few light types, `UsdPreviewSurface`
materials and textures. `.usdz` is imported automatically; `.usd`/`.usda`/`.usdc`
after enabling *Import* in the Inspector. Custom attributes are not part of
the documented feature set.

*Consequence for IfcUSD:* the `unity` profile writes only what the importer
reads (Y-up, no instancing, no curves, no hidden space volumes, preview
surface only). IFC data travels in a sidecar JSON that the bundled Unity
editor script attaches to the imported GameObjects.

### Schema-generation proposals

On the AOUSD forum, NVIDIA described an experiment that auto-generates a
`schema.usda` from IFC data, explicitly a "strawman" pending the AOUSD AEC
interest group. AEC Magazine reported NVIDIA researching how to layer IFC
schemas on USD. Other participants proposed an IfcOpenShell-based USD file
format plugin.

*IfcUSD's position:* rather than one USD type per IFC class, it uses a small
codeless set of *applied API schemas* (`IfcEntityAPI`, multiple-apply
`IfcPropertySetAPI`/`IfcQuantitySetAPI`, `IfcFileAPI`,
`IfcGeoreferenceAPI`). The IFC class stays data, so the same files work for
every IFC version and remain readable without the plugin. See
[schema-mapping.md](schema-mapping.md#4-the-ifcusd-usd-schema).

### IFC5 / IFCX (buildingSMART, alpha)

IFC5 is being developed as a JSON format (IFCX) with USD-like concepts: nodes
addressed by path, `children` and `inherits` composition arcs, layered
federation where later layers win, and attributes in namespaces, including
`usd::usdgeom::mesh` and `usd::xformop`. The reference implementation's
composition code is in `src/ifcx-core/composition`.

*Adopted in IfcUSD:* a Python port of that composition (with memoized,
non-shared composed nodes and explicit cycle errors), a direct IFCX → USD
mapping, and the namespace convention (`ns::a::b` → `ns:a:b`). The IFC5 and
IFC4 mappings share `ifc:class`, so downstream tools can treat both alike.

## Feature comparison

| | IfcOpenShell USDSerializer | Omniverse HOOPS converter | glTF/OBJ route | **IfcUSD** |
| --- | --- | --- | --- | --- |
| Open source | yes | no | yes (tools) | **yes (MIT)** |
| Runs outside Omniverse | yes | no | yes | **yes** |
| IFC2X3 / IFC4 / IFC4X3 | as IfcOpenShell | per HOOPS | as IfcOpenShell | **yes, plus schema fallback for IFC4X1/4X2/4X3_TC1/4_ADD2…** |
| IFC5 (IFCX) | no | no | no | **yes (alpha format)** |
| Spatial hierarchy | yes | yes | partial | **yes, with relationship type recorded** |
| Relative double-precision placements | yes | – | no (baked) | **yes** |
| Attributes / property sets / quantities | no | generic attributes | no | **typed, with units and original names** |
| Types, materials, classifications, groups, layers | no | partial | no | **yes (relationships and collections)** |
| All other IfcRel* | no | partial | no | **yes (relationship prims)** |
| Instancing of shared geometry | no | – | no | **yes** |
| Published USD schema | no | no | no | **yes (codeless, versioned)** |
| Deterministic / provenance | no | – | no | **byte-identical; SHA-256, versions, options** |
| Completeness report and validation | no | no | no | **yes** |
| Omniverse-specific output (MDL, Replicator semantics) | no | native | no | **yes (profile)** |
| Unity-specific output | no | no | partial | **yes (profile + metadata sidecar)** |

“–” means the source does not document it.

## Sources

* Buildings 2025, 15(24), 4503: *From Building Deliverables to Open Scene
  Description: A Pipeline for Lifecycle 3D Interoperability* —
  <https://doi.org/10.3390/buildings15244503>
* AOUSD forum, *Integrating USD and IFC for the AEC Industry* —
  <https://forum.aousd.org/t/integrating-usd-and-ifc-for-the-aec-industry/1259>
* AEC Magazine, *Nvidia looking to encode the IFC schema in USD* —
  <https://aecmag.com/collaboration/nvidia-looking-to-encode-the-ifc-schema-in-usd/>
* NVIDIA, Omniverse HOOPS converter documentation —
  <https://docs.omniverse.nvidia.com/kit/docs/omni.kit.converter.hoops/510.6.0/Overview.html>
* IfcOpenShell `USDSerializer.cpp` (v0.8.0) —
  <https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/serializers/USDSerializer.cpp>
* IfcConvert documentation — <https://ifcopenshell.sourceforge.net/ifcconvert.html>
* Unity USD Importer manual —
  <https://docs.unity3d.com/Packages/com.unity.importer.usd@1.0/manual/index.html>
* NVIDIA, Omniverse Unity connector requirements —
  <https://docs.omniverse.nvidia.com/connect/latest/unity/requirements.html>
* buildingSMART IFC5-development —
  <https://github.com/buildingSMART/IFC5-development>
