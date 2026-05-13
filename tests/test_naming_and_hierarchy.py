from __future__ import annotations

from ifcusd.hierarchy import EntityPathRegistry
from ifcusd.naming import safe_identifier


class FakeEntity:
    def __init__(self, step_id: int, ifc_class: str, name: str, parent=None) -> None:
        self._id = step_id
        self._ifc_class = ifc_class
        self.Name = name
        self.GlobalId = f"GID{step_id}"
        self.parent = parent

    def id(self) -> int:
        return self._id

    def is_a(self, value: str | None = None):
        if value is None:
            return self._ifc_class
        return value == self._ifc_class


def test_safe_identifier_returns_usd_compatible_identifier() -> None:
    assert safe_identifier("01 Level / East") == "_01_Level_East"
    assert safe_identifier("###", fallback="Thing") == "Thing"


def test_registry_returns_stable_unique_paths_without_parent() -> None:
    registry = EntityPathRegistry()
    first = FakeEntity(1, "IfcWall", "Wall")
    second = FakeEntity(2, "IfcWall", "Wall")

    assert registry.path_for(first) == "/IFC/Wall_GID1"
    assert registry.path_for(first) == "/IFC/Wall_GID1"
    assert registry.path_for(second) == "/IFC/Wall_GID2"
