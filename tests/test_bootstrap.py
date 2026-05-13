from __future__ import annotations

from ifcusd import bootstrap


def test_missing_runtime_dependencies_uses_import_specs(monkeypatch) -> None:
    def fake_find_spec(import_name: str):
        return object() if import_name == "ifcopenshell" else None

    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", fake_find_spec)

    missing = bootstrap.missing_runtime_dependencies()

    assert [dependency.import_name for dependency in missing] == ["pxr"]


def test_no_auto_install_reports_pip_command(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "missing_runtime_dependencies",
        lambda: [bootstrap.RuntimeDependency("pxr", "usd-core>=24.8", "OpenUSD")],
    )

    try:
        bootstrap.ensure_runtime_dependencies(auto_install=False)
    except bootstrap.DependencyInstallError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected DependencyInstallError")

    assert "usd-core>=24.8" in message
    assert "pip install" in message
