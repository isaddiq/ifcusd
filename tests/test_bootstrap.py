from __future__ import annotations

import pytest

from ifcusd import bootstrap


def test_missing_runtime_dependencies_uses_import_specs(monkeypatch) -> None:
    def fake_find_spec(import_name: str):
        return None if import_name == "pxr" else object()

    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", fake_find_spec)

    missing = bootstrap.missing_runtime_dependencies()

    assert [dependency.import_name for dependency in missing] == ["pxr"]


def test_missing_dependencies_are_reported_not_installed_by_default(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "missing_runtime_dependencies",
        lambda: [bootstrap.RuntimeDependency("pxr", "usd-core>=24.8", "OpenUSD")],
    )
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: pytest.fail("pip must not run"))

    with pytest.raises(bootstrap.DependencyInstallError) as error:
        bootstrap.ensure_runtime_dependencies()

    assert "usd-core>=24.8" in str(error.value)
    assert "--install-deps" in str(error.value)
