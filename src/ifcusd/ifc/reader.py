"""Opening IFC files of any released schema version.

IfcOpenShell only ships a handful of schemas (for example 0.9 has IFC2X3, IFC4
and IFC4X3_ADD2). Files that declare an intermediate or alias identifier such
as ``IFC4X1``, ``IFC4X3_TC1`` or ``IFC4_ADD2`` are opened with the closest
compatible schema instead of failing. Any instances that schema cannot
represent are counted from the raw file and reported, so the fallback never
loses data silently.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ifcopenshell

from ifcusd.errors import InputError, UnsupportedSchemaError
from ifcusd.ifc import util

log = logging.getLogger(__name__)

_FILE_SCHEMA_RE = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", re.IGNORECASE)
_INSTANCE_RE = re.compile(rb"#\d+\s*=\s*([A-Za-z0-9_]+)\s*\(")
_HEADER_SCAN_BYTES = 1 << 20


@dataclass
class OpenedIfc:
    """An opened IFC model plus everything known about how it was read."""

    model: Any
    path: Path
    sha256: str
    declared_schema: str
    schema: str
    compatibility_schema: str | None = None
    dropped_entities: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def available_schemas() -> tuple[str, ...]:
    names = ifcopenshell.ifcopenshell_wrapper.schema_names()
    return tuple(name for name in names if name.upper().startswith("IFC"))


def read_declared_schema(path: Path) -> str | None:
    with open(path, "rb") as handle:
        head = handle.read(_HEADER_SCAN_BYTES)
    match = _FILE_SCHEMA_RE.search(head)
    return match.group(1).decode("ascii", errors="replace").strip() if match else None


def compatible_schema(declared: str, available: tuple[str, ...]) -> str | None:
    """Pick the installed schema that best matches a declared identifier."""
    upper = declared.upper().replace(" ", "")
    by_upper = {name.upper(): name for name in available}
    if upper in by_upper:
        return by_upper[upper]

    def latest(prefix: str) -> str | None:
        matches = sorted(name for name in available if name.upper().startswith(prefix))
        return matches[-1] if matches else None

    if upper.startswith("IFC4X"):
        return latest("IFC4X3") or latest("IFC4X")
    if upper.startswith("IFC4"):
        return by_upper.get("IFC4") or latest("IFC4")
    if upper.startswith("IFC2X"):
        return by_upper.get("IFC2X3") or latest("IFC2X")
    return None


def open_ifc(path: str | os.PathLike[str], *, allow_schema_fallback: bool = True) -> OpenedIfc:
    """Open an ``.ifc`` or ``.ifczip`` file."""
    source = Path(path)
    if not source.exists():
        raise InputError(f"Input file does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix == ".ifcxml":
        raise InputError(
            "ifcXML input is not supported by the installed IfcOpenShell. Convert it to an .ifc (STEP) file first."
        )

    digest = sha256_of(source)
    with tempfile.TemporaryDirectory(prefix="ifcusd_") as scratch:
        spf_path = _extract_ifczip(source, Path(scratch)) if suffix == ".ifczip" else source
        declared = read_declared_schema(spf_path) or ""
        available = available_schemas()
        try:
            model = ifcopenshell.open(str(spf_path))
            opened = OpenedIfc(
                model=model,
                path=source,
                sha256=digest,
                declared_schema=declared or util.schema_identifier(model),
                schema=util.schema_identifier(model),
            )
            return opened
        except Exception as exc:
            if not _is_schema_error(exc):
                raise InputError(f"Could not read IFC file {source}: {exc}") from exc
            target = compatible_schema(declared, available) if declared else None
            if not allow_schema_fallback or target is None:
                raise UnsupportedSchemaError(
                    f"IFC schema {declared or '<unknown>'!r} is not supported by IfcOpenShell "
                    f"{ifcopenshell.version} (available: {', '.join(available)})."
                ) from exc
            rewritten = Path(scratch) / f"compat_{spf_path.name}"
            _rewrite_schema(spf_path, rewritten, target)
            try:
                model = ifcopenshell.open(str(rewritten))
            except Exception as retry_exc:
                raise UnsupportedSchemaError(
                    f"IFC schema {declared!r} could not be read with compatibility schema {target}: {retry_exc}"
                ) from retry_exc
            dropped = _dropped_instances(rewritten, model)
            warnings = [f"File declares schema {declared}; it was read with the closest supported schema {target}."]
            if dropped:
                total = sum(dropped.values())
                warnings.append(
                    f"{total} instance(s) of {len(dropped)} class(es) do not exist in {target} "
                    "and could not be converted (see dropped_entities in the report)."
                )
            for message in warnings:
                log.warning(message)
            return OpenedIfc(
                model=model,
                path=source,
                sha256=digest,
                declared_schema=declared,
                schema=util.schema_identifier(model),
                compatibility_schema=target,
                dropped_entities=dropped,
                warnings=warnings,
            )


def _is_schema_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "schema" in text


def _extract_ifczip(source: Path, scratch: Path) -> Path:
    try:
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if Path(name).suffix.lower() == ".ifc":
                    target = scratch / Path(name).name
                    with archive.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    return target
    except zipfile.BadZipFile as exc:
        raise InputError(f"{source} is not a valid ifcZIP archive") from exc
    raise InputError(f"No .ifc file found inside {source}")


def _rewrite_schema(source: Path, target: Path, schema: str) -> None:
    with open(source, "rb") as src, open(target, "wb") as dst:
        head = src.read(_HEADER_SCAN_BYTES)
        head = _FILE_SCHEMA_RE.sub(
            lambda match: match.group(0).replace(match.group(1), schema.encode("ascii")),
            head,
            count=1,
        )
        dst.write(head)
        shutil.copyfileobj(src, dst, 1 << 20)


def _dropped_instances(path: Path, model: Any) -> dict[str, int]:
    """Count instances present in the file text but missing from the parsed model."""
    file_counts: Counter[str] = Counter()
    with open(path, "rb") as handle:
        data_started = False
        for line in handle:
            if not data_started:
                data_started = line.strip().upper().startswith(b"DATA;")
                continue
            for match in _INSTANCE_RE.finditer(line):
                file_counts[match.group(1).decode("ascii", errors="replace").upper()] += 1
    parsed: Counter[str] = Counter(entity.is_a().upper() for entity in model)
    return {
        name: count - parsed.get(name, 0) for name, count in sorted(file_counts.items()) if count > parsed.get(name, 0)
    }


# --------------------------------------------------------------------------- header


def header_info(model: Any) -> dict[str, Any]:
    """STEP header data (FILE_DESCRIPTION, FILE_NAME, FILE_SCHEMA)."""
    info: dict[str, Any] = {}
    header = getattr(model, "header", None)
    if header is None:
        return info

    def get(section: str, name: str) -> Any:
        try:
            return getattr(getattr(header, section), name)
        except Exception:
            return None

    def strings(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item not in (None, "")]
        return [str(value)] if value != "" else []

    info["description"] = strings(get("file_description", "description"))
    info["implementationLevel"] = get("file_description", "implementation_level") or ""
    info["name"] = get("file_name", "name") or ""
    info["timeStamp"] = get("file_name", "time_stamp") or ""
    info["author"] = strings(get("file_name", "author"))
    info["organization"] = strings(get("file_name", "organization"))
    info["preprocessorVersion"] = get("file_name", "preprocessor_version") or ""
    info["originatingSystem"] = get("file_name", "originating_system") or ""
    info["authorization"] = get("file_name", "authorization") or ""
    info["schemaIdentifiers"] = strings(get("file_schema", "schema_identifiers"))
    return info


# --------------------------------------------------------------------------- georeferencing


def georeference_info(model: Any, units: Any) -> dict[str, Any]:
    """Map conversion, projected CRS, site reference location and true north."""
    data: dict[str, Any] = {}
    operations = util.by_type(model, "IfcCoordinateOperation")
    if operations:
        operation = sorted(operations, key=lambda item: item.id())[0]
        data["conversionClass"] = operation.is_a()
        for name, _kind, value in util.explicit_attributes(operation):
            if name in ("SourceCRS", "TargetCRS") or value is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                data[_lower_first(name)] = float(value)
        crs = util.attr(operation, "TargetCRS")
        if crs is not None:
            for name, _kind, value in util.explicit_attributes(crs):
                if value is None:
                    continue
                key = f"crs:{_lower_first(name)}"
                if util.is_entity(value):
                    label = units.unit_label(value) if name == "MapUnit" else None
                    data[key] = label or value.is_a()
                else:
                    data[key] = value if isinstance(value, (int, float)) else str(value)
    sites = sorted(util.by_type(model, "IfcSite"), key=lambda item: item.id())
    for site in sites:
        latitude = _dms_to_degrees(util.attr(site, "RefLatitude"))
        longitude = _dms_to_degrees(util.attr(site, "RefLongitude"))
        if latitude is not None and longitude is not None:
            data["refLatitude"] = latitude
            data["refLongitude"] = longitude
            elevation = util.attr(site, "RefElevation")
            if elevation is not None:
                data["refElevation"] = float(elevation) * units.length_scale
            break
    true_north = _true_north(model)
    if true_north is not None:
        data["trueNorth"] = true_north
    return data


def _lower_first(name: str) -> str:
    return name[0].lower() + name[1:] if name else name


def _dms_to_degrees(value: Any) -> float | None:
    if not value or not isinstance(value, (list, tuple)):
        return None
    parts = [float(part) for part in value] + [0.0] * (4 - len(value))
    # IFC requires all components to share a sign; some exporters only sign the
    # degrees, so the sign is taken from the first non-zero component.
    sign = next((-1.0 if part < 0 else 1.0 for part in parts if part != 0), 1.0)
    degrees, minutes, seconds, millionths = (abs(part) for part in parts[:4])
    return sign * (degrees + minutes / 60.0 + seconds / 3600.0 + millionths / 3.6e9)


def _true_north(model: Any) -> tuple[float, float] | None:
    for context in util.by_type(model, "IfcGeometricRepresentationContext", include_subtypes=False):
        direction = util.attr(context, "TrueNorth")
        ratios = util.attr(direction, "DirectionRatios") if direction is not None else None
        if ratios and len(ratios) >= 2:
            return float(ratios[0]), float(ratios[1])
    return None
