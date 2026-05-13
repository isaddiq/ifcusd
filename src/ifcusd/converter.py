from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.util.unit

from ifcusd.geometry import iter_mesh_geometries
from ifcusd.hierarchy import EntityPathRegistry, ROOT_PATH, iter_spatial_entities
from ifcusd.metadata import extract_entity_metadata
from ifcusd.options import ConversionOptions
from ifcusd.usd_writer import UsdStageWriter


@dataclass(frozen=True)
class ConversionResult:
    input_path: Path
    output_path: Path
    elements_seen: int = 0
    meshes_written: int = 0
    spatial_prims_written: int = 0
    skipped_geometry: list[str] = field(default_factory=list)


class IfcToUsdConverter:
    def __init__(self, options: ConversionOptions | None = None) -> None:
        self.options = options or ConversionOptions()
        self._model: Any | None = None

    def convert(self, input_path: str | Path, output_path: str | Path) -> ConversionResult:
        source = Path(input_path)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        model = ifcopenshell.open(str(source))
        self._model = model
        meters_per_unit = self.options.meters_per_unit or _calculate_unit_scale(model)
        writer = UsdStageWriter(
            target,
            meters_per_unit=meters_per_unit,
            up_axis=self.options.up_axis,
        )
        writer.set_stage_metadata(_stage_metadata(model, source, meters_per_unit))

        registry = EntityPathRegistry(ROOT_PATH)
        spatial_count = self._write_spatial_hierarchy(model, registry, writer)

        elements_seen = 0
        meshes_written = 0
        skipped_geometry: list[str] = []

        for entity, mesh in iter_mesh_geometries(model, self.options):
            elements_seen += 1
            try:
                for ancestor in registry.ancestor_chain(entity):
                    writer.ensure_entity_xform(
                        registry.path_for(ancestor),
                        self._metadata_for(ancestor),
                    )
                entity_path = registry.path_for(entity)
                writer.add_mesh(entity_path, mesh, self._metadata_for(entity))
                meshes_written += 1
            except Exception as exc:
                label = _entity_label(entity)
                skipped_geometry.append(f"{label}: {exc}")
                if not self.options.ignore_geometry_errors:
                    raise

        writer.save()
        return ConversionResult(
            input_path=source,
            output_path=target,
            elements_seen=elements_seen,
            meshes_written=meshes_written,
            spatial_prims_written=spatial_count,
            skipped_geometry=skipped_geometry,
        )

    def _write_spatial_hierarchy(
        self,
        model: Any,
        registry: EntityPathRegistry,
        writer: UsdStageWriter,
    ) -> int:
        count = 0
        for entity in iter_spatial_entities(model):
            for ancestor in registry.ancestor_chain(entity):
                writer.ensure_entity_xform(registry.path_for(ancestor), self._metadata_for(ancestor))
            writer.ensure_entity_xform(registry.path_for(entity), self._metadata_for(entity))
            count += 1
        return count

    def _metadata_for(self, entity: Any) -> dict[str, Any]:
        if not self.options.include_metadata:
            return {}
        return extract_entity_metadata(
            entity,
            self._model,
            include_properties=self.options.include_properties,
            include_quantities=self.options.include_quantities,
            include_materials=self.options.include_materials,
        )


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    options: ConversionOptions | None = None,
) -> ConversionResult:
    return IfcToUsdConverter(options).convert(input_path, output_path)


def _calculate_unit_scale(model: Any) -> float:
    try:
        return float(ifcopenshell.util.unit.calculate_unit_scale(model))
    except Exception:
        return 1.0


def _stage_metadata(model: Any, source: Path, meters_per_unit: float) -> dict[str, Any]:
    return {
        "source_file": str(source),
        "ifc_schema": str(getattr(model, "schema", "")),
        "meters_per_unit": meters_per_unit,
    }


def _entity_label(entity: Any) -> str:
    try:
        return f"{entity.is_a()} #{entity.id()}"
    except Exception:
        return str(entity)
