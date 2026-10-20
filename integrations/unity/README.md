# IfcUSD for Unity

Attaches IFC metadata to models imported with Unity's USD Importer.

1. Convert with a metadata sidecar:

   ```bash
   ifcusd convert model.ifc model.usdz --profile unity --metadata-json model.ifcusd.json
   ```

2. Copy this folder into your project as `Assets/IfcUSD/`. `Runtime/` holds the
   `IfcMetadata` component; `Editor/` holds the menu command.
3. Import `model.usdz`, place it in a scene, select its root object and run
   **Tools ▸ IfcUSD ▸ Attach IFC Metadata…**, choosing `model.ifcusd.json`.

Objects are matched by their USD prim path (`/World/<Project>/<Site>/…`). The
command is undoable. See [../../docs/omniverse-and-unity.md](../../docs/omniverse-and-unity.md)
for details.
