"""OpenUSD to IFC conversion entry points."""

from __future__ import annotations

import hashlib
import itertools
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.api.owner
import ifcopenshell.api.project
import ifcopenshell.guid

from ifcusd._version import __version__
from ifcusd.dependencies import usd_version
from ifcusd.errors import InputError, OutputError
from ifcusd.ifc.reader import available_schemas, compatible_schema, open_ifc, sha256_of
from ifcusd.options import IfcExportOptions
from ifcusd.reverse.report import IfcExportReport

USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
IFC_OUTPUT_EXTENSIONS = (".ifc",)
GUID_NAMESPACE = uuid.UUID("2c6d5f0e-7b1a-5e93-8d24-9a0f6c3e1b57")


@dataclass(frozen=True)
class IfcExportResult:
    """Outcome of a USD to IFC conversion.

    Attributes:
        input_path: The USD file.
        output_path: The written IFC file.
        source_path: The source IFC file (merge mode), else ``None``.
        model: The written ``ifcopenshell.file``, for further processing.
        report: Full [`IfcExportReport`][ifcusd.reverse.report.IfcExportReport].
    """

    input_path: Path
    output_path: Path
    source_path: Path | None = None
    model: Any = field(default=None, repr=False, compare=False)
    report: IfcExportReport | None = None


