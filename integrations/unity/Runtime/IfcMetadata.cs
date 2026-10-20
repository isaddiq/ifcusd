// IfcUSD - IFC metadata for GameObjects imported from IfcUSD USD files.
//
// Unity's USD importer brings in geometry, transforms and materials but not
// custom USD attributes. Convert with `ifcusd convert model.ifc model.usdz
// --profile unity --metadata-json model.ifcusd.json` and use the editor menu
// "Tools > IfcUSD > Attach IFC Metadata..." to attach this component to every
// imported object that has IFC data.

using System;
using System.Collections.Generic;
using UnityEngine;

namespace IfcUsd
{
    [Serializable]
    public class IfcProperty
    {
        public string set;
        public string name;
        public string value;
        public string type;
        public string unit;
        public bool quantity;
    }

    [Serializable]
    public class IfcObjectRecord
    {
        public string path;
        public string globalId;
        public string ifcClass;
        public string name;
        public List<IfcProperty> properties = new List<IfcProperty>();
    }

    [Serializable]
    public class IfcMetadataDocument
    {
        public string format;
        public string converterVersion;
        public string schemaVersion;
        public string source;
        public string sourceSha256;
        public string ifcSchema;
        public string usdFile;
        public List<IfcObjectRecord> objects = new List<IfcObjectRecord>();
    }

    /// <summary>IFC identity and properties of one converted object.</summary>
    [DisallowMultipleComponent]
    public class IfcMetadata : MonoBehaviour
    {
        [Tooltip("USD prim path of this object in the converted file.")]
        public string usdPath;
        public string globalId;
        public string ifcClass;
        public string ifcName;
        public List<IfcProperty> properties = new List<IfcProperty>();

        /// <summary>Value of a property, or null when it is not present.</summary>
        public string Get(string propertySet, string propertyName)
        {
            foreach (var property in properties)
            {
                if (property.set == propertySet && property.name == propertyName)
                {
                    return property.value;
                }
            }
            return null;
        }

        public void Apply(IfcObjectRecord record)
        {
            usdPath = record.path;
            globalId = record.globalId;
            ifcClass = record.ifcClass;
            ifcName = record.name;
            properties = record.properties ?? new List<IfcProperty>();
        }
    }
}
