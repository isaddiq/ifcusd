"""Conversion reports.

The report answers "was anything lost?" explicitly. Every ``IfcRoot`` instance
in the source file is accounted for in :attr:`ConversionReport.coverage` as
one of:

``prim``        written as its own USD prim
``native``      a relationship expressed natively (namespace parenting, a USD
                relationship property or a collection)
``attributes``  a property/quantity set written as attributes on the prims it
                is attached to
``filtered``    excluded by an explicit element filter
``skipped``     excluded because a data group was switched off in the options
``missing``     not represented (always reported as a warning; expected to be 0)
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

COVERAGE_BUCKETS = ("prim", "native", "attributes", "filtered", "skipped", "missing")


@dataclass
class ConversionReport:
    input_path: str
    output_path: str
    input_format: str
    ifc_schema: str
    declared_schema: str
    source_sha256: str
    converter_version: str
    schema_version: str
    ifcopenshell_version: str
    usd_version: str
    profile: str
    options: dict[str, Any]
    compatibility_schema: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)
    products_without_geometry: list[dict[str, Any]] = field(default_factory=list)
    geometry_errors: list[dict[str, Any]] = field(default_factory=list)
    dropped_entities: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def missing_count(self) -> int:
        return sum(entry.get("missing", 0) for entry in self.coverage.values())

    @property
    def complete(self) -> bool:
        """True when nothing from the source was dropped or failed."""
        return not self.geometry_errors and not self.dropped_entities and self.missing_count == 0

    def coverage_totals(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for entry in self.coverage.values():
            for bucket, value in entry.items():
                totals[bucket] += value
        return {bucket: totals.get(bucket, 0) for bucket in ("inFile", *COVERAGE_BUCKETS)}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["complete"] = self.complete
        data["coverageTotals"] = self.coverage_totals()
        return data

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    def summary_lines(self) -> list[str]:
        counts = self.counts
        totals = self.coverage_totals()
        schema = self.ifc_schema
        if self.compatibility_schema and self.declared_schema != self.ifc_schema:
            schema = f"{self.declared_schema} (read as {self.ifc_schema})"
        lines = [
            f"input   {self.input_path}  [{schema}]",
            f"output  {self.output_path}  [profile: {self.profile}]",
            (
                f"prims {counts.get('prims', 0)} | elements {counts.get('elements', 0)} | "
                f"spatial {counts.get('spatial', 0)} | types {counts.get('types', 0)} | "
                f"relationships {counts.get('relationships', 0)}"
            ),
            (
                f"geometry: meshes {counts.get('meshes', 0)} | instances {counts.get('instances', 0)} "
                f"(prototypes {counts.get('prototypes', 0)}) | curves {counts.get('curves', 0)} | "
                f"triangles {counts.get('triangles', 0)} | materials {counts.get('materials', 0)}"
            ),
            (
                f"data: property sets {counts.get('propertySets', 0)} | properties {counts.get('properties', 0)} | "
                f"quantities {counts.get('quantities', 0)}"
            ),
            (
                f"coverage of {totals['inFile']} IfcRoot instances: prim {totals['prim']}, "
                f"native {totals['native']}, attributes {totals['attributes']}, filtered {totals['filtered']}, "
                f"skipped {totals['skipped']}, missing {totals['missing']}"
            ),
        ]
        if self.products_without_geometry:
            lines.append(f"{len(self.products_without_geometry)} product(s) have a representation but no tessellation")
        if self.geometry_errors:
            lines.append(f"{len(self.geometry_errors)} geometry error(s)")
        if self.dropped_entities:
            lines.append(f"{sum(self.dropped_entities.values())} instance(s) not representable in {self.ifc_schema}")
        total_time = self.timings.get("total")
        if total_time is not None:
            lines.append(f"completed in {total_time:.2f}s ({'complete' if self.complete else 'see warnings'})")
        return lines
