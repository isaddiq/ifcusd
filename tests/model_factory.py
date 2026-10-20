"""Builds feature-rich IFC test models with IfcOpenShell's authoring API.

The same model is produced for IFC2X3, IFC4 and IFC4X3_ADD2 so every test can
run against every schema without shipping third-party sample files.
"""

from __future__ import annotations

from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import numpy as np

UNICODE_TEXT = "Ünïcødé – 数据"


def _api(module: str, function: str, model: ifcopenshell.file, **kwargs):
    import importlib

    return getattr(importlib.import_module(f"ifcopenshell.api.{module}"), function)(model, **kwargs)


def _place(
    model: ifcopenshell.file, product, x: float = 0.0, y: float = 0.0, z: float = 0.0, angle: float = 0.0
) -> None:
    matrix = np.eye(4)
    c, s = np.cos(angle), np.sin(angle)
    matrix[:2, :2] = [[c, -s], [s, c]]
    matrix[:3, 3] = [x, y, z]
    _api("geometry", "edit_object_placement", model, product=product, matrix=matrix, is_si=True)


def _box(model: ifcopenshell.file, context, size=(1.0, 1.0, 1.0)):
    sx, sy, sz = size
    vertices = [[(0, 0, 0), (sx, 0, 0), (sx, sy, 0), (0, sy, 0), (0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)]]
    faces = [[(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]]
    return _api("geometry", "add_mesh_representation", model, context=context, vertices=vertices, faces=faces)


def _guid() -> str:
    return ifcopenshell.guid.new()


def build_model(schema: str = "IFC4") -> ifcopenshell.file:
    import importlib

    model = importlib.import_module("ifcopenshell.api.project").create_file(version=schema)
    entity = lambda cls, **kw: _api("root", "create_entity", model, ifc_class=cls, **kw)  # noqa: E731
    if schema == "IFC2X3":  # IFC2X3 requires an owner history on every rooted entity
        owner_settings = importlib.import_module("ifcopenshell.api.owner.settings")
        person = _api("owner", "add_person", model, identification="tester", family_name="Tester")
        organisation = _api("owner", "add_organisation", model, identification="ifcusd", name="IfcUSD")
        user = _api("owner", "add_person_and_organisation", model, person=person, organisation=organisation)
        application = _api(
            "owner",
            "add_application",
            model,
            application_developer=organisation,
            version="1.0",
            application_full_name="IfcUSD tests",
            application_identifier="ifcusd",
        )
        owner_settings.get_user = lambda _file: user
        owner_settings.get_application = lambda _file: application

    project = entity("IfcProject", name="Test Project")
    length = _api("unit", "add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI")
    area = _api("unit", "add_si_unit", model, unit_type="AREAUNIT")
    volume = _api("unit", "add_si_unit", model, unit_type="VOLUMEUNIT")
    _api("unit", "assign_unit", model, units=[length, area, volume])
    model_context = _api("context", "add_context", model, context_type="Model")
    body = _api(
        "context",
        "add_context",
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_context,
    )

    if schema != "IFC2X3":
        crs = model.create_entity("IfcProjectedCRS", Name="EPSG:7856", GeodeticDatum="GDA2020")
        model.create_entity(
            "IfcMapConversion",
            SourceCRS=model_context,
            TargetCRS=crs,
            Eastings=334000.0,
            Northings=6250000.0,
            OrthogonalHeight=12.0,
            XAxisAbscissa=1.0,
            XAxisOrdinate=0.0,
            Scale=1.0,
        )

    site = entity("IfcSite", name="Site")
    site.RefLatitude = (-33, 52, 0, 0)
    site.RefLongitude = (151, 12, 30, 0)
    building = entity("IfcBuilding", name="Building A")
    ground = entity("IfcBuildingStorey", name="Ground Floor")
    first = entity("IfcBuildingStorey", name="First Floor")
    ground.Elevation = 0.0
    first.Elevation = 3000.0
    _api("aggregate", "assign_object", model, relating_object=project, products=[site])
    _api("aggregate", "assign_object", model, relating_object=site, products=[building])
    _api("aggregate", "assign_object", model, relating_object=building, products=[ground, first])
    for product in (site, building, ground):
        _place(model, product)
    _place(model, first, z=3.0)

    # --- wall type with type-level property set and material layer set
    wall_type = entity("IfcWallType", name="WT-200")
    type_pset = _api("pset", "add_pset", model, product=wall_type, name="Pset_WallCommon")
    _api("pset", "edit_pset", model, pset=type_pset, properties={"FireRating": "REI60", "IsExternal": True})
    concrete = _api(
        "material", "add_material", model, name="Concrete", category=None if schema == "IFC2X3" else "concrete"
    )
    layer_set = _api("material", "add_material_set", model, name="Wall Layers", set_type="IfcMaterialLayerSet")
    layer = _api("material", "add_layer", model, layer_set=layer_set, material=concrete)
    layer.LayerThickness = 200.0
    _api("material", "assign_material", model, products=[wall_type], material=layer_set)

    style = _api("style", "add_style", model, name="Concrete Grey")
    shading = "IfcSurfaceStyleRendering" if schema == "IFC2X3" else "IfcSurfaceStyleShading"
    attributes = {"SurfaceColour": {"Name": None, "Red": 0.5, "Green": 0.55, "Blue": 0.6}, "Transparency": 0.25}
    if schema == "IFC2X3":
        attributes["ReflectanceMethod"] = "NOTDEFINED"
    _api("style", "add_surface_style", model, style=style, ifc_class=shading, attributes=attributes)
    _api("style", "assign_material_style", model, material=concrete, style=style, context=body)

    walls = []
    for index in range(2):
        wall = entity("IfcWall", name=f"Wall {index + 1}")
        _place(model, wall, x=0.0, y=index * 4.0, angle=0.3 * index)
        representation = _api(
            "geometry", "add_wall_representation", model, context=body, length=5.0, height=3.0, thickness=0.2
        )
        _api("geometry", "assign_representation", model, product=wall, representation=representation)
        _api("type", "assign_type", model, related_objects=[wall], relating_type=wall_type)
        _api("spatial", "assign_container", model, products=[wall], relating_structure=ground)
        walls.append(wall)
    wall = walls[0]

    pset = _api("pset", "add_pset", model, product=wall, name="Custom Data (Ext)")
    _api(
        "pset",
        "edit_pset",
        model,
        pset=pset,
        properties={
            "Load bearing": True,
            "Count": 3,
            "Area (m²)": 12.5,
            "Note": UNICODE_TEXT,
        },
    )
    occurrence_common = _api("pset", "add_pset", model, product=wall, name="Pset_WallCommon")
    _api("pset", "edit_pset", model, pset=occurrence_common, properties={"FireRating": "REI90"})

    label = lambda v: model.create_entity("IfcLabel", v)  # noqa: E731
    length_value = lambda v: model.create_entity("IfcLengthMeasure", v)  # noqa: E731
    enumeration = model.create_entity(
        "IfcPropertyEnumeration",
        Name="PEnum_Status",
        EnumerationValues=[label("NEW"), label("EXISTING"), label("DEMOLISH")],
    )
    extra = [
        model.create_entity(
            "IfcPropertyEnumeratedValue",
            Name="Status",
            EnumerationValues=[label("NEW")],
            EnumerationReference=enumeration,
        ),
        model.create_entity(
            "IfcPropertyBoundedValue",
            Name="Tolerance",
            UpperBoundValue=length_value(5.0),
            LowerBoundValue=length_value(-5.0),
        ),
        model.create_entity("IfcPropertyListValue", Name="Offsets", ListValues=[length_value(1.0), length_value(2.5)]),
        model.create_entity(
            "IfcPropertyTableValue",
            Name="Curve",
            DefiningValues=[length_value(0.0), length_value(1.0)],
            DefinedValues=[length_value(10.0), length_value(20.0)],
        ),
        model.create_entity("IfcPropertyReferenceValue", Name="Made Of", PropertyReference=concrete),
        model.create_entity(
            "IfcComplexProperty",
            Name="Assembly",
            UsageName="Test",
            HasProperties=[model.create_entity("IfcPropertySingleValue", Name="Inner", NominalValue=label("x"))],
        ),
    ]
    pset.HasProperties = list(pset.HasProperties) + extra

    qto = _api("pset", "add_qto", model, product=wall, name="Qto_WallBaseQuantities")
    _api("pset", "edit_qto", model, qto=qto, properties={"Length": 5000.0, "NetSideArea": 15.0})

    # --- opening and door
    opening = entity("IfcOpeningElement", name="Door Opening")
    _place(model, opening, x=1.0, y=-0.1)
    _api("geometry", "assign_representation", model, product=opening, representation=_box(model, body, (1.0, 0.4, 2.1)))
    _api("feature", "add_feature", model, feature=opening, element=wall)
    door = entity("IfcDoor", name="Door")
    _place(model, door, x=1.0, y=0.0)
    _api("geometry", "assign_representation", model, product=door, representation=_box(model, body, (1.0, 0.05, 2.1)))
    _api("feature", "add_filling", model, opening=opening, element=door)
    _api("spatial", "assign_container", model, products=[door], relating_structure=ground)

    # --- furniture sharing type geometry (mapped representation -> USD instancing)
    furniture_type = entity("IfcFurnitureType" if schema != "IFC2X3" else "IfcFurnitureType", name="Table")
    _api(
        "geometry",
        "assign_representation",
        model,
        product=furniture_type,
        representation=_box(model, body, (1.2, 0.8, 0.75)),
    )
    tables = []
    for index in range(2):
        table = entity("IfcFurnishingElement" if schema == "IFC2X3" else "IfcFurniture", name=f"Table {index + 1}")
        _place(model, table, x=2.0 + index * 1.5, y=1.0, z=3.0)
        _api("type", "assign_type", model, related_objects=[table], relating_type=furniture_type)
        tables.append(table)

    space = entity("IfcSpace", name="Room 1")
    _place(model, space, z=3.0)
    _api("geometry", "assign_representation", model, product=space, representation=_box(model, body, (5.0, 4.0, 2.8)))
    _api("aggregate", "assign_object", model, relating_object=first, products=[space])
    _api("spatial", "assign_container", model, products=tables, relating_structure=space)

    # --- associations, groups, layers, generic relationships
    classification = _api("classification", "add_classification", model, classification="Uniclass")
    _api(
        "classification",
        "add_reference",
        model,
        products=[wall],
        classification=classification,
        identification="Ss_25_10",
        name="Wall systems",
    )
    group = _api("group", "add_group", model, name="Envelope")
    _api("group", "assign_group", model, products=walls, group=group)
    layer = _api("layer", "add_layer", model, name="A-WALL")
    _api("layer", "assign_layer", model, items=[walls[1].Representation.Representations[0]], layer=layer)

    model.create_entity(
        "IfcRelConnectsElements", GlobalId=_guid(), Name="Corner", RelatingElement=walls[0], RelatedElement=walls[1]
    )
    task = entity("IfcTask", name="Build walls")
    model.create_entity("IfcRelAssignsToProcess", GlobalId=_guid(), RelatingProcess=task, RelatedObjects=walls)
    model.create_entity(
        "IfcPropertySet",
        GlobalId=_guid(),
        Name="Orphan Set",
        HasProperties=[model.create_entity("IfcPropertySingleValue", Name="Lonely", NominalValue=label("yes"))],
    )
    if schema == "IFC4X3_ADD2":
        road = entity("IfcRoad", name="Access Road")
        _api("aggregate", "assign_object", model, relating_object=site, products=[road])
        _place(model, road, x=20.0)
        kerb = entity("IfcKerb", name="Kerb")
        _place(model, kerb, x=20.0)
        _api(
            "geometry", "assign_representation", model, product=kerb, representation=_box(model, body, (10.0, 0.2, 0.3))
        )
        _api("spatial", "assign_container", model, products=[kerb], relating_structure=road)
    return model


def write_model(directory: Path, schema: str = "IFC4") -> Path:
    path = directory / f"test_{schema}.ifc"
    build_model(schema).write(str(path))
    return path
