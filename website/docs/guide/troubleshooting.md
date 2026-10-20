# Troubleshooting

## Installation

??? question "`ModuleNotFoundError: No module named 'pxr'` or `'ifcopenshell'`"
    The runtime dependencies are missing from the active environment:

    ```bash
    python -m pip install "usd-core>=24.8" "ifcopenshell>=0.8.0" numpy
    ```

    or let IfcUSD install them once: `ifcusd convert ... --install-deps`. Make
    sure the `python`/`ifcusd` you run belongs to the environment you
    installed into (`python -m ifcusd --version`).

??? question "`ifcusd` is not recognized as a command"
    The `Scripts` (Windows) or `bin` (macOS/Linux) folder of your environment
    is not on `PATH`. Activate the virtual environment, or run
    `python -m ifcusd …` instead.

??? question "Which Python versions work?"
    Python 3.10 – 3.14 (64-bit). Use a version for which IfcOpenShell and
    `usd-core` publish wheels on your platform; `pip` reports an error
    otherwise.

## Converting

??? question "`IFC schema 'IFC4X1' is not supported`"
    By default IfcUSD reads unknown schema identifiers with the closest
    supported schema and reports anything that schema cannot hold. You only
    see this error with `--no-schema-fallback`, or for identifiers with no
    related schema (e.g. non-IFC STEP files). Check the file header with
    `ifcusd info model.ifc`.

??? question "Some elements have no geometry"
    Run with `-v`, or look at `products_without_geometry` in the report. The
    usual causes are:

    * geometry IfcOpenShell cannot tessellate (some IFC4X3 alignment curves,
      invalid B-reps in the source);
    * curve-only representations with `--no-curves` or the Unity profile;
    * spaces with `--spaces skip` (Unity profile) or openings, which are left
      out unless `--openings` is set.

    The elements' IFC data is written in every case.

??? question "The model is 1,000× too small or too large"
    IfcUSD writes geometry in stage units and sets `metersPerUnit` to match
    (1.0 by default), whatever the IFC project unit. If a viewer still shows
    the wrong size, it is ignoring `metersPerUnit`. Convert with the unit it
    expects, for example `--meters-per-unit 0.01` for a centimetre-based
    scene. Version 0.1 had a bug here; re-convert files made with it.

??? question "The model is far from the origin, or jitters when navigating"
    Georeferenced IFC files often place the site at survey coordinates.
    IfcUSD keeps those placements in double precision, but real-time renderers
    work in single precision. Reference the file into a scene and add a
    translation on the referencing prim
    ([recipe](recipes.md#place-a-converted-building-in-a-larger-scene)).

??? question "Conversion of a large model is slow or uses a lot of memory"
    * Tessellation dominates; it uses `--threads` (all cores but one by default).
    * Coarser tessellation helps: `--linear-deflection 0.01`.
    * `--no-curves`, `--spaces skip` and `--no-owner-history` reduce the amount
      of data; `--no-metadata` writes geometry only.
    * Write `.usdc` rather than `.usda` for large models.

??? question "Strange characters in prim names"
    Prim names must be ASCII identifiers in USD, so names are transliterated
    (`Tür` → `Tur`) and other characters become `_`. The original name is
    always in the prim's `displayName` and in `ifc:name`.

## Viewing

??? question "Omniverse or `ifcusd validate` reports that `OmniPBR.mdl` cannot be resolved"
    Expected outside Omniverse: OmniPBR is an MDL material that ships with
    Omniverse. Inside Omniverse it resolves automatically, and
    `UsdPreviewSurface` is used everywhere else. Use the `generic` profile (or
    `--material-model preview`) for files meant for other tools.

??? question "Unity imports nothing"
    `.usdz` files import automatically. For `.usd`, `.usda` and `.usdc`,
    select the file and enable **Import** in the Inspector. The USD Importer
    package (`com.unity.importer.usd`) must be installed.

??? question "Unity: the IFC metadata is not attached"
    Select the **root** GameObject of the imported model before running
    *Tools ▸ IfcUSD ▸ Attach IFC Metadata…*. The command matches objects by
    USD prim path, so convert with the same `--root` and `--naming` options
    that the sidecar was written with.

??? question "Properties show up as `custom` attributes in usdview"
    That is expected: property names are dynamic, so they are custom
    attributes in the schema-defined `ifc:pset`/`ifc:qto` namespaces. Register
    the [USD schema](../reference/usd-schema.md) to get the declared
    attributes recognized.

## Still stuck?

Open an issue at <https://github.com/isaddiq/ifcusd/issues> with the output
of `ifcusd --version`, the command you ran and, if you can, the conversion
report (`--report report.json`).
