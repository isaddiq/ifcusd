from __future__ import annotations

from typing import Any

import ifcopenshell.util.element

from ifcusd.naming import entity_label


ROOT_PATH = "/IFC"


class EntityPathRegistry:
    def __init__(self, root_path: str = ROOT_PATH) -> None:
        self.root_path = root_path
        self._paths_by_id: dict[int, str] = {}
        self._used_paths: set[str] = {root_path}

    def path_for(self, entity: Any) -> str:
        entity_id = _entity_id(entity)
        if entity_id in self._paths_by_id:
            return self._paths_by_id[entity_id]

        parent = parent_entity(entity)
        parent_path = self.path_for(parent) if parent is not None else self.root_path
        path = self._unique_child_path(parent_path, entity_label(entity))
        self._paths_by_id[entity_id] = path
        return path

    def ancestor_chain(self, entity: Any) -> list[Any]:
        chain: list[Any] = []
        seen: set[int] = set()
        current = parent_entity(entity)
        while current is not None:
            current_id = _entity_id(current)
            if current_id in seen:
                break
            seen.add(current_id)
            chain.append(current)
            current = parent_entity(current)
        chain.reverse()
        return chain

    def _unique_child_path(self, parent_path: str, name: str) -> str:
        base_path = f"{parent_path}/{name}"
        path = base_path
        index = 2
        while path in self._used_paths:
            path = f"{base_path}_{index}"
            index += 1
        self._used_paths.add(path)
        return path


def parent_entity(entity: Any) -> Any | None:
    if _is_ifc_class(entity, "IfcProject"):
        return None

    aggregate = _safe_util_call(ifcopenshell.util.element.get_aggregate, entity)
    if aggregate is not None:
        return aggregate

    container = _safe_util_call(
        ifcopenshell.util.element.get_container,
        entity,
        should_get_direct=True,
    )
    if container is not None:
        return container

    return None


def iter_spatial_entities(model: Any) -> list[Any]:
    entities: list[Any] = []
    for ifc_class in (
        "IfcProject",
        "IfcSite",
        "IfcBuilding",
        "IfcBuildingStorey",
        "IfcSpace",
        "IfcFacility",
        "IfcFacilityPart",
    ):
        try:
            entities.extend(model.by_type(ifc_class))
        except Exception:
            continue
    return entities


def _safe_util_call(func: Any, entity: Any, **kwargs: Any) -> Any:
    try:
        return func(entity, **kwargs)
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


def _entity_id(entity: Any) -> int:
    try:
        return int(entity.id())
    except Exception:
        return id(entity)
