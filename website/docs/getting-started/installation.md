# Installation

IfcUSD is a pure-Python package. Its three runtime dependencies (IfcOpenShell,
OpenUSD's `usd-core` and NumPy) ship binary wheels for Windows, macOS and
Linux, so installation needs no compiler.

## Requirements

| | |
| --- | --- |
| Python | 3.10, 3.11, 3.12, 3.13 or 3.14 (64-bit) |
| IfcOpenShell | 0.8.0 or newer (0.8.4 and 0.9.0 are tested) |
| OpenUSD (`usd-core`) | 24.8 or newer (25.8 and 26.8 are tested) |
| NumPy | 1.24 or newer |

## Install

=== "From GitHub (recommended)"

    ```bash
    python -m pip install --upgrade pip
    python -m pip install "git+https://github.com/isaddiq/ifcusd.git"
    ```

    To pin a release, add a tag: `git+https://github.com/isaddiq/ifcusd.git@v1.0.0`.

=== "From a clone (development)"

    ```bash
    git clone https://github.com/isaddiq/ifcusd.git
    cd ifcusd
    python -m venv .venv
    ```

    Activate the environment:

    ```powershell
    .\.venv\Scripts\Activate.ps1      # Windows PowerShell
    ```

    ```bash
    source .venv/bin/activate         # macOS / Linux
    ```

    Then install in editable mode with the development tools:

    ```bash
    python -m pip install -e ".[dev]"
    ```

    To try the browser UI from the clone without installing IfcUSD at all,
    run `python app.py`. If IfcOpenShell, `usd-core` or NumPy is missing, it
    offers to install it with pip for the Python you ran it with.

=== "From PyPI"

    ```bash
    python -m pip install ifcusd
    ```

    !!! note
        Use this once a release has been published to PyPI by the repository's
        publish workflow. Until then, install from GitHub.

!!! tip "Use a virtual environment"
    `usd-core` and IfcOpenShell are large binary packages. A dedicated virtual
    environment keeps them from conflicting with other software, for example
    a DCC's own copy of OpenUSD.

## Verify the installation

```bash
ifcusd --version
```

```text
ifcusd 1.1.0 (schema 1.1)
```

Convert one of the sample files in the repository (or any IFC file you have):

```bash
ifcusd convert ifc/Architecture.ifc out/Architecture.usda --validate
```

The last lines should read `completed … (complete)` and `OK (0 errors …)`.

Then open the browser UI:

```bash
ifcusd ui
```

Your browser opens [IfcUSD Studio](../guide/studio.md) at
`http://127.0.0.1:8765/`. Press ++ctrl+c++ in the terminal to stop it.

## Inside NVIDIA Omniverse Kit

Kit already contains OpenUSD (`pxr`). Installing `usd-core` into Kit's Python
would create a second, conflicting copy, so install IfcUSD without it:

```bash
python -m pip install --no-deps "git+https://github.com/isaddiq/ifcusd.git"
python -m pip install --no-deps ifcopenshell numpy
```

Use the `python` that ships with Kit, and IfcOpenShell wheels that match its
Python version. Converting outside Kit with the command line and then opening
the `.usd` file in Omniverse avoids this step entirely.

## Optional: register the USD schema

Output files never require the IfcUSD schema plugin. Registering it adds
schema-aware queries (`prim.HasAPI("IfcEntityAPI")`), fallback values and
documentation in OpenUSD tools:

```bash
ifcusd schema                      # prints the plugin folder
```

=== "Windows (PowerShell)"

    ```powershell
    $env:PXR_PLUGINPATH_NAME = (ifcusd schema)
    ```

=== "macOS / Linux"

    ```bash
    export PXR_PLUGINPATH_NAME="$(ifcusd schema)"
    ```

=== "Python"

    ```python
    import ifcusd.schema
    ifcusd.schema.register()   # call before the first stage is opened
    ```

See [USD schema](../reference/usd-schema.md) for details.

## Development tools

The `dev` extra adds pytest and ruff for running the test suite and linters:

```bash
python -m pip install -e ".[dev]"
```

The tools that build this website are kept apart from the package, in
`website/requirements.txt`; see [Contributing](../about/contributing.md#documentation-website).

## Uninstall

```bash
python -m pip uninstall ifcusd
```
