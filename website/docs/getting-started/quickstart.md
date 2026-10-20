# Quick start

## Convert from the command line

```bash
ifcusd convert model.ifc model.usdc
```

That is all a conversion needs. The output format follows the file extension:

| Extension | Format | Use it for |
| --- | --- | --- |
| `.usdc` | binary crate | production, large models, fastest to load |
| `.usda` | text | inspection, diffs, debugging |
| `.usd` | binary crate | the generic USD extension |
| `.usdz` | zipped package (includes textures) | Unity, Apple tools, sharing a single file |

Pick a **profile** for your target application:

=== "Omniverse"

    ```bash
    ifcusd convert model.ifc model.usdc --profile omniverse
    ```

=== "Unity"

    ```bash
    ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
    ```

=== "Generic OpenUSD"

    ```bash
    ifcusd convert model.ifc model.usda
    ```

Add `--validate` to check the result against the source and `--report
report.json` to keep the full conversion report:

```bash
ifcusd convert model.ifc model.usdc --validate --report report.json
```

```text
input   model.ifc  [IFC4]
output  model.usdc  [profile: generic]
prims 84 | elements 15 | spatial 7 | types 16 | relationships 57
geometry: meshes 14 | instances 0 (prototypes 0) | curves 0 | triangles 1167 | materials 10
data: property sets 20 | properties 43 | quantities 25
coverage of 117 IfcRoot instances: prim 40, native 57, attributes 20, filtered 0, skipped 0, missing 0
completed in 0.47s (complete)
model.usdc: OK (0 errors, 0 warnings, 0 notes)
```

## Convert from Python

```python
from ifcusd import ConversionOptions, convert

result = convert("model.ifc", "model.usdc", ConversionOptions.for_profile("omniverse"))

print("\n".join(result.report.summary_lines()))
print(result.report.complete)          # True when nothing was lost
```

## Read IFC data from the USD file

Every IFC object is a prim with typed `ifc:*` attributes:

```python
from pxr import Usd

stage = Usd.Stage.Open("model.usdc")
for prim in stage.Traverse():
    if prim.GetAttribute("ifc:class").Get() == "IfcWall":
        print(
            prim.GetAttribute("ifc:globalId").Get(),
            prim.GetAttribute("ifc:name").Get(),
            prim.GetAttribute("ifc:pset:Pset_WallCommon:IsExternal").Get(),
        )
```

## Next steps

<div class="grid cards" markdown>

-   :material-school:{ .lg .middle } __Tutorial__

    ---

    A guided first conversion: inspect, convert, read back and validate.

    [:octicons-arrow-right-24: Tutorial](tutorial.md)

-   :material-console:{ .lg .middle } __Command line__

    ---

    Every command, option and exit code.

    [:octicons-arrow-right-24: CLI reference](../guide/cli.md)

-   :material-language-python:{ .lg .middle } __Python API__

    ---

    Options, results, reports and validation in code.

    [:octicons-arrow-right-24: Python API](../guide/python-api.md)

-   :material-sitemap:{ .lg .middle } __Output structure__

    ---

    Where every piece of IFC data ends up in USD.

    [:octicons-arrow-right-24: What gets written](../guide/output.md)

</div>
