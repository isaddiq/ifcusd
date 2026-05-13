from __future__ import annotations

import json
from typing import Any

import ifcopenshell.util.element


BASE_IFC_ATTRIBUTES = (
    "GlobalId",
    "Name",
    "Description",
    "ObjectType",
    "Tag",
    "PredefinedType",
    "LongName",
    "Phase",
)


def extract_entity_metadata(
    entity: Any,
    model: Any | None = None,
    *,
    include_properties: bool = True,
    include_quantities: bool = True,
    include_materials: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "ifc_class": _entity_class(entity),
        "ifc_step_id": _entity_id(entity),
    }

    for attr_name in BASE_IFC_ATTRIBUTES:
        value = _safe_getattr(entity, attr_name)
        if value is not None:
            metadata[attr_name] = normalize_value(value)

    type_entity = _safe_util_call(ifcopenshell.util.element.get_type, entity)
    if type_entity is not None:
        metadata["type"] = entity_reference(type_entity)

    relationships = extract_relationship_metadata(entity, model)
    if relationships:
        metadata["relationships"] = relationships

    if include_properties:
        property_sets = _safe_get_psets(entity, psets_only=True)
        if property_sets:
            metadata["property_sets"] = normalize_value(property_sets)

    if include_quantities:
        quantities = _safe_get_psets(entity, qtos_only=True)
        if quantities:
            metadata["quantities"] = normalize_value(quantities)

    if include_materials:
        material = _safe_util_call(ifcopenshell.util.element.get_material, entity)
        if material is not None:
            metadata["material"] = normalize_value(material)
        styles = _safe_util_call(ifcopenshell.util.element.get_styles, entity)
        if styles:
            metadata["styles"] = normalize_value(styles)

    return metadata


def extract_relationship_metadata(entity: Any, model: Any | None = None) -> dict[str, Any]:
    relationships: dict[str, Any] = {}

    aggregate = _safe_util_call(ifcopenshell.util.element.get_aggregate, entity)
    if aggregate is not None:
        relationships["aggregate_parent"] = entity_reference(aggregate)

    container = _safe_util_call(
        ifcopenshell.util.element.get_container,
        entity,
        should_get_direct=True,
    )
    if container is not None:
        relationships["spatial_container"] = entity_reference(container)

    type_entity = _safe_util_call(ifcopenshell.util.element.get_type, entity)
    if type_entity is not None and type_entity is not entity:
        relationships["type"] = entity_reference(type_entity)

    groups = _safe_util_call(ifcopenshell.util.element.get_groups, entity)
    if groups:
        relationships["groups"] = normalize_value(groups)

    referenced_structures = _safe_util_call(
        ifcopenshell.util.element.get_referenced_structures,
        entity,
    )
    if referenced_structures:
        relationships["referenced_structures"] = normalize_value(referenced_structures)

    if model is not None:
        layers = _safe_util_call(ifcopenshell.util.element.get_layers, model, entity)
        if layers:
            relationships["presentation_layers"] = normalize_value(layers)

    filled_void = _safe_util_call(ifcopenshell.util.element.get_filled_void, entity)
    if filled_void is not None:
        relationships["fills_opening"] = entity_reference(filled_void)

    voided_element = _safe_util_call(ifcopenshell.util.element.get_voided_element, entity)
    if voided_element is not None:
        relationships["voids_element"] = entity_reference(voided_element)

    external_references = _extract_external_references(entity)
    if external_references:
        relationships["external_references"] = external_references

    return relationships


def entity_reference(entity: Any) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "ifc_class": _entity_class(entity),
        "ifc_step_id": _entity_id(entity),
    }
    for attr_name in ("GlobalId", "Name", "Description", "ObjectType", "Tag"):
        value = _safe_getattr(entity, attr_name)
        if value is not None:
            reference[attr_name] = normalize_value(value)
    return reference


def normalize_value(value: Any, *, _depth: int = 0) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    wrapped = _safe_getattr(value, "wrappedValue")
    if wrapped is not None:
        return normalize_value(wrapped, _depth=_depth + 1)

    if _looks_like_entity(value):
        return entity_reference(value)

    if isinstance(value, dict):
        return {
            str(key): normalize_value(nested_value, _depth=_depth + 1)
            for key, nested_value in value.items()
            if nested_value is not None
        }

    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item, _depth=_depth + 1) for item in value]

    if _depth < 2:
        extracted = _extract_public_ifc_fields(value)
        if extracted:
            return extracted

    return str(value)


def metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_get_psets(entity: Any, *, psets_only: bool = False, qtos_only: bool = False) -> dict[str, Any]:
    try:
        return ifcopenshell.util.element.get_psets(
            entity,
            psets_only=psets_only,
            qtos_only=qtos_only,
        )
    except Exception:
        return {}


def _safe_util_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception:
        return None


def _extract_public_ifc_fields(value: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for attr_name in ("Name", "Description", "Category", "Priority", "LayerThickness"):
        attr_value = _safe_getattr(value, attr_name)
        if attr_value is not None:
            fields[attr_name] = normalize_value(attr_value)
    entity_class = _entity_class(value)
    if entity_class != value.__class__.__name__:
        fields["ifc_class"] = entity_class
    return fields


def _safe_getattr(value: Any, attr_name: str) -> Any:
    try:
        return getattr(value, attr_name)
    except Exception:
        return None


def _extract_external_references(entity: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for rel in _safe_getattr(entity, "HasAssociations") or []:
        if _is_ifc_class(rel, "IfcRelAssociatesClassification"):
            related = _safe_getattr(rel, "RelatingClassification")
            if related is not None:
                references.append(
                    {
                        "relationship": "classification",
                        "reference": entity_reference(related),
                    }
                )
        elif _is_ifc_class(rel, "IfcRelAssociatesDocument"):
            related = _safe_getattr(rel, "RelatingDocument")
            if related is not None:
                references.append(
                    {
                        "relationship": "document",
                        "reference": entity_reference(related),
                    }
                )
        elif _is_ifc_class(rel, "IfcRelAssociatesLibrary"):
            related = _safe_getattr(rel, "RelatingLibrary")
            if related is not None:
                references.append(
                    {
                        "relationship": "library",
                        "reference": entity_reference(related),
                    }
                )
    return references


def _looks_like_entity(value: Any) -> bool:
    return callable(_safe_getattr(value, "is_a")) and callable(_safe_getattr(value, "id"))


def _entity_class(entity: Any) -> str:
    try:
        return str(entity.is_a())
    except Exception:
        return entity.__class__.__name__


def _entity_id(entity: Any) -> int | None:
    try:
        return int(entity.id())
    except Exception:
        return None


def _is_ifc_class(entity: Any, ifc_class: str) -> bool:
    try:
        return bool(entity.is_a(ifc_class))
    except Exception:
        try:
            return entity.is_a() == ifc_class
        except Exception:
            return False
