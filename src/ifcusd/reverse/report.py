"""USD to IFC export reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IfcExportReport:
    """Everything known about one USD to IFC export; serialize it with ``to_json``.

    Attributes:
        input_path: The USD file.
        output_path: The written IFC file.
        mode: ``merge`` (a source IFC was updated) or ``standalone`` (rebuilt from USD).
        ifc_schema: Schema of the written file.
        source_path: The source IFC file (merge mode).
        layers: Additional USD layers composed on top of the input.
        source_sha256: SHA-256 of the source IFC file.
        source_matches_usd: Whether the USD file was converted from exactly this source
            (``None`` when the USD file carries no IfcUSD provenance).
        converter_version: IfcUSD version.
        ifcopenshell_version: IfcOpenShell version used.
        usd_version: OpenUSD version used.
        options: All export options.
        counts: ``matched``, ``created``, ``newObjects``, ``duplicates``,
            ``attributesUpdated``, ``reclassified``, ``placementsUpdated``,
            ``propertySetsCreated``, ``propertySetsUpdated``, ``propertyValues``,
            ``timeSeries``, ``classificationReferences``, ``templates``,
            ``representations``, ``triangles``, ``relationships``, ``notInUsd``.
        created_objects: New IFC elements made from prims without IFC identity.
        not_in_usd: Source products with no prim in the stage (deleted,
            deactivated or filtered out at conversion); they are kept.
        validation: Result of IfcOpenShell schema validation, when requested.
        warnings: Human-readable warnings.
        timings: Seconds per phase (``read``, ``write``, ``save``, ``total``).
    """

    input_path: str
    output_path: str
    mode: str
    ifc_schema: str
    source_path: str | None = None
    layers: list[str] = field(default_factory=list)
    source_sha256: str | None = None
    source_matches_usd: bool | None = None
    converter_version: str = ""
    ifcopenshell_version: str = ""
    usd_version: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    created_objects: list[dict[str, Any]] = field(default_factory=list)
    not_in_usd: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def valid(self) -> bool | None:
        """Schema validation outcome (``None`` when validation was not run)."""
        return None if self.validation is None else bool(self.validation.get("ok"))

    @property
    def introduced_errors(self) -> int | None:
        """Validation errors the export introduced (in merge mode, errors the source has are excluded)."""
        return (
            None if self.validation is None else int(self.validation.get("newErrors", self.validation.get("errors", 0)))
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    def summary_lines(self) -> list[str]:
        c = self.counts
        lines = [
            f"input   {Path(self.input_path).name}" + (f"  (+{len(self.layers)} layer(s))" if self.layers else ""),
            f"output  {Path(self.output_path).name}  [{self.ifc_schema}, {self.mode}]",
        ]
        if self.mode == "merge":
            lines.append(
                f"source  {Path(self.source_path or '').name}: matched {c.get('matched', 0)} | "
                f"attributes {c.get('attributesUpdated', 0)} | placements {c.get('placementsUpdated', 0)} | "
                f"not in USD {c.get('notInUsd', 0)}"
            )
        lines.append(
            f"entities: created {c.get('created', 0)} | new objects {c.get('newObjects', 0)} | "
            f"duplicates {c.get('duplicates', 0)} | relationships {c.get('relationships', 0)}"
        )
        lines.append(
            f"data: property sets +{c.get('propertySetsCreated', 0)} ~{c.get('propertySetsUpdated', 0)} | "
            f"values {c.get('propertyValues', 0)} | time series {c.get('timeSeries', 0)} | "
            f"classifications {c.get('classificationReferences', 0)} | templates {c.get('templates', 0)}"
        )
        lines.append(f"geometry: representations {c.get('representations', 0)} | triangles {c.get('triangles', 0)}")
        if self.validation is not None:
            errors = self.validation.get("errors", 0)
            inherited = errors - self.validation.get("newErrors", errors)
            state = "OK" if self.validation.get("ok") else f"{errors} error(s)"
            if inherited:
                state += f", {inherited} already in the source"
            lines.append(f"validation: {state}")
        lines.append(f"completed in {self.timings.get('total', 0.0):.2f}s ({len(self.warnings)} warning(s))")
        return lines
