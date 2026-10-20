"""Command line interface, schema plugin and the bundled sample files."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, SAMPLES, open_stage

from ifcusd import ConversionOptions, convert, validate_usd
from ifcusd.cli import main


def test_cli_convert_validate_info_schema(ifc_files, tmp_path, capsys) -> None:
    target = tmp_path / "cli.usdc"
    report = tmp_path / "report.json"
    code = main(
        [
            "convert",
            str(ifc_files["IFC4"]),
            str(target),
            "--profile",
            "omniverse",
            "--report",
            str(report),
            "--validate",
            "--strict",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0, output
    assert "missing 0" in output and "OK" in output
    assert json.loads(report.read_text(encoding="utf-8"))["complete"] is True

    assert main(["validate", str(target), "--source", str(ifc_files["IFC4"])]) == 0
    assert "OK" in capsys.readouterr().out
    assert main(["info", str(ifc_files["IFC2X3"]), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["schema"] == "IFC2X3" and info["units"]["LENGTHUNIT"] == "MILLIMETRE"
    assert main(["schema"]) == 0
    assert capsys.readouterr().out.strip().endswith("resources")


def test_cli_reports_errors_with_exit_codes(tmp_path, capsys) -> None:
    assert main(["convert", str(tmp_path / "missing.ifc"), str(tmp_path / "x.usda")]) == 3
    assert main(["convert", str(tmp_path / "missing.ifc"), str(tmp_path / "x.usda"), "--meters-per-unit", "-1"]) == 2
    assert "error:" in capsys.readouterr().err


def test_schema_plugin_registers_in_fresh_process(ifc_files, tmp_path) -> None:
    target = tmp_path / "plugin.usda"
    convert(ifc_files["IFC4"], target, ConversionOptions())
    from ifcusd.schema import plugin_path

    script = (
        "import sys\n"
        "from pxr import Usd\n"
        "stage = Usd.Stage.Open(sys.argv[1])\n"
        "assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('IfcEntityAPI')\n"
        "entities = [p for p in stage.Traverse() if p.HasAPI('IfcEntityAPI')]\n"
        "walls = [p for p in entities if p.GetAttribute('ifc:class').Get() == 'IfcWall']\n"
        "assert len(walls) == 2, walls\n"
        "assert walls[0].HasAPI('IfcPropertySetAPI', 'Pset_WallCommon')\n"
        "definition = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition('IfcEntityAPI')\n"
        "assert 'ifc:globalId' in definition.GetPropertyNames()\n"
        "print('ok')\n"
    )
    env = dict(os.environ, PXR_PLUGINPATH_NAME=str(plugin_path()))
    completed = subprocess.run([sys.executable, "-c", script, str(target)], env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


@pytest.mark.parametrize("name", ["Architecture", "Structural", "example"])
def test_bundled_samples_convert_completely(name, tmp_path) -> None:
    source = SAMPLES / f"{name}.ifc"
    target = tmp_path / f"{name}.usdc"
    result = convert(source, target, ConversionOptions.for_profile("omniverse"))
    assert result.report.complete, result.report.warnings
    validation = validate_usd(target, source=source)
    assert validation.ok, [(i.code, i.message) for i in validation.errors]
    assert validation.stats["missingPropertyValues"] == 0
    assert open_stage(target).GetDefaultPrim()


def test_run_from_checkout_helper(tmp_path) -> None:
    target = tmp_path / "helper.usda"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "convert_ifc_to_usd.py"), str(SAMPLES / "Architecture.ifc"), str(target), "-q"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert target.exists()
