from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

pxr = pytest.importorskip("pxr", reason="OpenUSD Python bindings (usd-core) are required")
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is required")

SCHEMAS = ("IFC2X3", "IFC4", "IFC4X3_ADD2")
SAMPLES = ROOT / "ifc"


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("models")


@pytest.fixture(scope="session")
def ifc_files(model_dir: Path) -> dict[str, Path]:
    from model_factory import write_model

    return {schema: write_model(model_dir, schema) for schema in SCHEMAS}


@pytest.fixture(scope="session")
def converted(ifc_files: dict[str, Path], model_dir: Path):
    """Each synthetic model converted once with default options: {schema: (ifc, usd, result)}."""
    from ifcusd import ConversionOptions, convert

    results = {}
    for schema, path in ifc_files.items():
        target = model_dir / f"{schema}.usda"
        results[schema] = (path, target, convert(path, target, ConversionOptions(threads=2)))
    return results


@pytest.fixture(params=SCHEMAS)
def schema(request: pytest.FixtureRequest) -> str:
    return request.param


def open_stage(path: Path):
    from pxr import Usd

    stage = Usd.Stage.Open(str(path))
    assert stage is not None, f"could not open {path}"
    return stage


def prims_by_class(stage, ifc_class: str) -> list:
    return [p for p in stage.TraverseAll() if p.GetAttribute("ifc:class").Get() == ifc_class]


def prim_named(stage, name: str):
    matches = [p for p in stage.TraverseAll() if p.GetAttribute("ifc:name").Get() == name]
    assert matches, f"no prim with ifc:name {name!r}"
    return matches[0]
