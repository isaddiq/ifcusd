from __future__ import annotations

import pytest

from ifcusd.ifc.reader import _dms_to_degrees, compatible_schema
from ifcusd.ifc.values import to_usd_value
from ifcusd.naming import UniqueNames, lower_first, prim_base_name, safe_identifier
from ifcusd.options import ConversionOptions


def test_safe_identifier_returns_usd_compatible_identifier() -> None:
    assert safe_identifier("01 Level / East") == "_01_Level_East"
    assert safe_identifier("###", fallback="Thing") == "Thing"
    assert safe_identifier("Tür Größe") == "Tur_Groe"
    assert safe_identifier("数据", fallback="Item") == "Item"


def test_prim_base_name_modes() -> None:
    kwargs = {"ifc_class": "IfcWall", "name": "Basic Wall", "global_id": "0u91K9$k96zh", "step_id": 7}
    assert prim_base_name(**kwargs, mode="name_guid") == "Basic_Wall_0u91K9_k96zh"
    assert prim_base_name(**kwargs, mode="name") == "Basic_Wall"
    assert prim_base_name(**kwargs, mode="guid") == "_0u91K9_k96zh"
    assert prim_base_name(ifc_class="IfcWall", name=None, global_id=None, step_id=7) == "IfcWall_7"


def test_unique_names_are_deterministic() -> None:
    names = UniqueNames()
    assert [names.claim("Body"), names.claim("Body"), names.claim("Body")] == ["Body", "Body_2", "Body_3"]


def test_lower_first_is_reversible_for_express_names() -> None:
    for name in ("GlobalId", "PredefinedType", "RefLatitude"):
        converted = lower_first(name)
        assert converted[0].islower()
        assert converted[0].upper() + converted[1:] == name


@pytest.mark.parametrize(
    ("value", "kwargs", "expected_type", "expected"),
    [
        (True, {}, "bool", True),
        (3, {}, "int", 3),
        (2**40, {}, "int64", 2**40),
        (1.5, {}, "double", 1.5),
        ("text", {}, "string", "text"),
        ("NOTDEFINED", {"enum": True}, "token", "NOTDEFINED"),
        ((1, 2, 3), {}, "int[]", [1, 2, 3]),
        ((1, 2.5), {}, "double[]", [1.0, 2.5]),
        (("a", "b"), {}, "string[]", ["a", "b"]),
    ],
)
def test_to_usd_value_maps_express_types(value, kwargs, expected_type, expected) -> None:
    result = to_usd_value(value, **kwargs)
    assert result is not None
    assert result.type_name == expected_type
    assert result.value == expected


def test_nested_aggregates_become_json() -> None:
    result = to_usd_value(((1, 2), (3, 4)))
    assert result is not None and result.type_name == "string" and result.encoding == "json"
    assert result.value == "[[1,2],[3,4]]"


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("IFC4", "IFC4"),
        ("IFC4_ADD2_TC1", "IFC4"),
        ("IFC4X1", "IFC4X3_ADD2"),
        ("IFC4X3_TC1", "IFC4X3_ADD2"),
        ("IFC2X3_TC1", "IFC2X3"),
        ("IFC2X2_FINAL", "IFC2X3"),
        ("STEP_AP214", None),
    ],
)
def test_compatible_schema_selection(declared: str, expected: str | None) -> None:
    assert compatible_schema(declared, ("IFC2X3", "IFC4", "IFC4X3_ADD2")) == expected


def test_dms_conversion_handles_signs() -> None:
    assert _dms_to_degrees((51, 30, 0, 0)) == pytest.approx(51.5)
    assert _dms_to_degrees((-33, -52, 0, 0)) == pytest.approx(-33.8666667)
    assert _dms_to_degrees((-33, 52, 0, 0)) == pytest.approx(-33.8666667)  # inconsistently signed input


def test_profiles_set_target_defaults() -> None:
    unity = ConversionOptions.for_profile("unity")
    assert (unity.up_axis, unity.instancing, unity.include_curves, unity.spaces) == ("Y", False, False, "skip")
    omniverse = ConversionOptions.for_profile("omniverse", up_axis="Y")
    assert omniverse.write_mdl and omniverse.legacy_semantics and omniverse.up_axis == "Y"
    assert ConversionOptions(meters_per_unit=0.01).output_scale == pytest.approx(100.0)


@pytest.mark.parametrize(
    "bad", [{"profile": "maya"}, {"naming": "x"}, {"meters_per_unit": 0}, {"threads": 0}, {"root_prim_name": "1x"}]
)
def test_invalid_options_are_rejected(bad) -> None:
    with pytest.raises(ValueError):
        ConversionOptions(**bad)
