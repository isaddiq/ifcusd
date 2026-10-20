"""Metadata sidecar JSON for applications that ignore custom USD attributes.

Unity's USD importer brings in geometry, transforms and materials but drops
custom attributes. The sidecar carries the IFC identity and properties of
every object, keyed by USD prim path, in a flat layout that Unity's built-in
``JsonUtility`` can parse (see ``integrations/unity``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ifcusd._version import SCHEMA_VERSION, __version__

SIDECAR_FORMAT = "ifcusd-metadata/1"


def write_sidecar(path: str | Path, objects: list[dict[str, Any]], report: Any | None = None) -> Path:
    target = Path(path)
    document = {
        "format": SIDECAR_FORMAT,
        "converterVersion": __version__,
        "schemaVersion": SCHEMA_VERSION,
        "source": Path(report.input_path).name if report is not None else "",
        "sourceSha256": report.source_sha256 if report is not None else "",
        "ifcSchema": report.ifc_schema if report is not None else "",
        "usdFile": Path(report.output_path).name if report is not None else "",
        "objects": sorted(objects, key=lambda item: item["path"]),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return target
