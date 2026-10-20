# Recipes

Short, tested solutions to common tasks. Each assumes `from pxr import Usd`
and an open stage where needed.

## Batch-convert a folder

=== "Python"

    ```python
    from pathlib import Path
    from ifcusd import ConversionOptions, IfcToUsdConverter

    source, target = Path("models"), Path("usd")
    converter = IfcToUsdConverter(ConversionOptions.for_profile("omniverse"))

    for ifc in sorted(source.glob("*.ifc")):
        result = converter.convert(
            ifc, target / f"{ifc.stem}.usdc", report_path=target / f"{ifc.stem}.report.json"
        )
        status = "complete" if result.report.complete else "; ".join(result.report.warnings)
        print(f"{ifc.name}: {status}")
    ```

=== "PowerShell"

    ```powershell
    New-Item -ItemType Directory -Force usd | Out-Null
    Get-ChildItem models\*.ifc | ForEach-Object {
        ifcusd convert $_.FullName "usd\$($_.BaseName).usdc" --profile omniverse --report "usd\$($_.BaseName).report.json"
    }
    ```

=== "bash"

    ```bash
    mkdir -p usd
    for f in models/*.ifc; do
        name="$(basename "${f%.*}")"
        ifcusd convert "$f" "usd/$name.usdc" --profile omniverse --report "usd/$name.report.json"
    done
    ```

## Index elements by GlobalId

```python
by_guid = {
    prim.GetAttribute("ifc:globalId").Get(): prim
    for prim in stage.Traverse()
    if prim.HasAttribute("ifc:globalId")
}
wall = by_guid["0OfZwWc8j9QP5uX8xPTxDH"]
```

## All elements of a class (including subclasses)

`ifc:class` stores the exact class. To include subclasses, ask IfcOpenShell
for the schema the file was read with:

```python
import ifcopenshell

schema_name = stage.GetPrimAtPath("/World").GetAttribute("ifc:file:schema").Get()   # e.g. "IFC4"
schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema_name)

def is_subclass(name: str, ancestor: str) -> bool:
    declaration = schema.declaration_by_name(name)
    while declaration is not None:
        if declaration.name() == ancestor:
            return True
        declaration = declaration.supertype()
    return False

# IFC4X3 renamed IfcBuildingElement to IfcBuiltElement
ancestor = "IfcBuiltElement" if schema_name.startswith("IFC4X3") else "IfcBuildingElement"
elements = [
    p for p in stage.Traverse()
    if p.HasAttribute("ifc:class") and is_subclass(p.GetAttribute("ifc:class").Get(), ancestor)
]
```

## Export quantities to CSV

```python
import csv

with open("quantities.csv", "w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["globalId", "class", "name", "set", "quantity", "value", "unit"])
    for prim in stage.Traverse():
        for attribute in prim.GetAuthoredAttributes():
            if not attribute.GetName().startswith("ifc:qto:"):
                continue
            writer.writerow([
                prim.GetAttribute("ifc:globalId").Get(),
                prim.GetAttribute("ifc:class").Get(),
                prim.GetAttribute("ifc:name").Get(),
                attribute.GetMetadata("displayGroup"),
                attribute.GetMetadata("displayName"),
                attribute.Get(),
                attribute.GetCustomDataByKey("ifcUnit"),
            ])
```

With pandas, collect the rows in a list instead and call
`pandas.DataFrame(rows)`.

## Members of groups, systems and zones

```python
from pxr import Usd

groups = stage.GetPrimAtPath("/World/IfcData/Groups")
for group in groups.GetChildren():
    members = Usd.CollectionAPI(group, "members").GetIncludesRel().GetTargets()
    print(group.GetAttribute("ifc:name").Get(), [str(path.name) for path in members])
```

## Place a converted building in a larger scene

Reference the converted file instead of editing it, so it can be
re-converted at any time:

```python
from pxr import Gf, Usd, UsdGeom

scene = Usd.Stage.CreateNew("site.usda")
UsdGeom.SetStageUpAxis(scene, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(scene, 1.0)
world = UsdGeom.Xform.Define(scene, "/World")
scene.SetDefaultPrim(world.GetPrim())

building = UsdGeom.Xform.Define(scene, "/World/BuildingA")
building.GetPrim().GetReferences().AddReference("model.usdc")
building.AddTranslateOp().Set(Gf.Vec3d(-120.0, -45.0, 0.0))  # bring the model near the origin
scene.Save()
```

## Hide or isolate a category in a viewer session

```python
from pxr import UsdGeom

for prim in stage.Traverse():
    if prim.GetAttribute("ifc:class").Get() in ("IfcSpace", "IfcSpatialZone"):
        UsdGeom.Imageable(prim).MakeInvisible()
```

Make changes like this in a session layer or override layer when the
converted file must stay untouched.

## Use the schema in queries

```python
import ifcusd.schema

ifcusd.schema.register()          # before opening the stage

from pxr import Usd

stage = Usd.Stage.Open("model.usdc")
for prim in stage.Traverse():
    if prim.HasAPI("IfcPropertySetAPI", "Pset_WallCommon"):
        print(prim.GetPath())
```

## Semantic labels for synthetic data

Every element carries `semantics:labels:class = ["<IfcClass>"]`
(`SemanticsLabelsAPI:class`), and the Omniverse profile also writes the
legacy `semantic:Semantics:params:*` attributes. Replicator and Isaac Sim
annotators therefore produce segmentation masks labelled by IFC class without
any extra labelling step.

```python
labels = prim.GetAttribute("semantics:labels:class").Get()   # ["IfcWall"]
```

## Smaller files for quick previews

```bash
ifcusd convert model.ifc preview.usdz --no-metadata --no-curves --linear-deflection 0.02
```

## Keep a conversion audit trail

```bash
ifcusd convert model.ifc model.usdc --report model.report.json --strict
ifcusd validate model.usdc --source model.ifc --json model.validation.json
```

The report records the source SHA-256 and all tool versions. Re-running the
same command with the same versions reproduces the file byte for byte.
