from __future__ import annotations

import ifcusd.metadata as metadata_module
from ifcusd.metadata import extract_entity_metadata, metadata_json, normalize_value


class FakeIfcEntity:
    GlobalId = "2ABC"
    Name = "Wall Type"

    def id(self) -> int:
        return 42

    def is_a(self, value: str | None = None):
        if value is None:
            return "IfcWallType"
        return value == "IfcWallType"


class MetadataEntity:
    def __init__(self, step_id: int, ifc_class: str, name: str) -> None:
        self._id = step_id
        self._ifc_class = ifc_class
        self.GlobalId = f"GID{step_id}"
        self.Name = name
        self.HasAssociations = ()

    def id(self) -> int:
        return self._id

    def is_a(self, value: str | None = None):
        if value is None:
            return self._ifc_class
        return value == self._ifc_class


class FakeAssociation:
    def __init__(self, ifc_class: str, attr_name: str, related: MetadataEntity) -> None:
        self._ifc_class = ifc_class
        setattr(self, attr_name, related)

    def is_a(self, value: str | None = None):
        if value is None:
            return self._ifc_class
        return value == self._ifc_class


def test_normalize_value_converts_ifc_entity_to_reference() -> None:
    value = normalize_value(FakeIfcEntity())

    assert value["ifc_class"] == "IfcWallType"
    assert value["ifc_step_id"] == 42
    assert value["GlobalId"] == "2ABC"


def test_metadata_json_is_stable_and_compact() -> None:
    assert metadata_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_extract_entity_metadata_includes_properties_quantities_materials_and_relationships(
    monkeypatch,
) -> None:
    entity = MetadataEntity(1, "IfcWall", "Wall A")
    aggregate = MetadataEntity(2, "IfcBuildingStorey", "Level 1")
    container = MetadataEntity(3, "IfcSpace", "Room 101")
    type_entity = MetadataEntity(4, "IfcWallType", "Exterior Wall Type")
    group = MetadataEntity(5, "IfcGroup", "Envelope")
    referenced = MetadataEntity(6, "IfcBuildingStorey", "Referenced Level")
    layer = MetadataEntity(7, "IfcPresentationLayerAssignment", "A-WALL")
    opening = MetadataEntity(8, "IfcOpeningElement", "Door Opening")
    voided = MetadataEntity(9, "IfcWall", "Host Wall")
    material = MetadataEntity(10, "IfcMaterial", "Concrete")
    style = MetadataEntity(11, "IfcSurfaceStyle", "Concrete Style")
    classification = MetadataEntity(12, "IfcClassificationReference", "Uniclass")
    entity.HasAssociations = (
        FakeAssociation(
            "IfcRelAssociatesClassification",
            "RelatingClassification",
            classification,
        ),
    )

    util = metadata_module.ifcopenshell.util.element
    monkeypatch.setattr(util, "get_aggregate", lambda item: aggregate if item is entity else None)
    monkeypatch.setattr(
        util,
        "get_container",
        lambda item, should_get_direct=False: container if item is entity else None,
    )
    monkeypatch.setattr(util, "get_type", lambda item: type_entity if item is entity else None)
    monkeypatch.setattr(util, "get_groups", lambda item: [group] if item is entity else [])
    monkeypatch.setattr(
        util,
        "get_referenced_structures",
        lambda item: [referenced] if item is entity else [],
        raising=False,
    )
    monkeypatch.setattr(util, "get_layers", lambda model, item: [layer] if item is entity else [])
    monkeypatch.setattr(util, "get_filled_void", lambda item: opening if item is entity else None)
    monkeypatch.setattr(util, "get_voided_element", lambda item: voided if item is entity else None)
    monkeypatch.setattr(util, "get_material", lambda item: material if item is entity else None)
    monkeypatch.setattr(util, "get_styles", lambda item: [style] if item is entity else [])
    monkeypatch.setattr(
        util,
        "get_psets",
        lambda item, psets_only=False, qtos_only=False: (
            {"Pset_WallCommon": {"FireRating": "EI60"}}
            if psets_only
            else {"Qto_WallBaseQuantities": {"NetVolume": 3.5}}
            if qtos_only
            else {}
        ),
    )

    metadata = extract_entity_metadata(entity, model=object())

    assert metadata["ifc_class"] == "IfcWall"
    assert metadata["property_sets"]["Pset_WallCommon"]["FireRating"] == "EI60"
    assert metadata["quantities"]["Qto_WallBaseQuantities"]["NetVolume"] == 3.5
    assert metadata["material"]["Name"] == "Concrete"
    assert metadata["styles"][0]["Name"] == "Concrete Style"

    relationships = metadata["relationships"]
    assert relationships["aggregate_parent"]["Name"] == "Level 1"
    assert relationships["spatial_container"]["Name"] == "Room 101"
    assert relationships["type"]["Name"] == "Exterior Wall Type"
    assert relationships["groups"][0]["Name"] == "Envelope"
    assert relationships["referenced_structures"][0]["Name"] == "Referenced Level"
    assert relationships["presentation_layers"][0]["Name"] == "A-WALL"
    assert relationships["fills_opening"]["Name"] == "Door Opening"
    assert relationships["voids_element"]["Name"] == "Host Wall"
    assert relationships["external_references"][0]["relationship"] == "classification"
