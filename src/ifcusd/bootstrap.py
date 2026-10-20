"""Runtime dependency checks.

Dependencies are declared in ``pyproject.toml`` and installed by pip. This
module only verifies that they import, and installs them on request
(``ifcusd ... --install-deps``); it never changes the environment on its own.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeDependency:
    import_name: str
    package_spec: str
    label: str


class DependencyInstallError(RuntimeError):
    """Raised when runtime dependencies are missing or cannot be installed."""


RUNTIME_DEPENDENCIES: tuple[RuntimeDependency, ...] = (
    RuntimeDependency("ifcopenshell", "ifcopenshell>=0.8.0", "IfcOpenShell"),
    RuntimeDependency("pxr", "usd-core>=24.8", "OpenUSD Python bindings"),
    RuntimeDependency("numpy", "numpy>=1.24", "NumPy"),
)


def missing_runtime_dependencies() -> list[RuntimeDependency]:
    return [
        dependency for dependency in RUNTIME_DEPENDENCIES if importlib.util.find_spec(dependency.import_name) is None
    ]


def ensure_runtime_dependencies(*, auto_install: bool = False, verbose: bool = False) -> None:
    missing = missing_runtime_dependencies()
    if not missing:
        return

    install_specs = " ".join(f'"{dependency.package_spec}"' for dependency in missing)
    if not auto_install:
        missing_labels = ", ".join(dependency.label for dependency in missing)
        raise DependencyInstallError(
            f"Missing required runtime dependencies: {missing_labels}. "
            f"Install them with `{sys.executable} -m pip install {install_specs}` "
            "or re-run with --install-deps."
        )

    command = [sys.executable, "-m", "pip", "install", *[d.package_spec for d in missing]]
    print("Installing missing runtime dependencies: " + ", ".join(d.label for d in missing), file=sys.stderr)
    if verbose:
        print("Running: " + " ".join(command), file=sys.stderr)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise DependencyInstallError(
            "Automatic dependency installation failed. Run this manually: " + " ".join(command)
        )

    importlib.invalidate_caches()
    still_missing = missing_runtime_dependencies()
    if still_missing:
        raise DependencyInstallError(
            "Dependencies were installed but could not be imported: "
            + ", ".join(dependency.label for dependency in still_missing)
        )
