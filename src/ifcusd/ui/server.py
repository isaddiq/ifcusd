"""IfcUSD Studio: a local web UI for IFC <-> USD conversion.

The server uses only the standard library and listens on ``127.0.0.1``. Every
API call carries a per-session token that is only handed to the page the
server itself serves, and requests whose ``Host`` is not this machine are
refused, so other web pages cannot drive it.

Endpoints (JSON unless noted):

``GET  /api/state``                     version, profiles, option defaults, files, jobs
``POST /api/upload``                    raw file body, ``X-File-Name`` header
``POST /api/open-path``                 ``{"path": ...}``: use a file on this computer in place
``DELETE /api/files/<id>``              forget a file (uploads and outputs are deleted)
``POST /api/convert``                   start a conversion job
``GET  /api/jobs/<id>``                 job status, progress and report
``GET  /api/files/<id>/download``       the file itself (``?token=``)
``GET  /api/files/<id>/summary|children|node|search``   inspection
``GET  /api/schema``                    the IfcUSD schema and mapping
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import queue
import secrets
import shutil
import tempfile
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ifcusd.ui.inspect import file_kind, open_view, schema_description

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "app.css": "text/css"}
DEFAULT_PORT = 8765
CHUNK = 1 << 20
MAX_VIEWS = 4  # opened models and stages kept for browsing
USD_FORMATS = ("usda", "usdc", "usdz", "usd")
IFC_SCHEMAS = ("IFC4", "IFC4X3", "IFC2X3")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
WRONG_KIND = "choose an IFC (.ifc, .ifczip, .ifcx) or USD (.usd, .usda, .usdc, .usdz) file"

# Conversion options the page may set; anything else is ignored.
TO_USD_OPTIONS = {
    "up_axis": str,
    "meters_per_unit": float,
    "naming": str,
    "instancing": bool,
    "include_curves": bool,
    "include_openings": bool,
    "spaces": str,
    "use_world_coords": bool,
    "include_properties": bool,
    "include_quantities": bool,
    "include_materials": bool,
    "include_relationships": bool,
    "inherit_type_properties": bool,
    "semantic_labels": bool,
    "legacy_semantics": bool,
    "physics": bool,
    "material_model": str,
}
TO_IFC_OPTIONS = {
    "schema": str,
    "update_attributes": bool,
    "update_properties": bool,
    "update_placements": bool,
    "include_new_objects": bool,
    "include_geometry": bool,
    "include_physics": bool,
    "include_semantics": bool,
    "include_time_series": bool,
    "include_trajectories": bool,
    "property_templates": bool,
}


# ---------------------------------------------------------------------- state


@dataclass
class FileEntry:
    id: str
    name: str
    path: Path
    kind: str
    origin: str  # upload, sample, local or output
    created: float = field(default_factory=time.time)

    @property
    def owned(self) -> bool:
        """Whether the file lives in the workspace (and may be deleted)."""
        return self.origin in ("upload", "output")

    def to_dict(self) -> dict[str, Any]:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = None
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "origin": self.origin,
            "size": size,
            "location": str(self.path),
            "created": self.created,
        }


@dataclass
class Job:
    id: str
    direction: str
    request: dict[str, Any]
    status: str = "queued"  # queued, running, done, failed
    stage: str = "queued"
    fraction: float | None = 0.0
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    summary: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    outputs: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def to_dict(self, files: dict[str, FileEntry]) -> dict[str, Any]:
        return {
            "id": self.id,
            "direction": self.direction,
            "status": self.status,
            "stage": self.stage,
            "fraction": self.fraction,
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
            "summary": self.summary,
            "report": self.report,
            "validation": self.validation,
            "outputs": [files[i].to_dict() for i in self.outputs if i in files],
            "log": self.log[-200:],
        }


class _JobLog(logging.Handler):
    def __init__(self, job: Job) -> None:
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        self.job.log.append(f"{record.levelname.lower()}: {record.getMessage()}")


class Studio:
    """Files, conversion jobs and opened views of one UI session."""

    def __init__(self, workspace: Path | None = None, samples: Path | None = None) -> None:
        self.token = secrets.token_urlsafe(24)
        self.allow_paths = True  # off when the server is reachable from other computers
        self.workspace = Path(workspace) if workspace else Path(tempfile.gettempdir()) / "ifcusd-studio"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.files: dict[str, FileEntry] = {}
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._views: OrderedDict[str, Any] = OrderedDict()
        self._view_lock = threading.Lock()
        self._queue: queue.Queue[Job] = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="ifcusd-studio-worker", daemon=True)
        self._worker.start()
        self.samples = [self._register(p, "sample") for p in _sample_files(samples)]

    # ------------------------------------------------------------------ files

    def _register(self, path: Path, origin: str, name: str | None = None) -> FileEntry:
        kind = file_kind(path)
        if kind is None:
            raise ValueError(f"{path.name}: {WRONG_KIND}")
        entry = FileEntry(id=secrets.token_hex(6), name=name or path.name, path=path, kind=kind, origin=origin)
        with self._lock:
            self.files[entry.id] = entry
        return entry

    def new_folder(self) -> Path:
        folder = self.workspace / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        folder.mkdir(parents=True)
        return folder

    def upload(self, name: str, stream: Any, length: int) -> FileEntry:
        name = Path(name.replace("\\", "/")).name.strip() or "upload"
        if file_kind(name) is None:
            raise ValueError(f"{name}: {WRONG_KIND}")
        target = self.new_folder() / name
        remaining = length
        with target.open("wb") as handle:
            while remaining > 0:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError("the upload ended early")
                handle.write(chunk)
                remaining -= len(chunk)
        return self._register(target, "upload")

    def open_path(self, raw: str) -> FileEntry:
        if not self.allow_paths:
            raise ValueError("Files can only be opened by path when IfcUSD Studio listens on this computer only")
        path = Path(raw.strip().strip('"')).expanduser()
        if not path.is_file():
            raise ValueError(f"No file at {path}")
        path = path.resolve()
        with self._lock:
            known = next((f for f in self.files.values() if f.path == path), None)
        if known is not None:  # opened before (or a sample): one entry per file
            self._drop_view(known.id)  # it may have changed on disk since
            return known
        return self._register(path, "local")

    def remove(self, file_id: str) -> None:
        with self._lock:
            entry = self.files.pop(file_id, None)
        if entry is None:
            raise KeyError(file_id)
        self._drop_view(file_id)
        if entry.owned:
            entry.path.unlink(missing_ok=True)
            try:
                entry.path.parent.rmdir()  # only when nothing else is in its folder
            except OSError:
                pass

    def file(self, file_id: str) -> FileEntry:
        with self._lock:
            entry = self.files.get(file_id)
        if entry is None or not entry.path.is_file():
            raise KeyError(file_id)
        return entry

    # ------------------------------------------------------------------ views

    def view(self, file_id: str) -> Any:
        entry = self.file(file_id)
        with self._view_lock:
            view = self._views.get(file_id)
            if view is None:
                view = open_view(entry.path)
                self._views[file_id] = view
                while len(self._views) > MAX_VIEWS:
                    self._views.popitem(last=False)
            self._views.move_to_end(file_id)
            return view

    def _drop_view(self, file_id: str) -> None:
        with self._view_lock:
            self._views.pop(file_id, None)

    # ------------------------------------------------------------------ jobs

    def submit(self, request: dict[str, Any]) -> Job:
        direction = request.get("direction")
        if direction not in ("to-usd", "to-ifc"):
            raise ValueError("direction must be 'to-usd' or 'to-ifc'")
        source = self.file(str(request.get("input", "")))
        expected = "ifc" if direction == "to-usd" else "usd"
        if source.kind != expected:
            raise ValueError(f"{source.name} is not an {expected.upper()} file")
        if direction == "to-ifc":
            if request.get("source") and self.file(str(request["source"])).kind != "ifc":
                raise ValueError("the source model must be an IFC file")
            for layer in request.get("layers", []) or []:
                if self.file(str(layer)).kind != "usd":
                    raise ValueError("edit layers must be USD files")
        job = Job(id=secrets.token_hex(6), direction=direction, request=request)
        with self._lock:
            self.jobs[job.id] = job
        self._queue.put(job)
        return job

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            handler = _JobLog(job)
            logging.getLogger("ifcusd").addHandler(handler)
            job.status, job.stage, job.started = "running", "starting", time.time()
            try:
                if job.direction == "to-usd":
                    self._to_usd(job)
                else:
                    self._to_ifc(job)
                job.status, job.stage, job.fraction = "done", "done", 1.0
            except Exception as exc:  # reported to the page
                job.status, job.error = "failed", _message(exc)
                job.log.append(traceback.format_exc(limit=6))
            finally:
                job.finished = time.time()
                logging.getLogger("ifcusd").removeHandler(handler)

    def _to_usd(self, job: Job) -> None:
        from ifcusd import ConversionOptions
        from ifcusd.converter import convert_file
        from ifcusd.validate import validate_usd

        request = job.request
        source = self.file(request["input"])
        overrides = _options(request.get("options", {}), TO_USD_OPTIONS)

        def progress(stage: str, fraction: float) -> None:
            job.stage, job.fraction = stage, max(0.0, min(1.0, float(fraction)))

        options = ConversionOptions.for_profile(
            str(request.get("profile") or "generic"), progress=progress, **overrides
        )
        fmt = str(request.get("format") or "usda").lower()
        if fmt not in USD_FORMATS:
            raise ValueError(f"format must be one of {', '.join(USD_FORMATS)}")
        output = self.new_folder() / f"{Path(source.name).stem}.{fmt}"
        result = convert_file(source.path, output, options)
        if result.report is not None:
            job.summary = result.report.summary_lines()
            job.report = _jsonable(result.report.to_dict())
        if request.get("validate"):
            job.stage, job.fraction = "validating", None
            validation = validate_usd(output, source=source.path if source.path.suffix.lower() != ".ifcx" else None)
            job.validation = {"ok": validation.ok, "summary": validation.summary_lines(limit=40)}
        job.outputs.append(self._register(output, "output").id)

    def _to_ifc(self, job: Job) -> None:
        from ifcusd import IfcExportOptions
        from ifcusd.reverse.converter import convert_usd_to_ifc

        request = job.request
        usd = self.file(request["input"])
        source = self.file(request["source"]) if request.get("source") else None
        if source is not None and source.kind != "ifc":
            raise ValueError(f"{source.name} is not an IFC file")
        layers = [self.file(i) for i in request.get("layers", []) or []]
        for layer in layers:
            if layer.kind != "usd":
                raise ValueError(f"{layer.name} is not a USD layer")
        overrides = _options(request.get("options", {}), TO_IFC_OPTIONS)
        if not overrides.get("schema") or source is not None:
            overrides.pop("schema", None)
        elif overrides["schema"] not in IFC_SCHEMAS:
            raise ValueError(f"schema must be one of {', '.join(IFC_SCHEMAS)}")
        options = IfcExportOptions(**overrides)
        name = f"{Path(source.name).stem}.updated.ifc" if source is not None else f"{Path(usd.name).stem}.ifc"
        output = self.new_folder() / name
        job.stage, job.fraction = "converting", None
        result = convert_usd_to_ifc(
            usd.path,
            output,
            options,
            source=source.path if source is not None else None,
            layers=[layer.path for layer in layers] or None,
            validate=bool(request.get("validate")),
        )
        report = result.report
        if report is not None:
            job.summary = report.summary_lines()
            job.report = _jsonable(report.to_dict())
            if request.get("validate") and report.validation is not None:
                validation = report.validation
                issues = [
                    f"{i.get('instance', '')[:100]} {i.get('attribute', '')}: {i.get('message', '')}"
                    for i in validation.get("issues", [])[:40]
                ]
                job.validation = {"ok": bool(validation.get("ok")), "summary": issues}
        job.outputs.append(self._register(output, "output").id)

    # ------------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        from ifcusd import PROFILES, SCHEMA_VERSION, ConversionOptions, IfcExportOptions, __version__

        defaults = {}
        for profile in PROFILES:
            data = ConversionOptions.for_profile(profile).to_dict()
            defaults[profile] = {k: data[k] for k in TO_USD_OPTIONS}
        export = IfcExportOptions().to_dict()
        with self._lock:
            files = [f.to_dict() for f in self.files.values()]
            jobs = [j.to_dict(self.files) for j in self.jobs.values()]
        return {
            "version": __version__,
            "mappingVersion": SCHEMA_VERSION,
            "profiles": list(PROFILES),
            "defaults": defaults,
            "exportDefaults": {k: export.get(k) for k in TO_IFC_OPTIONS},
            "formats": list(USD_FORMATS),
            "ifcSchemas": list(IFC_SCHEMAS),
            "files": files,
            "jobs": jobs,
            "workspace": str(self.workspace),
            "allowPaths": self.allow_paths,
        }


def _options(raw: Any, allowed: dict[str, type]) -> dict[str, Any]:
    """Typed option overrides from the page, limited to ``allowed``."""
    if not isinstance(raw, dict):
        raise ValueError("options must be an object")
    options: dict[str, Any] = {}
    for name, kind in allowed.items():
        if name not in raw or raw[name] in (None, ""):
            continue
        value = raw[name]
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        elif kind is float:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number") from None
            if not value > 0:
                raise ValueError(f"{name} must be greater than 0")
        else:
            value = str(value)
        options[name] = value
    return options


def _message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _jsonable(data: Any) -> Any:
    return json.loads(json.dumps(data, default=str))


def _sample_files(folder: Path | None) -> list[Path]:
    """IFC files of the repository's ``ifc/`` folder, when running from a source checkout."""
    if folder is None:
        folder = Path(__file__).resolve().parents[3] / "ifc"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and file_kind(p) == "ifc")


