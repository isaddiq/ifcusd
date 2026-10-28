---
title: IfcUSD — IFC to OpenUSD without losing the BIM
description: Convert IFC2X3, IFC4, IFC4X3 and IFC5 building models to OpenUSD for NVIDIA Omniverse, Unity and any OpenUSD pipeline, with every property, type, material and relationship preserved.
hide:
  - navigation
  - toc
---

<div class="ifc-hero" markdown>

<div markdown>

<h1>IFC to <span class="ifc-gradient">OpenUSD</span>,<br>without losing the BIM.</h1>

<p class="ifc-hero__lead">
IfcUSD is a Python package, a command-line tool and a browser UI. It
converts building models in every IFC version to OpenUSD, including geometry, spatial
structure, every attribute, property and quantity, types, materials,
classifications, groups and relationships, and proves that nothing was
lost. It also converts back: robot and simulation results recorded in USD,
physics included, return to the IFC model as standard, self-describing IFC
data, and the next conversion hands them to the simulation again.
</p>

[Get started :material-arrow-right:](getting-started/installation.md){ .md-button .md-button--primary }
[Quick start](getting-started/quickstart.md){ .md-button }
[IfcUSD Studio](guide/studio.md){ .md-button }
[:fontawesome-brands-github: GitHub](https://github.com/isaddiq/ifcusd){ .md-button }

<div class="ifc-pills">
<span>IFC2X3</span><span>IFC4</span><span>IFC4X3</span><span>IFC5 · IFCX</span>
<span>USD → IFC</span><span>Browser UI</span><span>NVIDIA Omniverse</span><span>Unity 6</span><span>Python 3.10 – 3.14</span><span>MIT</span>
</div>

</div>

<div class="ifc-terminal">
<div class="ifc-terminal__bar"><i></i><i></i><i></i></div>
<pre><code><span class="t-prompt">$</span> pip install "git+https://github.com/isaddiq/ifcusd.git"
<span class="t-prompt">$</span> ifcusd convert model.ifc model.usdc --validate
<span class="t-dim">input   model.ifc  [IFC4]
output  model.usdc  [profile: generic]</span>
prims 84 | elements 15 | spatial 7 | types 16 | relationships 57
geometry: meshes 14 | triangles 1167 | materials 10
data: property sets 20 | properties 43 | quantities 25
coverage of 117 IfcRoot instances: prim 40, native 57,
         attributes 20, filtered 0, skipped 0, <span class="t-ok">missing 0</span>
<span class="t-ok">completed in 0.31s (complete)</span>
model.usdc: <span class="t-ok">OK</span> (0 errors, 0 warnings)
  missingObjects: <span class="t-ok">0</span>   missingPropertyValues: <span class="t-ok">0</span>
<span class="t-prompt">$</span> ifcusd ui
<span class="t-dim">IfcUSD Studio is running at http://127.0.0.1:8765/</span></code></pre>
</div>

</div>

<h2 class="ifc-section-title">Why IfcUSD</h2>
<p class="ifc-section-lead">Most IFC → USD routes keep the geometry and drop the building information. IfcUSD keeps all of it, in typed, documented USD that every tool can read.</p>

<div class="grid cards" markdown>

-   :material-database-check:{ .lg .middle } __Complete by construction__

    ---

    Every `IfcRoot` instance is accounted for in a coverage report, and
    `ifcusd validate --source` re-reads the IFC to prove that every object and
    property value arrived.

    [:octicons-arrow-right-24: Reports and validation](guide/validation.md)

-   :material-format-list-bulleted-type:{ .lg .middle } __Typed, not stringified__

    ---

    Booleans, integers, measures with units, enumerations with allowed
    values, lists, tables, bounded and complex properties. Original IFC
    names stay in `displayName`.

    [:octicons-arrow-right-24: What gets written](guide/output.md)

-   :material-history:{ .lg .middle } __Every IFC version__

    ---

    IFC2X3, IFC4 and IFC4X3 natively; IFC4X1, IFC4X2, IFC4X3-TC1 and
    IFC4-ADD2 through schema fallback with loss reporting; IFC5 (`.ifcx`)
    with layer composition; `.ifczip`.

    [:octicons-arrow-right-24: IFC5 support](guide/ifc5.md)

-   :material-cube-outline:{ .lg .middle } __A real USD schema__

    ---

    A versioned, codeless USD schema (`IfcEntityAPI`, `IfcPropertySetAPI`,
    `IfcQuantitySetAPI`…) documents the mapping. It is optional at runtime,
    so files open everywhere.

    [:octicons-arrow-right-24: USD schema](reference/usd-schema.md)

-   :material-target:{ .lg .middle } __Accurate geometry__

    ---

    Local meshes under double-precision transforms, native instancing of
    type geometry, per-face materials. Matches IfcOpenShell's world
    geometry to 5 × 10⁻⁵ m.

    [:octicons-arrow-right-24: Mapping specification](reference/schema-mapping.md)

-   :material-repeat:{ .lg .middle } __Reproducible__

    ---

    Byte-identical output for any thread count. Source SHA-256, tool versions
    and options are recorded in every file. Suitable for research and audit
    trails.

    [:octicons-arrow-right-24: Reproducibility](reference/schema-mapping.md#5-reproducibility)

-   :material-swap-horizontal:{ .lg .middle } __Both directions__

    ---

    `ifcusd to-ifc` writes USD back to IFC: edited values, moved objects,
    new robots and sensors, physics, trajectories and sensor time series,
    as property sets and templates any IFC tool can read. The next
    `ifcusd convert` turns the physics back into native `UsdPhysics`.

    [:octicons-arrow-right-24: USD to IFC and robot data](guide/usd-to-ifc.md)

-   :material-robot-industrial:{ .lg .middle } __Robot data in BIM__

    ---

    Train robots in Isaac Sim on the real building, then hand the results to
    code checking and design: an IFC extension built only from standard
    mechanisms, declared inside each file.

    [:octicons-arrow-right-24: IFC extension](reference/ifc-extension.md)

</div>

<h2 class="ifc-section-title">How it works</h2>
<p class="ifc-section-lead">The scene is planned before anything is written, so paths are stable and the output does not depend on processing order.</p>

<div class="ifc-pipeline">
<div><strong>Read</strong><p>IfcOpenShell opens IFC-SPF or ifcZIP in any schema version, with automatic fallback for unknown identifiers.</p></div>
<div><strong>Plan</strong><p>Every entity gets a parent, a USD type, a model kind and a stable name; relationships are classified as native or prim.</p></div>
<div><strong>Write</strong><p>Typed attributes, property sets, relationships and collections, then multithreaded tessellation and instancing.</p></div>
<div><strong>Verify</strong><p>Coverage report, OpenUSD validators and a round-trip completeness check against the source IFC.</p></div>
</div>

<h2 class="ifc-section-title">Or point and click</h2>
<p class="ifc-section-lead">IfcUSD Studio runs in your browser, on your computer. Convert in either direction, browse the IFC data and physics of any file, and read how IFC maps to USD. Start it with <code>ifcusd ui</code>, or <code>python app.py</code> in a clone of the repository.</p>

![IfcUSD Studio converting an IFC model for Omniverse and Isaac Sim](assets/studio-convert.png){ loading=lazy width="880" style="display:block;margin:0 auto;border:1px solid var(--md-default-fg-color--lightest);border-radius:8px" }

[Open the IfcUSD Studio guide :octicons-arrow-right-24:](guide/studio.md)

<h2 class="ifc-section-title">Built for your target</h2>

=== "NVIDIA Omniverse"

    ```bash
    ifcusd convert model.ifc model.usdc --profile omniverse
    ```

    OmniPBR (MDL) and UsdPreviewSurface materials, instanceable prototypes for
    repeated type geometry, and Replicator-ready semantic labels by IFC class.
    IFC data appears as prim properties.
    [Omniverse guide :octicons-arrow-right-24:](guide/omniverse-and-unity.md#nvidia-omniverse)

=== "Unity 6"

    ```bash
    ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
    ```

    Y-up, importer-safe content (meshes, transforms, UsdPreviewSurface) and a
    metadata sidecar that the bundled editor script attaches to the imported
    GameObjects.
    [Unity guide :octicons-arrow-right-24:](guide/omniverse-and-unity.md#unity-unity-6-20231)

=== "Python"

    ```python
    from ifcusd import ConversionOptions, convert, validate_usd

    result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))
    assert result.report.complete
    assert validate_usd("model.usdc", source="model.ifc").ok
    ```

    [Python API :octicons-arrow-right-24:](guide/python-api.md)

=== "Any OpenUSD tool"

    ```bash
    ifcusd convert model.ifc model.usda
    ```

    Only core OpenUSD schemas, so usdview, Blender, Houdini, Maya and Apple
    tools open the output directly.
    [What gets written :octicons-arrow-right-24:](guide/output.md)

<h2 class="ifc-section-title">What you get</h2>

| IFC | OpenUSD |
| --- | --- |
| Spatial structure (`IfcProject` → `IfcSite` → `IfcBuilding` → `IfcBuildingStorey` …) | `Xform` hierarchy with model kinds and relative placements |
| Elements and their representations | `Xform` + `Body` `Mesh`, `Axis` `BasisCurves`; instanceable prototypes |
| Attributes (`GlobalId`, `Name`, `PredefinedType`, …) | `ifc:globalId`, `ifc:name`, `ifc:predefinedType` (`IfcEntityAPI`) |
| Property sets and quantities | `ifc:pset:<Set>:<Property>`, `ifc:qto:<Set>:<Quantity>` with units |
| Types, materials, classifications, documents | `rel ifc:isTypedBy`, `ifc:hasMaterial`, `ifc:hasClassification`, … |
| Groups, systems, zones, layers | `UsdCollectionAPI` collections |
| Other relationships | relationship prims under `/World/IfcData/Relationships` |
| Surface styles | `UsdPreviewSurface` (+ OmniPBR) materials |
| Units, georeferencing, header | `ifc:units:*`, `ifc:geo:*`, `ifc:file:*` on `/World` |
| `IfcUSD_Physics`, stored by a previous simulation | `UsdPhysics` mass, rigid bodies, colliders and physics materials |

<div class="ifc-cta" markdown>

## Convert your first model in two minutes

Install with pip, then run one command or open IfcUSD Studio. Open the result in Omniverse, Isaac Sim, Unity or usdview.

[Install IfcUSD](getting-started/installation.md){ .md-button .md-button--primary }
[Follow the tutorial](getting-started/tutorial.md){ .md-button }

</div>
