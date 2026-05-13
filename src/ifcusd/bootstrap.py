from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import subprocess
import sys


@dataclass(frozen=True)
class RuntimeDependency:
    import_name: str
    package_spec: str
    label: str


class DependencyInstallError(RuntimeError):
    """Raised when automatic runtime dependency installation fails."""


RUNTIME_DEPENDENCIES: tuple[RuntimeDependency, ...] = (
    RuntimeDependency("ifcopenshell", "ifcopenshell>=0.8.0", "IfcOpenShell"),
    RuntimeDependency("pxr", "usd-core>=24.8", "OpenUSD Python bindings"),
)


def missing_runtime_dependencies() -> list[RuntimeDependency]:
    return [
        dependency
        for dependency in RUNTIME_DEPENDENCIES
        if importlib.util.find_spec(dependency.import_name) is None
    ]


def ensure_runtime_dependencies(*, auto_install: bool = True, verbose: bool = False) -> None:
    missing = missing_runtime_dependencies()
    if not missing:
        return

    if not auto_install:
        missing_labels = ", ".join(dependency.label for dependency in missing)
        install_specs = " ".join(dependency.package_spec for dependency in missing)
        raise DependencyInstallError(
            f"Missing required runtime dependencies: {missing_labels}. "
            f"Install them with `{sys.executable} -m pip install {install_specs}`."
        )

    package_specs = [dependency.package_spec for dependency in missing]
    print(
        "Installing missing runtime dependencies: "
        + ", ".join(dependency.label for dependency in missing),
        file=sys.stderr,
    )
    command = [sys.executable, "-m", "pip", "install", *package_specs]
    if verbose:
        print("Running: " + " ".join(command), file=sys.stderr)

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise DependencyInstallError(
            "Automatic dependency installation failed. Run this manually: "
            + " ".join(command)
        )

    still_missing = missing_runtime_dependencies()
    if still_missing:
        raise DependencyInstallError(
            "Dependencies were installed but could not be imported: "
            + ", ".join(dependency.label for dependency in still_missing)
        )