class UsdToIfcConverter:
    """Converts OpenUSD stages to IFC, either updating a source IFC or rebuilding one.

    Args:
        options: Export options; defaults to ``IfcExportOptions()``.

    Example:
        ```python
        from ifcusd import UsdToIfcConverter

        converter = UsdToIfcConverter()
        result = converter.convert(
            "model.usdc", "model.trained.ifc", source="model.ifc", layers=["robot_run_01.usda"]
        )
        print("\\n".join(result.report.summary_lines()))
        ```
    """

    def __init__(self, options: IfcExportOptions | None = None) -> None:
        self.options = options or IfcExportOptions()

    def convert(
        self,
        input_path: str | os.PathLike[str],
        output_path: str | os.PathLike[str],
        *,
        source: str | os.PathLike[str] | None = None,
        layers: list[str | os.PathLike[str]] | None = None,
        report_path: str | os.PathLike[str] | None = None,
        validate: bool = False,
    ) -> IfcExportResult:
        """Convert one USD stage to IFC.

        Args:
            input_path: ``.usd``, ``.usda``, ``.usdc`` or ``.usdz`` file.
            output_path: ``.ifc`` file. Parent folders are created.
            source: The IFC file the USD was converted from. When given, it is
                updated (merge mode): everything in it is kept and the changes
                and additions made in USD are written. Without it the model is
                rebuilt from USD alone (standalone mode).
            layers: Additional USD layers composed on top of the input, in
                order (later layers win), e.g. robot runs recorded separately.
            report_path: Also write the export report as JSON here.
            validate: Validate the written file against its IFC schema.

        Returns:
            The [`IfcExportResult`][ifcusd.reverse.converter.IfcExportResult].

        Raises:
            InputError: An input is missing or unreadable, or ``schema`` conflicts with the source.
            OutputError: The output extension is unsupported or the file cannot be written.
        """
        from ifcusd.reverse.builder import ModelWriter
        from ifcusd.reverse.stage import UsdScene, open_stage

        options = self.options
        started = time.perf_counter()
        timings: dict[str, float] = {}
        usd_path = Path(input_path)
        target = Path(output_path)
        layer_paths = [Path(p) for p in layers or []]
        source_path = Path(source) if source is not None else None
        if usd_path.suffix.lower() not in USD_EXTENSIONS:
            raise InputError(f"Input must end with one of {', '.join(USD_EXTENSIONS)}: {usd_path}")
        if target.suffix.lower() not in IFC_OUTPUT_EXTENSIONS:
            raise OutputError(f"Output must end with one of {', '.join(IFC_OUTPUT_EXTENSIONS)}: {target}")
        for path in (usd_path, *layer_paths, *([source_path] if source_path else [])):
            if not path.is_file():
                raise InputError(f"File not found: {path}")

        stage, provenance = open_stage(usd_path, layer_paths)
        scene = UsdScene(stage, options, provenance)
        warnings: list[str] = []
        source_sha = None
        matches = None
        if source_path is not None:
            opened = open_ifc(source_path, allow_schema_fallback=options.schema_fallback)
            model = opened.model
            warnings.extend(opened.warnings)
            source_sha = opened.sha256
            if options.schema and compatible_schema(options.schema, available_schemas()) != opened.schema:
                raise InputError(
                    f"In merge mode the source schema {opened.schema} is kept; schema={options.schema!r} conflicts"
                )
            if provenance.get("sourceSha256"):
                matches = provenance["sourceSha256"] == source_sha
                if not matches:
                    warnings.append(
                        f"{usd_path.name} was converted from a different file than {source_path.name} "
                        "(SHA-256 mismatch); prims are matched by GlobalId"
                    )
        else:
            schema = self._standalone_schema(scene)
            model = ifcopenshell.api.project.create_file(version=schema)
        timings["read"] = time.perf_counter() - started

        mark = time.perf_counter()
        seed = _seed([usd_path, *layer_paths], source_sha)
        with deterministic_guids(seed), owner_history_for(model) as owner_history:
            writer = ModelWriter(
                model,
                scene,
                options,
                merge=source_path is not None,
                identity=source_sha or str(provenance.get("sourceSha256") or usd_path.name),
                owner_history=owner_history,
                project_name=usd_path.stem,
            )
            writer.run()
        timings["write"] = time.perf_counter() - mark

        mark = time.perf_counter()
        self._header(model, target, merge=source_path is not None)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            model.write(str(target))
        except Exception as exc:
            raise OutputError(f"Could not write {target}: {exc}") from exc
        timings["save"] = time.perf_counter() - mark

        validation = None
        if validate:
            baseline = (
                open_ifc(source_path, allow_schema_fallback=options.schema_fallback).model if source_path else None
            )
            validation = validate_ifc(model, baseline=baseline)
        timings["total"] = time.perf_counter() - started
        report = IfcExportReport(
            input_path=str(usd_path),
            output_path=str(target),
            mode="merge" if source_path is not None else "standalone",
            ifc_schema=str(model.schema_identifier),
            source_path=str(source_path) if source_path else None,
            layers=[str(p) for p in layer_paths],
            source_sha256=source_sha,
            source_matches_usd=matches,
            converter_version=__version__,
            ifcopenshell_version=str(ifcopenshell.version),
            usd_version=usd_version(),
            options=options.to_dict(),
            counts=dict(sorted(writer.counts.items())),
            created_objects=writer.created_objects,
            not_in_usd=writer.not_in_usd,
            validation=validation,
            warnings=warnings + writer.warnings,
            timings={key: round(value, 4) for key, value in timings.items()},
        )
        if report_path is not None:
            report.to_json(report_path)
        return IfcExportResult(usd_path, target, source_path, model, report)

    def _standalone_schema(self, scene: Any) -> str:
        available = available_schemas()
        requested = self.options.schema or scene.file_info.get("ifc:file:schema")
        if requested:
            schema = compatible_schema(str(requested), available)
            if schema is None and str(requested).upper().startswith("IFC5"):
                schema = compatible_schema("IFC4X3", available)
            if schema is not None:
                return schema
            if self.options.schema:
                raise InputError(f"IFC schema {self.options.schema!r} is not available (have {', '.join(available)})")
        return "IFC4"

    @staticmethod
    def _header(model: Any, target: Path, *, merge: bool) -> None:
        file_name = model.header.file_name
        file_name.name = target.name
        file_name.time_stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        file_name.preprocessor_version = f"IfcUSD {__version__} (IfcOpenShell {ifcopenshell.version})"
        if not merge:
            file_name.originating_system = f"IfcUSD {__version__}"


def convert_usd_to_ifc(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    options: IfcExportOptions | None = None,
    **kwargs: Any,
) -> IfcExportResult:
    """Convert a USD stage to IFC in one call.

    Equivalent to ``UsdToIfcConverter(options).convert(input_path, output_path, **kwargs)``.

    Args:
        input_path: ``.usd``, ``.usda``, ``.usdc`` or ``.usdz`` file.
        output_path: ``.ifc`` file.
        options: Export options; defaults to ``IfcExportOptions()``.
        **kwargs: ``source``, ``layers``, ``report_path`` and ``validate``, see
            [`UsdToIfcConverter.convert`][ifcusd.reverse.converter.UsdToIfcConverter.convert].

    Returns:
        The [`IfcExportResult`][ifcusd.reverse.converter.IfcExportResult].

    Example:
        ```python
        from ifcusd import convert, convert_usd_to_ifc

        convert("model.ifc", "model.usdc")                     # IFC -> USD
        # ... train robots in Omniverse / Isaac Sim, save their data in model.usdc ...
        convert_usd_to_ifc("model.usdc", "model.updated.ifc", source="model.ifc")  # USD -> IFC
        ```
    """
    return UsdToIfcConverter(options).convert(input_path, output_path, **kwargs)


