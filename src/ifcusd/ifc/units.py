"""Project unit resolution.

IfcOpenShell tessellates geometry in metres, but property and quantity values
stay in the project's units. The converter writes those values unchanged and
records the unit next to each one, so nothing is lost to rounding and a
consumer can always convert them.
"""

from __future__ import annotations

from typing import Any

import ifcopenshell.util.unit

from ifcusd.ifc import util

# Measure types that carry their unit in the type itself rather than via the
# project's unit assignment.
_QUANTITY_MEASURES = {
    "IfcQuantityLength": "IfcLengthMeasure",
    "IfcQuantityArea": "IfcAreaMeasure",
    "IfcQuantityVolume": "IfcVolumeMeasure",
    "IfcQuantityCount": "IfcCountMeasure",
    "IfcQuantityWeight": "IfcMassMeasure",
    "IfcQuantityTime": "IfcTimeMeasure",
    "IfcQuantityNumber": "IfcNumericMeasure",
}


class UnitResolver:
    """Looks up unit labels for measures in one IFC model."""

    def __init__(self, model: Any) -> None:
        self.model = model
        try:
            self.length_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
        except Exception:
            self.length_scale = 1.0
        self._project_units: dict[str, Any] = {}
        for assignment in util.by_type(model, "IfcUnitAssignment"):
            for unit in util.attr(assignment, "Units", ()) or ():
                unit_type = util.attr(unit, "UnitType")
                if unit_type is None and unit.is_a("IfcMonetaryUnit"):
                    unit_type = "CURRENCYUNIT"
                if unit_type and str(unit_type) not in self._project_units:
                    self._project_units[str(unit_type)] = unit
        self._measure_cache: dict[str, str | None] = {}
        self._si_cache: dict[str, float] = {}

    def unit_label(self, unit: Any) -> str | None:
        if unit is None:
            return None
        if util.is_a(unit, "IfcMonetaryUnit"):
            currency = util.attr(unit, "Currency")
            return str(currency) if currency is not None else "CURRENCY"
        for getter in (
            ifcopenshell.util.unit.get_full_unit_name,
            ifcopenshell.util.unit.get_unit_symbol,
        ):
            try:
                label = getter(unit)
            except Exception:
                continue
            if label:
                return str(label)
        name = util.attr(unit, "Name") or util.attr(unit, "UserDefinedType")
        return str(name) if name else unit.is_a()

    def label_for_measure(self, measure_type: str | None) -> str | None:
        """Project unit label for an IFC measure type such as ``IfcLengthMeasure``."""
        if not measure_type:
            return None
        if measure_type in self._measure_cache:
            return self._measure_cache[measure_type]
        label = None
        try:
            unit_type = ifcopenshell.util.unit.get_measure_unit_type(measure_type)
        except Exception:
            unit_type = None
        if unit_type and unit_type in self._project_units:
            label = self.unit_label(self._project_units[unit_type])
        elif measure_type in ("IfcPlaneAngleMeasure", "IfcPositivePlaneAngleMeasure"):
            label = "RADIAN"
        self._measure_cache[measure_type] = label
        return label

    def si_scale(self, measure_type: str | None) -> float:
        """SI value of one project unit of ``measure_type``.

        ``1.0`` when the measure has no unit or the project does not assign one
        (IFC then means SI). Mass is relative to the kilogram.
        """
        if not measure_type:
            return 1.0
        if measure_type not in self._si_cache:
            scale = 1.0
            try:
                unit_type = ifcopenshell.util.unit.get_measure_unit_type(measure_type)
            except Exception:
                unit_type = None
            if unit_type in self._project_units and unit_type not in ("USERDEFINED", "REALUNIT"):
                try:
                    scale = float(ifcopenshell.util.unit.calculate_unit_scale(self.model, unit_type))
                except Exception:
                    scale = 1.0
                if unit_type == "MASSUNIT":
                    scale /= 1000.0  # IfcOpenShell scales mass units relative to the gram
            self._si_cache[measure_type] = scale or 1.0
        return self._si_cache[measure_type]

    @staticmethod
    def measure_for_quantity(quantity_class: str) -> str | None:
        return _QUANTITY_MEASURES.get(quantity_class)

    def project_units(self) -> dict[str, str]:
        """``{unit_type: label}`` for every unit in the project unit assignment."""
        result: dict[str, str] = {}
        for unit_type, unit in sorted(self._project_units.items()):
            label = self.unit_label(unit)
            if label:
                result[unit_type] = label
        return result
