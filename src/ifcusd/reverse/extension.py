"""The IfcUSD extension: robot and simulation data in IFC.

IFC cannot gain new EXPRESS entities without a new schema release, but it has
standard mechanisms for additional data, and the export uses exactly those:

* **Property sets** with documented names and typed, unit-aware values:

  ``IfcUSD_Physics``
      ``UsdPhysics`` data: mass, density, centre of mass, rigid body and
      collision settings, friction, restitution, articulation.
  ``IfcUSD_Simulation``
      Motion of animated objects: ``Trajectory`` (a time series of position
      and orientation), ``Duration`` and ``PathLength``.
  ``<Namespace>_<Group>``
      Attributes authored in an extension namespace, for example
      ``robot:navigation:clearance`` -> ``Robot_Navigation.clearance``.

* **Time series** (``IfcRegularTimeSeries``, ``IfcIrregularTimeSeries``) for
  time-sampled USD values, referenced by ``IfcPropertyReferenceValue``.
* **Classifications**: every ``UsdSemantics`` taxonomy becomes an
  ``IfcClassification`` and every label an ``IfcClassificationReference``.
* **Property set templates**: each new set is declared as an
  ``IfcPropertySetTemplate`` (property names, measure types, descriptions,
  applicable classes) in the ``IfcProjectLibrary`` "IfcUSD Extension", so the
  extension is self-describing and can be validated (IFC4 and later).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import ifcopenshell.api.project
import ifcopenshell.guid
import numpy as np

from ifcusd.dependencies import import_usd
from ifcusd.ifc import util
from ifcusd.reverse.attributes import create
from ifcusd.reverse.properties import SetData, SetValue
from ifcusd.reverse.stage import PrimRecord, UsdScene, attribute_value, layer_name, quaternion, rigid
from ifcusd.reverse.values import ValueFactory, flatten

_usd = import_usd()
Usd, UsdGeom, UsdPhysics, UsdShade = _usd.Usd, _usd.UsdGeom, _usd.UsdPhysics, _usd.UsdShade

PHYSICS_SET = "IfcUSD_Physics"
SIMULATION_SET = "IfcUSD_Simulation"
LIBRARY_NAME = "IfcUSD Extension"
SEMANTICS_SOURCE = "OpenUSD UsdSemantics"

SET_DESCRIPTIONS = {
    PHYSICS_SET: "Physical simulation properties from OpenUSD UsdPhysics, written by IfcUSD.",
    SIMULATION_SET: "Motion recorded in an OpenUSD simulation (time-sampled transforms), written by IfcUSD.",
}
PROPERTY_DESCRIPTIONS = {
    PHYSICS_SET: {
        "Mass": "Mass (UsdPhysics.MassAPI); the sum over the object's bodies when not authored on the object.",
        "Density": "Mass density (UsdPhysics.MassAPI, or the bound physics material).",
        "CenterOfMass": "Centre of mass X, Y, Z in the object's placement frame.",
        "RigidBodyEnabled": "The object is a dynamic rigid body (UsdPhysics.RigidBodyAPI).",
        "KinematicEnabled": "The rigid body is moved kinematically, not by the solver.",
        "CollisionEnabled": "The object, or a part of it, collides (UsdPhysics.CollisionAPI).",
        "CollisionApproximation": "Collision shape approximation (UsdPhysics.MeshCollisionAPI), e.g. convexHull.",
        "StaticFriction": "Static friction coefficient of the bound physics material.",
        "DynamicFriction": "Dynamic friction coefficient of the bound physics material.",
        "Restitution": "Coefficient of restitution of the bound physics material.",
        "Articulated": "The object is an articulation (UsdPhysics.ArticulationRootAPI), typically a robot.",
        "JointCount": "Number of physics joints in the object.",
    },
    SIMULATION_SET: {
        "Trajectory": "Position X, Y, Z (project length unit) and orientation quaternion W, X, Y, Z in the project "
        "coordinate system at every USD time sample.",
        "Duration": "Time between the first and the last trajectory sample.",
        "PathLength": "Distance travelled along the trajectory.",
    },
}


# ---------------------------------------------------------------------- harvesting


def physics_set(scene: UsdScene, record: PrimRecord, factory: ValueFactory) -> SetData | None:
    """``IfcUSD_Physics`` values of a record, or ``None`` when it has no physics."""
    root = record.prim
    prims = scene.subtree(root) if record.new_object else [root, *_descendants(record.geometry)]
    values: dict[str, tuple[str, Any, str]] = {}  # name -> (usd type, value, IFC type)
    kg, mpu = scene.kilograms_per_unit, scene.meters_per_unit

    mass = _authored(root, UsdPhysics.MassAPI, "GetMassAttr")
    if not mass:
        masses = [_authored(p, UsdPhysics.MassAPI, "GetMassAttr") for p in prims if p != root]
        mass = sum(m for m in masses if m) or None
    if mass:
        values["Mass"] = ("double", factory.from_si(mass * kg, "IfcMassMeasure"), "IfcMassMeasure")
    density = _authored(root, UsdPhysics.MassAPI, "GetDensityAttr")
    if density:
        values["Density"] = (
            "double",
            factory.from_si(density * kg / mpu**3, "IfcMassDensityMeasure"),
            "IfcMassDensityMeasure",
        )
    if root.HasAPI(UsdPhysics.MassAPI):
        center = UsdPhysics.MassAPI(root).GetCenterOfMassAttr()
        if center.HasAuthoredValue():
            point = [float(c) * mpu for c in attribute_value(center, scene.time)]
            if all(math.isfinite(c) and abs(c) < 1e30 for c in point):
                values["CenterOfMass"] = (
                    "double3",
                    tuple(factory.from_si(point, "IfcLengthMeasure")),
                    "IfcLengthMeasure",
                )
    if root.HasAPI(UsdPhysics.RigidBodyAPI):
        body = UsdPhysics.RigidBodyAPI(root)
        values["RigidBodyEnabled"] = (
            "bool",
            bool(attribute_value(body.GetRigidBodyEnabledAttr(), scene.time)),
            "IfcBoolean",
        )
        values["KinematicEnabled"] = (
            "bool",
            bool(attribute_value(body.GetKinematicEnabledAttr(), scene.time)),
            "IfcBoolean",
        )
    colliders = [p for p in prims if p.HasAPI(UsdPhysics.CollisionAPI)]
    if colliders:
        enabled = any(
            UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get(scene.time) is not False for p in colliders
        )
        values["CollisionEnabled"] = ("bool", enabled, "IfcBoolean")
        approximations = [
            str(UsdPhysics.MeshCollisionAPI(p).GetApproximationAttr().Get(scene.time))
            for p in colliders
            if p.HasAPI(UsdPhysics.MeshCollisionAPI)
        ]
        if approximations:
            values["CollisionApproximation"] = ("token", approximations[0], "IfcLabel")
    for prim in [root, *colliders]:
        material, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial("physics")
        if material and material.GetPrim().HasAPI(UsdPhysics.MaterialAPI):
            api = UsdPhysics.MaterialAPI(material.GetPrim())
            for name, getter in (
                ("StaticFriction", api.GetStaticFrictionAttr),
                ("DynamicFriction", api.GetDynamicFrictionAttr),
                ("Restitution", api.GetRestitutionAttr),
            ):
                attribute = getter()
                if attribute.HasAuthoredValue():
                    values[name] = ("double", float(attribute_value(attribute, scene.time)), "IfcReal")
            material_density = api.GetDensityAttr()
            density_value = attribute_value(material_density, scene.time) if material_density.HasAuthoredValue() else 0
            if "Density" not in values and density_value and density_value > 0:
                si = float(density_value) * kg / mpu**3
                values["Density"] = ("double", factory.from_si(si, "IfcMassDensityMeasure"), "IfcMassDensityMeasure")
            break
    if record.new_object:
        joints = 0
        articulated = False
        for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)):
            articulated = articulated or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
            joints += prim.IsA(UsdPhysics.Joint)
        if articulated:
            values["Articulated"] = ("bool", True, "IfcBoolean")
        if joints:
            values["JointCount"] = ("int", joints, "IfcInteger")
    if not values:
        return None
    data = SetData(name=PHYSICS_SET, origin="physics")
    for name, (usd_type, value, value_type) in values.items():
        data.values.append(
            SetValue(path=(name,), name=name, usd_type=usd_type, value=value, metadata={"ifcValueType": value_type})
        )
    return data


def simulation_set(scene: UsdScene, record: PrimRecord, factory: ValueFactory) -> SetData | None:
    """``IfcUSD_Simulation`` with the trajectory of an object whose transform is animated."""
    if not record.prim.IsA(UsdGeom.Xformable):
        return None
    times = sorted(set(UsdGeom.Xformable(record.prim).GetTimeSamples()))
    if len(times) < 2:
        return None
    samples = []
    positions = []
    for time in times:
        placement, _residual = rigid(scene.ifc_matrix_at(record.prim, time))
        position = [float(c) for c in placement[:3, 3]]
        positions.append(position)
        samples.append((time, [*factory.from_si(position, "IfcLengthMeasure"), *quaternion(placement[:3, :3])]))
    duration = (times[-1] - times[0]) / scene.time_codes_per_second
    path_length = float(np.sum(np.linalg.norm(np.diff(np.array(positions), axis=0), axis=1)))
    order = record.prim.GetAttribute("xformOpOrder")
    trajectory = SetValue(
        path=("Trajectory",),
        name="Trajectory",
        usd_type="double[]",
        metadata={"ifcComponentTypes": ["IfcLengthMeasure"] * 3 + ["IfcReal"] * 4},
        samples=samples,
        source=f"{record.path}.xformOp",
        source_layer=layer_name(order) if order else "",
    )
    data = SetData(name=SIMULATION_SET, origin="simulation", values=[trajectory])
    data.values.append(
        SetValue(
            path=("Duration",),
            name="Duration",
            usd_type="double",
            value=factory.from_si(duration, "IfcTimeMeasure"),
            metadata={"ifcValueType": "IfcTimeMeasure"},
        )
    )
    data.values.append(
        SetValue(
            path=("PathLength",),
            name="PathLength",
            usd_type="double",
            value=factory.from_si(path_length, "IfcLengthMeasure"),
            metadata={"ifcValueType": "IfcLengthMeasure"},
        )
    )
    return data


def semantic_labels(prim: Any, ifc_class: str | None) -> list[tuple[str, str]]:
    """``(taxonomy, label)`` pairs from ``UsdSemantics`` and the legacy Omniverse ``SemanticsAPI``.

    The ``class`` label IfcUSD writes itself (the IFC class) is left out.
    """
    labels: list[tuple[str, str]] = []
    legacy: dict[str, dict[str, str]] = {}
    for attribute in prim.GetAttributes():
        name = attribute.GetName()
        if name.startswith("semantics:labels:"):
            taxonomy = name.split(":", 2)[2]
            labels.extend((taxonomy, str(label)) for label in attribute.Get() or ())
        elif name.startswith("semantic:") and name.endswith(("params:semanticType", "params:semanticData")):
            instance = name.split(":")[1]
            legacy.setdefault(instance, {})[name.rsplit(":", 1)[1]] = str(attribute.Get() or "")
    for entry in legacy.values():
        if entry.get("semanticType") and entry.get("semanticData"):
            labels.append((entry["semanticType"], entry["semanticData"]))
    seen: set[tuple[str, str]] = set()
    result = []
    for taxonomy, label in labels:
        if (taxonomy, label) in seen or (taxonomy == "class" and label == ifc_class) or not label:
            continue
        seen.add((taxonomy, label))
        result.append((taxonomy, label))
    return result


def _authored(prim: Any, api: Any, getter: str) -> float | None:
    if not prim.HasAPI(api):
        return None
    attribute = getattr(api(prim), getter)()
    if not attribute.HasAuthoredValue():
        return None
    value = attribute_value(attribute, Usd.TimeCode.EarliestTime())
    return float(value) if value and value > 0 else None


def _descendants(prims: list[Any]) -> list[Any]:
    found = []
    for prim in prims:
        found.extend(Usd.PrimRange(prim, Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)))
    return found


# ---------------------------------------------------------------------- writing


class TimeSeriesWriter:
    """``IfcRegularTimeSeries`` when samples are evenly spaced, ``IfcIrregularTimeSeries`` otherwise.

    Each series links back to where it came from: an ``IfcDocumentReference``
    to the USD layer file, related through an ``IfcExternalReferenceRelationship``
    named after the prim attribute (IFC4 and later, where every time series
    needs an external reference).
    """

    def __init__(
        self,
        model: Any,
        factory: ValueFactory,
        *,
        origin: str,
        time_codes_per_second: float,
        warn: Callable[[str], None],
    ) -> None:
        self.model = model
        self.factory = factory
        self.origin = datetime.fromisoformat(origin)
        self.time_codes_per_second = time_codes_per_second
        self.warn = warn
        self.count = 0
        self._documents: dict[str, Any] = {}

    def __call__(self, value: SetValue) -> Any | None:
        component_types = value.metadata.get("ifcComponentTypes")
        rows = []
        for time, sample in value.samples:
            if sample is None:
                continue
            scalars = flatten(sample)
            if component_types and len(component_types) == len(scalars):
                items = [
                    self.factory.value(value.usd_type, v, t) for v, t in zip(scalars, component_types, strict=True)
                ]
            else:
                items = self.factory.values(value.usd_type, scalars, value.value_type)
            items = [i for i in items if i is not None]
            if items:
                rows.append((time / self.time_codes_per_second, items))
        if len(rows) < 2:
            return None
        seconds = [row[0] for row in rows]
        steps = np.diff(seconds)
        numeric = all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for _t, s in value.samples for v in flatten(s)
        )
        description = value.metadata.get("ifcDescription")
        common = {
            "Name": value.name,
            "Description": str(description) if description else None,
            "StartTime": self._time(seconds[0]),
            "EndTime": self._time(seconds[-1]),
            "TimeSeriesDataType": "CONTINUOUS" if numeric else "DISCRETE",
            "DataOrigin": str(value.metadata.get("ifcDataOrigin") or "SIMULATED"),
        }
        self.count += 1
        if steps[0] > 0 and np.allclose(steps, steps[0], rtol=1e-9, atol=1e-12):
            series_values = [
                create(self.model, "IfcTimeSeriesValue", self.warn, ListValues=items) for _t, items in rows
            ]
            series = create(
                self.model,
                "IfcRegularTimeSeries",
                self.warn,
                TimeStep=float(self.factory.from_si(float(steps[0]), "IfcTimeMeasure")),
                Values=series_values,
                **common,
            )
        else:
            irregular = [
                create(self.model, "IfcIrregularTimeSeriesValue", self.warn, TimeStamp=self._time(t), ListValues=items)
                for t, items in rows
            ]
            series = create(self.model, "IfcIrregularTimeSeries", self.warn, Values=irregular, **common)
        self._link_source(series, value)
        return series

    def _link_source(self, series: Any, value: SetValue) -> None:
        if not self.factory.is_entity_class("IfcExternalReferenceRelationship"):
            return
        location = value.source_layer or "USD stage"
        document = self._documents.get(location)
        if document is None:
            document = create(
                self.model,
                "IfcDocumentReference",
                self.warn,
                Location=location,
                Name="OpenUSD layer",
                Description="Time-sampled data recorded in OpenUSD",
            )
            self._documents[location] = document
        create(
            self.model,
            "IfcExternalReferenceRelationship",
            self.warn,
            Name=value.source or value.name,
            Description="USD attribute the time series was read from",
            RelatingReference=document,
            RelatedResourceObjects=[series],
        )

    def _time(self, seconds: float) -> Any:
        moment = self.origin + timedelta(seconds=seconds)
        if not self.factory.is_ifc2x3:
            return moment.isoformat()
        date = self.model.create_entity(
            "IfcCalendarDate", DayComponent=moment.day, MonthComponent=moment.month, YearComponent=moment.year
        )
        clock = self.model.create_entity(
            "IfcLocalTime",
            HourComponent=moment.hour,
            MinuteComponent=moment.minute,
            SecondComponent=moment.second + moment.microsecond / 1e6,
        )
        return self.model.create_entity("IfcDateAndTime", DateComponent=date, TimeComponent=clock)


class ClassificationWriter:
    """Semantic labels as classification references associated with the labelled entities."""

    def __init__(self, model: Any, owner_history: Callable[[], Any], warn: Callable[[str], None]) -> None:
        self.model = model
        self.owner_history = owner_history
        self.warn = warn
        self.references = 0
        self._systems: dict[str, Any] = {}
        self._references: dict[tuple[str, str], Any] = {}
        self._pending: dict[int, tuple[Any, list[Any]]] = {}

    def assign(self, entity: Any, labels: list[tuple[str, str]]) -> None:
        for taxonomy, label in labels:
            reference = self._reference(taxonomy, label)
            if any(
                util.is_a(rel, "IfcRelAssociatesClassification") and rel.RelatingClassification == reference
                for rel in util.inverse(entity, "HasAssociations")
            ):
                continue
            self._pending.setdefault(reference.id(), (reference, []))[1].append(entity)

    def finish(self) -> None:
        for reference, entities in self._pending.values():
            create(
                self.model,
                "IfcRelAssociatesClassification",
                self.warn,
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner_history(),
                Name="USD semantic label",
                RelatedObjects=entities,
                RelatingClassification=reference,
            )
        self._pending.clear()

    def _system(self, taxonomy: str) -> Any:
        if taxonomy not in self._systems:
            existing = next(
                (
                    c
                    for c in util.by_type(self.model, "IfcClassification")
                    if c.Name == taxonomy and util.attr(c, "Source") == SEMANTICS_SOURCE
                ),
                None,
            )
            self._systems[taxonomy] = existing or create(
                self.model,
                "IfcClassification",
                self.warn,
                Source=SEMANTICS_SOURCE,
                Edition="1",
                Name=taxonomy,
                Description=f"Semantic labels of the {taxonomy} taxonomy in OpenUSD",
            )
        return self._systems[taxonomy]

    def _reference(self, taxonomy: str, label: str) -> Any:
        key = (taxonomy, label)
        if key not in self._references:
            system = self._system(taxonomy)
            existing = next(
                (
                    r
                    for r in util.by_type(self.model, "IfcClassificationReference")
                    if util.attr(r, "ReferencedSource") == system
                    and (util.attr(r, "Identification") or util.attr(r, "ItemReference")) == label
                ),
                None,
            )
            if existing is None:
                self.references += 1
                existing = create(
                    self.model,
                    "IfcClassificationReference",
                    self.warn,
                    Identification=label,
                    ItemReference=label,
                    Name=label,
                    ReferencedSource=system,
                )
            self._references[key] = existing
        return self._references[key]


class TemplateWriter:
    """Declares the property sets the export added as ``IfcPropertySetTemplate``s (IFC4 and later)."""

    def __init__(
        self, model: Any, factory: ValueFactory, owner_history: Callable[[], Any], warn: Callable[[str], None]
    ) -> None:
        self.model = model
        self.factory = factory
        self.owner_history = owner_history
        self.warn = warn
        self.templates = 0
        self._uses: dict[tuple[str, bool], dict[str, Any]] = {}

    @property
    def supported(self) -> bool:
        return self.factory.is_entity_class("IfcPropertySetTemplate")

    def record(self, definition: Any, owner: Any, *, quantity: bool) -> None:
        name = str(util.attr(definition, "Name") or "")
        if not name or name.startswith(("Pset_", "Qto_")):
            return  # buildingSMART sets have published templates
        use = self._uses.setdefault((name, quantity), {"definitions": [], "classes": set(), "owners": set()})
        if definition not in use["definitions"]:
            use["definitions"].append(definition)
        if owner is not None:
            use["classes"].add(owner.is_a())
            use["owners"].add(
                "material"
                if util.is_a(owner, "IfcMaterialDefinition") or util.is_a(owner, "IfcMaterial")
                else "type"
                if util.is_a(owner, "IfcTypeObject")
                else "occurrence"
            )

    def finish(self, project: Any) -> None:
        if not self._uses or not self.supported or project is None:
            return
        library = self._library(project)
        new_templates = []
        for (name, quantity), use in sorted(self._uses.items()):
            template = self._template(name, quantity, use)
            if template.id() not in {t.id() for t in self._declared(library)}:
                new_templates.append(template)
            known = {t.Name for t in template.HasPropertyTemplates or ()}
            additions = []
            for definition in use["definitions"]:
                for item in _members(definition):
                    if item.Name not in known:
                        known.add(item.Name)
                        additions.append(self._property_template(name, item))
            if additions:
                template.HasPropertyTemplates = [*(template.HasPropertyTemplates or ()), *additions]
            self._link(template, use["definitions"])
        if new_templates:
            _declare(self.model, new_templates, library)

    def _library(self, project: Any) -> Any:
        library = next((lib for lib in util.by_type(self.model, "IfcProjectLibrary") if lib.Name == LIBRARY_NAME), None)
        if library is None:
            library = create(
                self.model,
                "IfcProjectLibrary",
                self.warn,
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner_history(),
                Name=LIBRARY_NAME,
                Description="Property set templates for data added in OpenUSD (robotics, simulation, ...) by IfcUSD.",
            )
            _declare(self.model, [library], project)
        return library

    @staticmethod
    def _declared(library: Any) -> list[Any]:
        return [d for rel in util.inverse(library, "Declares") for d in rel.RelatedDefinitions or ()]

    def _template(self, name: str, quantity: bool, use: dict[str, Any]) -> Any:
        owners = use["owners"]
        if quantity:
            template_type = "QTO_TYPEDRIVENOVERRIDE" if "type" in owners else "QTO_OCCURRENCEDRIVEN"
        elif owners == {"material"}:
            template_type = "PSET_MATERIALDRIVEN"
        elif owners == {"type"}:
            template_type = "PSET_TYPEDRIVENONLY"
        elif "type" in owners:
            template_type = "PSET_TYPEDRIVENOVERRIDE"
        else:
            template_type = "PSET_OCCURRENCEDRIVEN"
        existing = next((t for t in util.by_type(self.model, "IfcPropertySetTemplate") if t.Name == name), None)
        if existing is not None:
            classes = {c.strip() for c in str(existing.ApplicableEntity or "").split(",") if c.strip()}
            existing.ApplicableEntity = ",".join(sorted(classes | use["classes"])) or None
            return existing
        self.templates += 1
        return create(
            self.model,
            "IfcPropertySetTemplate",
            self.warn,
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner_history(),
            Name=name,
            Description=SET_DESCRIPTIONS.get(name, "Property set added from OpenUSD by IfcUSD."),
            TemplateType=template_type,
            ApplicableEntity=",".join(sorted(use["classes"])) or None,
        )

    def _property_template(self, set_name: str, item: Any) -> Any:
        description = PROPERTY_DESCRIPTIONS.get(set_name, {}).get(item.Name) or util.attr(item, "Description")
        common = {
            "GlobalId": ifcopenshell.guid.new(),
            "OwnerHistory": self.owner_history(),
            "Name": item.Name,
            "Description": str(description) if description else None,
        }
        if item.is_a("IfcComplexProperty") or item.is_a("IfcPhysicalComplexQuantity"):
            children = [self._property_template(set_name, child) for child in _members(item)]
            return create(
                self.model,
                "IfcComplexPropertyTemplate",
                self.warn,
                UsageName=util.attr(item, "UsageName"),
                TemplateType="Q_COMPLEX" if item.is_a("IfcPhysicalComplexQuantity") else "P_COMPLEX",
                HasPropertyTemplates=children or None,
                **common,
            )
        template_type, primary, secondary = _template_kind(item)
        allowed = self.factory.enum_items("IfcSimplePropertyTemplate", "TemplateType")
        if allowed and template_type not in allowed:
            template_type = "P_SINGLEVALUE" if template_type.startswith("P_") else "Q_COUNT"
        return create(
            self.model,
            "IfcSimplePropertyTemplate",
            self.warn,
            TemplateType=template_type,
            PrimaryMeasureType=primary,
            SecondaryMeasureType=secondary,
            Enumerators=util.attr(item, "EnumerationReference"),
            AccessState="READWRITE",
            **common,
        )

    def _link(self, template: Any, definitions: list[Any]) -> None:
        definitions = [d for d in definitions if util.is_a(d, "IfcPropertySetDefinition")]
        if not definitions:
            return
        rel = next(iter(util.inverse(template, "Defines")), None)
        if rel is None:
            create(
                self.model,
                "IfcRelDefinesByTemplate",
                self.warn,
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner_history(),
                RelatedPropertySets=definitions,
                RelatingTemplate=template,
            )
            return
        current = list(rel.RelatedPropertySets or ())
        rel.RelatedPropertySets = current + [d for d in definitions if d not in current]


def _declare(model: Any, definitions: list[Any], context: Any) -> None:
    """``IfcRelDeclares`` from ``context``, with definitions in a stable order (the API orders them by hash)."""
    rel = ifcopenshell.api.project.assign_declaration(model, definitions=definitions, relating_context=context)
    if rel is not None:
        rel.RelatedDefinitions = sorted(rel.RelatedDefinitions, key=lambda definition: definition.id())


def _members(definition: Any) -> list[Any]:
    for name in ("HasProperties", "Quantities", "Properties", "ExtendedProperties", "HasQuantities"):
        try:
            value = getattr(definition, name)
        except Exception:
            continue
        return list(value or ())
    return []


_QUANTITY_TEMPLATE = {
    "IfcQuantityLength": "Q_LENGTH",
    "IfcQuantityArea": "Q_AREA",
    "IfcQuantityVolume": "Q_VOLUME",
    "IfcQuantityCount": "Q_COUNT",
    "IfcQuantityWeight": "Q_WEIGHT",
    "IfcQuantityTime": "Q_TIME",
    "IfcQuantityNumber": "Q_NUMBER",
}


def _template_kind(item: Any) -> tuple[str, str | None, str | None]:
    """``(TemplateType, PrimaryMeasureType, SecondaryMeasureType)`` describing a property instance."""
    cls = item.is_a()
    if cls in _QUANTITY_TEMPLATE:
        return _QUANTITY_TEMPLATE[cls], None, None

    def type_of(values: Any) -> str | None:
        values = values if isinstance(values, (list, tuple)) else [values]
        return next((v.is_a() for v in values if v is not None), None)

    if cls == "IfcPropertySingleValue":
        return "P_SINGLEVALUE", type_of(item.NominalValue) or "IfcLabel", None
    if cls == "IfcPropertyEnumeratedValue":
        return "P_ENUMERATEDVALUE", type_of(item.EnumerationValues) or "IfcLabel", None
    if cls == "IfcPropertyListValue":
        return "P_LISTVALUE", type_of(item.ListValues) or "IfcLabel", None
    if cls == "IfcPropertyBoundedValue":
        return "P_BOUNDEDVALUE", type_of([item.UpperBoundValue, item.LowerBoundValue]) or "IfcReal", None
    if cls == "IfcPropertyTableValue":
        return "P_TABLEVALUE", type_of(item.DefiningValues), type_of(item.DefinedValues)
    if cls == "IfcPropertyReferenceValue":
        reference = util.attr(item, "PropertyReference")
        return "P_REFERENCEVALUE", reference.is_a() if reference is not None else None, None
    return "P_SINGLEVALUE", "IfcLabel", None
