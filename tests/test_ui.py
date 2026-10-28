"""IfcUSD Studio: the local web UI's API, end to end over HTTP."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ifcusd.ui.inspect import display_value, file_kind


@pytest.fixture(scope="module")
def studio(tmp_path_factory: pytest.TempPathFactory):
    from ifcusd.ui import Studio, make_server

    workspace = tmp_path_factory.mktemp("studio")
    server, session = make_server("127.0.0.1", 0, studio=Studio(workspace, samples=workspace / "none"))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, session
    server.shutdown()
    server.server_close()


def _call(base: str, token: str, path: str, *, method: str = "GET", body=None, data: bytes | None = None, headers=None):
    request = urllib.request.Request(base + path, method=method, headers={"X-IfcUSD-Token": token, **(headers or {})})
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    elif data is not None:
        request.data = data
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw


def _wait(base: str, token: str, job_id: str) -> dict:
    deadline = time.time() + 120
    while time.time() < deadline:
        job = _call(base, token, f"/api/jobs/{job_id}")
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("conversion did not finish")


def test_page_carries_the_session_token(studio) -> None:
    base, session = studio
    page = urllib.request.urlopen(base + "/", timeout=10).read().decode()
    assert session.token in page and "IfcUSD Studio" in page
    assert b"function h(" in urllib.request.urlopen(base + "/static/app.js", timeout=10).read()


def test_api_needs_the_token_and_a_local_host(studio) -> None:
    base, session = studio
    with pytest.raises(urllib.error.HTTPError) as missing:
        urllib.request.urlopen(base + "/api/state", timeout=10)
    assert missing.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as rebound:  # DNS rebinding: a foreign Host header
        _call(base, session.token, "/api/state", headers={"Host": "attacker.example:80"})
    assert rebound.value.code == 403
    state = _call(base, session.token, "/api/state")
    assert "omniverse" in state["profiles"] and state["defaults"]["unity"]["physics"] is False


def test_convert_both_ways_and_inspect(studio, physics_ifc: Path) -> None:
    base, session = studio
    token = session.token
    uploaded = _call(
        base,
        token,
        "/api/upload",
        method="POST",
        data=physics_ifc.read_bytes(),
        headers={"X-File-Name": "physics%20model.ifc"},
    )
    assert uploaded["kind"] == "ifc" and uploaded["name"] == "physics model.ifc"

    job = _call(
        base,
        token,
        "/api/convert",
        method="POST",
        body={
            "direction": "to-usd",
            "input": uploaded["id"],
            "profile": "omniverse",
            "format": "usda",
            "options": {"meters_per_unit": 0.01, "physics": True},
            "validate": True,
        },
    )
    job = _wait(base, token, job["id"])
    assert job["status"] == "done", job
    assert job["validation"]["ok"] and job["report"]["counts"]["physicsPrims"] == 2
    usd = job["outputs"][0]
    assert usd["name"] == "physics model.usda" and usd["kind"] == "usd"

    summary = _call(base, token, f"/api/files/{usd['id']}/summary")
    facts = {f["label"]: f["value"] for f in summary["facts"]}
    assert facts["Metres per unit"] == "0.01" and facts["Profile"] == "omniverse"
    assert {p["name"] for p in summary["physics"]} >= {"PhysicsMassAPI", "PhysicsRigidBodyAPI"}
    found = _call(base, token, f"/api/files/{usd['id']}/search?q=Wall%201")["nodes"]
    wall = next(n for n in found if n["ifcClass"] == "IfcWall")
    assert "physics" in wall["tags"]
    node = _call(base, token, f"/api/files/{usd['id']}/node?path={urllib.request.quote(wall['id'])}")
    titles = [g["title"] for g in node["groups"]]
    assert titles[:3] == ["IFC attributes", "IfcUSD_Physics", "Physics (UsdPhysics)"]  # both forms side by side
    assert "IfcEntityAPI" in node["schemas"] and "PhysicsMassAPI" in node["schemas"]
    children = _call(base, token, f"/api/files/{usd['id']}/children?path={urllib.request.quote(wall['id'])}")
    assert any(c["name"] == "Body" for c in children["nodes"])

    back = _call(
        base,
        token,
        "/api/convert",
        method="POST",
        body={"direction": "to-ifc", "input": usd["id"], "source": uploaded["id"], "options": {}, "validate": False},
    )
    back = _wait(base, token, back["id"])
    assert back["status"] == "done", back
    ifc = back["outputs"][0]
    assert ifc["name"] == "physics model.updated.ifc"
    assert {k: v for k, v in back["report"]["counts"].items() if v and k != "matched"} == {}  # nothing changed

    # an edit layer from the simulation, composed on top in the UI
    from pxr import Sdf, Usd, UsdPhysics

    edits = Path(usd["location"]).with_name("sim.usda")
    stage = Usd.Stage.Open(usd["location"])
    layer = Sdf.Layer.CreateNew(str(edits))
    stage.GetSessionLayer().subLayerPaths.append(layer.identifier)
    stage.SetEditTarget(layer)
    wall_prim = stage.GetPrimAtPath(wall["id"])
    UsdPhysics.MassAPI(wall_prim).GetMassAttr().Set(2000.0)
    layer.Save()
    layer_entry = _call(base, token, "/api/open-path", method="POST", body={"path": str(edits)})
    edited = _call(
        base,
        token,
        "/api/convert",
        method="POST",
        body={"direction": "to-ifc", "input": usd["id"], "source": uploaded["id"], "layers": [layer_entry["id"]]},
    )
    edited = _wait(base, token, edited["id"])
    assert edited["status"] == "done", edited
    assert edited["report"]["counts"]["propertyValues"] == 1  # only the mass changed

    tree = _call(base, token, f"/api/files/{ifc['id']}/children?path=")
    assert tree["nodes"][0]["ifcClass"] == "IfcProject"
    walls = _call(base, token, f"/api/files/{ifc['id']}/search?q=IfcWall")["nodes"]
    detail = _call(base, token, f"/api/files/{ifc['id']}/node?path={urllib.request.quote(walls[0]['id'])}")
    physics = next(g for g in detail["groups"] if g["title"] == "IfcUSD_Physics")
    assert physics["tone"] == "physics" and any(r["name"] == "Mass" for r in physics["rows"])

    downloaded = _call(base, token, f"/api/files/{ifc['id']}/download")
    assert downloaded.startswith(b"ISO-10303-21")
    _call(base, token, f"/api/files/{ifc['id']}", method="DELETE")
    assert not Path(ifc["location"]).exists()


def test_bad_requests_are_explained(studio, tmp_path: Path) -> None:
    base, session = studio
    with pytest.raises(urllib.error.HTTPError) as wrong_kind:
        _call(base, session.token, "/api/upload", method="POST", data=b"x", headers={"X-File-Name": "notes.txt"})
    assert wrong_kind.value.code == 400 and "IFC" in json.loads(wrong_kind.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as no_file:
        _call(base, session.token, "/api/open-path", method="POST", body={"path": str(tmp_path / "missing.ifc")})
    assert no_file.value.code == 400
    local = tmp_path / "local.usda"
    local.write_text("#usda 1.0\n", encoding="utf-8")
    entry = _call(base, session.token, "/api/open-path", method="POST", body={"path": str(local)})
    again = _call(base, session.token, "/api/open-path", method="POST", body={"path": f'"{local}"'})
    assert again["id"] == entry["id"]  # one entry per file
    _call(base, session.token, f"/api/files/{entry['id']}", method="DELETE")
    assert local.exists()  # files opened in place are only forgotten, never deleted


def test_paths_are_off_when_other_computers_can_connect(tmp_path: Path) -> None:
    from ifcusd.ui import Studio

    session = Studio(tmp_path / "workspace", samples=tmp_path / "none")
    session.allow_paths = False  # what make_server sets for a non-loopback host
    local = tmp_path / "model.ifc"
    local.write_text("ISO-10303-21;", encoding="utf-8")
    with pytest.raises(ValueError, match="this computer only"):
        session.open_path(str(local))
    assert session.state()["allowPaths"] is False


def test_schema_lists_the_plugin_and_extension(studio) -> None:
    base, session = studio
    schema = _call(base, session.token, "/api/schema")
    apis = {s["name"]: s for s in schema["schemas"]}
    assert apis["IfcPropertySetAPI"]["apply"] == "multiple" and apis["IfcEntityAPI"]["apply"] == "single"
    assert any(p["name"] == "ifc:globalId" for p in apis["IfcEntityAPI"]["properties"])
    assert [s["name"] for s in schema["extension"]] == ["IfcUSD_Physics", "IfcUSD_Simulation"]


def test_values_read_well() -> None:
    from pxr import Gf, Vt

    assert display_value(Gf.Vec3f(0.1, 2, 3), single=True) == ("(0.1, 2, 3)", None)
    assert display_value(Vt.IntArray(list(range(10)))) == ("[0, 1, 2, 3, 4, 5, …]", 10)
    assert display_value(1840.1234130859375, single=True)[0] == "1840.123"
    assert display_value(True) == ("true", None)
    assert file_kind("a.IFCZIP") == "ifc" and file_kind("b.usdz") == "usd" and file_kind("c.obj") is None