def validate_ifc(model: Any, baseline: Any = None) -> dict[str, Any]:
    """Schema validation (attribute types, cardinalities, inverses) with IfcOpenShell.

    With a ``baseline`` model (the source in merge mode), issues the source
    already has are marked ``"inSource": true`` and counted separately, so
    ``newErrors`` tells whether the export introduced any problem.
    """
    issues = _validation_issues(model)
    known = {_issue_key(issue) for issue in _validation_issues(baseline)} if baseline is not None else set()
    for issue in issues:
        issue["inSource"] = _issue_key(issue) in known
    errors = [i for i in issues if str(i.get("level", "error")).lower() == "error"]
    new_errors = [i for i in errors if not i["inSource"]]
    return {
        "ok": not errors,
        "errors": len(errors),
        "newErrors": len(new_errors),
        "issues": (new_errors + [i for i in issues if i not in new_errors])[:100],
    }


def _validation_issues(model: Any) -> list[dict[str, Any]]:
    import ifcopenshell.validate

    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(model, logger, express_rules=False)
    return [
        {key: str(value) for key, value in statement.items() if key in ("level", "message", "instance", "attribute")}
        for statement in logger.statements
    ]


def _issue_key(issue: dict[str, Any]) -> tuple[str, str, str]:
    """Identifies an issue independently of instance numbering: (class, attribute, message)."""
    instance = issue.get("instance", "")
    cls = instance.split("=", 1)[1].split("(", 1)[0] if "=" in instance else instance
    return cls, issue.get("attribute", ""), issue.get("message", "").split("\n", 1)[0]


@contextmanager
def deterministic_guids(seed: str) -> Iterator[None]:
    """Make ``ifcopenshell.guid.new`` reproducible: the same inputs give the same GlobalIds."""
    counter = itertools.count()
    original = ifcopenshell.guid.new

    def new() -> str:
        return ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{seed}:#{next(counter)}").hex)

    ifcopenshell.guid.new = new
    try:
        yield
    finally:
        ifcopenshell.guid.new = original


@contextmanager
def owner_history_for(model: Any) -> Iterator[Any]:
    """Yields a callable returning the owner history new entities get.

    IFC2X3 requires an ``IfcOwnerHistory`` on every rooted entity: one naming
    IfcUSD as the application is created (or reused) and IfcOpenShell's owner
    settings point to it for the duration of the export. Later schemas leave it
    unset.
    """
    if str(model.schema).upper() != "IFC2X3":
        yield lambda: None
        return
    owner = ifcopenshell.api.owner
    application = next(
        (a for a in model.by_type("IfcApplication") if a.ApplicationIdentifier == "IfcUSD"),
        None,
    )
    if application is None:
        organisation = owner.add_organisation(model, identification="IfcUSD", name="IfcUSD")
        application = owner.add_application(
            model,
            application_developer=organisation,
            version=__version__,
            application_full_name="IfcUSD",
            application_identifier="IfcUSD",
        )
    organisation = application.ApplicationDeveloper
    user = next(
        (u for u in model.by_type("IfcPersonAndOrganization") if u.TheOrganization == organisation),
        None,
    )
    if user is None:
        person = owner.add_person(model, identification="IfcUSD", family_name="IfcUSD")
        user = owner.add_person_and_organisation(model, person=person, organisation=organisation)
    settings = owner.settings
    previous = (settings.get_user, settings.get_application)
    settings.get_user = lambda _file: user
    settings.get_application = lambda _file: application
    cache: dict[str, Any] = {}

    def history() -> Any:
        if "history" not in cache:
            cache["history"] = owner.create_owner_history(model)
        return cache["history"]

    try:
        yield history
    finally:
        settings.get_user, settings.get_application = previous


def _seed(paths: list[Path], source_sha: str | None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(sha256_of(path).encode("ascii"))
    digest.update((source_sha or "").encode("ascii"))
    return digest.hexdigest()
