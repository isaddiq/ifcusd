"""The IfcUSD codeless USD schema and the names it defines.

Register the schema with OpenUSD before opening stages to get schema-aware
behaviour (``prim.HasAPI("IfcEntityAPI")``, fallback values, documentation)::

    import ifcusd.schema
    ifcusd.schema.register()

or set ``PXR_PLUGINPATH_NAME`` to :func:`plugin_path` before starting
usdview, Omniverse or any other OpenUSD application.
"""

from __future__ import annotations

from pathlib import Path

from ifcusd._version import SCHEMA_VERSION

ENTITY_API = "IfcEntityAPI"
FILE_API = "IfcFileAPI"
GEOREFERENCE_API = "IfcGeoreferenceAPI"
PROPERTY_SET_API = "IfcPropertySetAPI"
QUANTITY_SET_API = "IfcQuantitySetAPI"

NAMESPACE = "ifc"
PSET_NAMESPACE = "ifc:pset"
QTO_NAMESPACE = "ifc:qto"

# Attributes declared by IfcEntityAPI (all other ifc:* attributes are custom).
ENTITY_ATTRIBUTES = {
    "ifc:class": "token",
    "ifc:globalId": "string",
    "ifc:stepId": "int",
    "ifc:name": "string",
    "ifc:description": "string",
    "ifc:objectType": "string",
    "ifc:tag": "string",
    "ifc:longName": "string",
    "ifc:predefinedType": "token",
    "ifc:parentRelationship": "token",
}
ENTITY_RELATIONSHIPS = {
    "ifc:isTypedBy",
    "ifc:hasMaterial",
    "ifc:hasClassification",
    "ifc:hasDocument",
    "ifc:hasLibrary",
    "ifc:hasApproval",
    "ifc:hasConstraint",
    "ifc:hasProfile",
    "ifc:fills",
    "ifc:ownerHistory",
}

__all__ = [
    "ENTITY_API",
    "FILE_API",
    "GEOREFERENCE_API",
    "PROPERTY_SET_API",
    "QUANTITY_SET_API",
    "SCHEMA_VERSION",
    "plugin_path",
    "register",
]


def plugin_path() -> Path:
    """Directory containing ``plugInfo.json`` for the IfcUSD schema."""
    return Path(__file__).resolve().parent / "resources"


def register() -> bool:
    """Register the schema plugin with OpenUSD's plugin registry.

    Returns ``True`` when the schema is available afterwards. Call this before
    the first stage is opened in the process; OpenUSD builds its schema
    registry once.
    """
    from pxr import Plug, Usd

    Plug.Registry().RegisterPlugins(str(plugin_path()))
    return bool(Usd.SchemaRegistry.GetTypeFromSchemaTypeName(ENTITY_API))
