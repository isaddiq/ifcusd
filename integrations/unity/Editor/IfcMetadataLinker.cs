// IfcUSD - editor command that attaches IFC metadata from an IfcUSD sidecar
// JSON file (`--metadata-json`) to a USD model imported into the scene.
//
// Usage: select the root GameObject of the imported model, then choose
// "Tools > IfcUSD > Attach IFC Metadata..." and pick the .json file.

using System.IO;
using UnityEditor;
using UnityEngine;

namespace IfcUsd.Editor
{
    public static class IfcMetadataLinker
    {
        [MenuItem("Tools/IfcUSD/Attach IFC Metadata...")]
        public static void AttachFromFile()
        {
            var root = Selection.activeTransform;
            if (root == null)
            {
                EditorUtility.DisplayDialog("IfcUSD", "Select the root GameObject of the imported USD model first.", "OK");
                return;
            }
            var path = EditorUtility.OpenFilePanel("IfcUSD metadata", "", "json");
            if (string.IsNullOrEmpty(path))
            {
                return;
            }
            var document = JsonUtility.FromJson<IfcMetadataDocument>(File.ReadAllText(path));
            if (document == null || document.format != "ifcusd-metadata/1")
            {
                EditorUtility.DisplayDialog("IfcUSD", "This is not an IfcUSD metadata file.", "OK");
                return;
            }
            int attached = Attach(root, document);
            Debug.Log($"IfcUSD: attached IFC metadata to {attached} of {document.objects.Count} objects from {document.source}.");
        }

        [MenuItem("Tools/IfcUSD/Attach IFC Metadata...", true)]
        private static bool CanAttach() => Selection.activeTransform != null;

        /// <summary>Attach records to GameObjects whose hierarchy path matches the USD prim path.</summary>
        public static int Attach(Transform root, IfcMetadataDocument document)
        {
            int attached = 0;
            foreach (var record in document.objects)
            {
                var target = Find(root, record.path);
                if (target == null)
                {
                    continue;
                }
                var component = target.GetComponent<IfcMetadata>();
                if (component == null)
                {
                    component = Undo.AddComponent<IfcMetadata>(target.gameObject);
                }
                else
                {
                    Undo.RecordObject(component, "Attach IFC metadata");
                }
                component.Apply(record);
                EditorUtility.SetDirty(component);
                attached++;
            }
            return attached;
        }

        // USD "/World/Project/Site/Wall" may map to "World/Project/..." below the
        // selected object or to "Project/..." when the selection is the World node.
        private static Transform Find(Transform root, string usdPath)
        {
            var relative = usdPath.TrimStart('/');
            var found = root.Find(relative);
            if (found != null)
            {
                return found;
            }
            int slash = relative.IndexOf('/');
            return slash < 0 ? null : root.Find(relative.Substring(slash + 1));
        }
    }
}
