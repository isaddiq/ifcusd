# IFC5 (IFCX)

IFC5 is buildingSMART's next-generation IFC, currently in **alpha**. Its
exchange format, IFCX, is JSON, and its design borrows directly from OpenUSD:
nodes addressed by path, `children` and `inherits` composition, layered
federation in which later layers win, and namespaced attributes including
`usd::usdgeom::mesh` and `usd::xformop`.

IfcUSD reads IFCX without IfcOpenShell. It uses a Python port of
buildingSMART's reference composition algorithm.

```bash
ifcusd convert hello-wall.ifcx hello-wall.usdc
```

## Layers

IFC5 lets several parties contribute layers to one model. Pass extra layers
with `--layer`. They are composed on top of the input in the order given:

```bash
ifcusd convert hello-wall.ifcx hello-wall.usda --layer hello-wall-add-fire-rating-30.ifcx
```

```python
from ifcusd import convert

convert("hello-wall.ifcx", "hello-wall.usda", layers=["hello-wall-add-fire-rating-30.ifcx"])
```

Composition rules (from the reference implementation):

1. **Federation:** nodes from all layers are collected in layer order.
2. **Flattening:** nodes with the same path are merged; later layers win, and
   a `null` child, inherit or attribute removes the entry.
3. **Composition:** `inherits` copy the referenced node's children and
   attributes; `children` instantiate the referenced node under a name.

Unlike the TypeScript reference, composed nodes are copied rather than shared,
so overrides cannot leak between instances. Cycles raise a clear error.

## Mapping

| IFCX | USD |
| --- | --- |
| composed node, child name | prim, prim name |
| `usd::xformop.transform` | `xformOp:transform` |
| `usd::usdgeom::mesh` | `Mesh` with flat normals and extent |
| `usd::usdgeom::basiscurves` | `BasisCurves` (linear) |
| `usd::usdgeom::visibility` | `visibility` |
| `points::array`, `points::base64`, `pcd::base64` | `Points` with per-point colours |
| `bsi::ifc::class {code, uri}` | `ifc:class`, `ifc:classUri` (`IfcEntityAPI`) |
| `bsi::ifc::presentation::diffuseColor`, `opacity` | bound `UsdPreviewSurface` material and `displayColor` |
| `{"ref": "<node>"}` | USD relationship to the referenced prim(s) |
| any other `ns::a::b` | attribute `ns:a:b` (`-` becomes `_`); enums become `token` with `allowedTokens`; objects become nested namespaces |

The exact IFCX key of every attribute is kept in `customData.ifcxKey`, so the
mapping is reversible. Because IFC5 and IFC4 outputs share `ifc:class`, tools
built on IfcUSD output handle both.

```python
wall.GetAttribute("bsi:ifc:prop:FireRating").Get()                     # "R30"
wall.GetAttribute("bsi:ifc:prop:FireRating").GetMetadata("allowedTokens")  # ["R30", "R60"]
```

## Coverage

For IFCX the report counts nodes. Nodes used only through `inherits` (shared
materials or types, for example) appear under `attributes`, because their data
is merged into the prims that inherit them. Remote `imports` (schema
references) are recorded on `/World` as `ifcx:imports`; they are not fetched.

!!! warning "Alpha format"
    IFC5 is still changing. IfcUSD accepts unknown namespaces and attributes,
    so files written against newer alpha schemas still convert, but the
    mapping of new `bsi::*` features may need updates as the standard
    matures.