# ---------------------------------------------------------------------- HTTP


class _Handler(BaseHTTPRequestHandler):
    server_version = "IfcUSDStudio"
    studio: Studio
    allowed_hosts: set[str] | None

    # ------------------------------------------------------------------ plumbing

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler API
        log.debug("%s - %s", self.address_string(), format % args)

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        if self.allowed_hosts is None:
            return True
        return (self.headers.get("Host") or "").lower() in self.allowed_hosts

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        token = self.headers.get("X-IfcUSD-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(token.encode(), self.studio.token.encode())

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return
        try:
            if not parts or parts[0] != "api":
                if method == "GET":
                    self._static(parts)
                else:
                    self._error(HTTPStatus.METHOD_NOT_ALLOWED, "not allowed")
                return
            if not self._token_ok(query):
                self._error(HTTPStatus.FORBIDDEN, "missing or wrong session token; reload the page")
                return
            self._api(method, parts[1:], query)
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, f"not found: {exc.args[0] if exc.args else ''}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, _message(exc))
        except Exception as exc:  # pragma: no cover - unexpected; keep the server alive
            log.exception("request failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, _message(exc))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._route("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    # ------------------------------------------------------------------ routes

    def _static(self, parts: list[str]) -> None:
        if not parts or parts == ["index.html"]:
            page = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", self.studio.token)
            self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
        elif len(parts) == 2 and parts[0] == "static" and parts[1] in STATIC_FILES:
            body = (STATIC / parts[1]).read_bytes()
            self._send(HTTPStatus.OK, body, f"{STATIC_FILES[parts[1]]}; charset=utf-8")
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def _api(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        studio = self.studio
        route = (method, parts[0] if parts else "")
        arg = (query.get("path") or query.get("q") or [""])[0]
        if route == ("GET", "state"):
            self._json(studio.state())
        elif route == ("GET", "schema"):
            self._json(schema_description())
        elif route == ("POST", "upload"):
            name = unquote(self.headers.get("X-File-Name") or "")
            length = int(self.headers.get("Content-Length") or -1)
            if length < 0:
                raise ValueError("Content-Length is required")
            self._json(studio.upload(name, self.rfile, length).to_dict(), HTTPStatus.CREATED)
        elif route == ("POST", "open-path"):
            self._json(studio.open_path(str(self._body().get("path", ""))).to_dict(), HTTPStatus.CREATED)
        elif route == ("POST", "convert"):
            job = studio.submit(self._body())
            self._json(job.to_dict(studio.files), HTTPStatus.ACCEPTED)
        elif route == ("GET", "jobs") and len(parts) == 2:
            job = studio.jobs.get(parts[1])
            if job is None:
                raise KeyError(parts[1])
            self._json(job.to_dict(studio.files))
        elif route == ("DELETE", "files") and len(parts) == 2:
            studio.remove(parts[1])
            self._json({"removed": parts[1]})
        elif route == ("GET", "files") and len(parts) == 3:
            self._file(parts[1], parts[2], arg, query)
        else:
            self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _file(self, file_id: str, action: str, arg: str, query: dict[str, list[str]]) -> None:
        studio = self.studio
        if action == "download":
            entry = studio.file(file_id)
            self._download(entry)
            return
        view = studio.view(file_id)
        if action == "summary":
            self._json(view.summary())
        elif action == "children":
            offset = int((query.get("offset") or ["0"])[0])
            self._json(view.children(arg, offset=offset))
        elif action == "node":
            self._json(view.node(arg))
        elif action == "search":
            self._json({"nodes": view.search(arg)})
        else:
            self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _download(self, entry: FileEntry) -> None:
        size = entry.path.stat().st_size
        content_type = mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        fallback = entry.name.encode("ascii", "replace").decode("ascii").replace('"', "").replace("?", "_")
        self.send_header(
            "Content-Disposition", f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(entry.name)}"
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        with entry.path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, CHUNK)


def make_server(
    host: str = "127.0.0.1", port: int = DEFAULT_PORT, *, studio: Studio | None = None
) -> tuple[ThreadingHTTPServer, Studio]:
    """A server on the first free port from ``port`` (``0`` picks any free port)."""
    from ifcusd import schema

    schema.register()  # schema-aware browsing (IfcEntityAPI ...) when no stage is open yet
    studio = studio or Studio()
    handler = type("Handler", (_Handler,), {"studio": studio, "allowed_hosts": None})
    candidates = [port] if port == 0 else [*range(port, port + 20), 0]
    last: OSError | None = None
    for candidate in candidates:
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
            break
        except OSError as exc:
            last = exc
    else:
        raise OSError(f"no free port from {port}: {last}")
    server.daemon_threads = True
    bound = server.server_address[1]
    if host in LOOPBACK:
        handler.allowed_hosts = {f"{name}:{bound}" for name in ("127.0.0.1", "localhost", "[::1]")}
    else:  # other computers can load the page (and its token): keep them out of this one's files
        studio.allow_paths = False
    return server, studio


def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
    workspace: Path | None = None,
) -> None:
    """Run IfcUSD Studio until interrupted (Ctrl+C)."""
    server, studio = make_server(host, port, studio=Studio(workspace))
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{server.server_address[1]}/"
    print(f"IfcUSD Studio is running at {url}")
    print(f"Files are kept in {studio.workspace}")
    if not studio.allow_paths:
        print(f"Warning: {host} is reachable from other computers, which can then convert files here.")
        print("Opening files by path is off; use 127.0.0.1 (the default) to keep IfcUSD Studio private.")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nIfcUSD Studio stopped.")
    finally:
        server.server_close()
