# License and citation

## License

IfcUSD is released under the **MIT License**. You may use it in commercial
and non-commercial work, modify it and redistribute it, as long as the
copyright notice is kept.

??? note "Full license text"
    ```text
    --8<-- "LICENSE"
    ```

IfcUSD depends on [IfcOpenShell](https://ifcopenshell.org/) (LGPL-3.0),
[OpenUSD](https://openusd.org/) (Tomorrow Open Source Technology License /
Apache-2.0 based) and [NumPy](https://numpy.org/) (BSD-3-Clause). Check
their terms when you redistribute a bundled application.

## Citing IfcUSD

If IfcUSD contributes to your research, please cite it. GitHub shows a
*Cite this repository* button generated from `CITATION.cff`:

```yaml title="CITATION.cff"
--8<-- "CITATION.cff"
```

```bibtex title="BibTeX"
@software{ifcusd,
  title   = {IfcUSD: complete and reproducible conversion of IFC building models to OpenUSD},
  author  = {{IfcUSD contributors}},
  year    = {2026},
  version = {1.0.0},
  url     = {https://github.com/isaddiq/ifcusd},
  license = {MIT}
}
```

When you report results, include the provenance recorded in each output file
(`customLayerData.ifcusd`: source SHA-256, IfcUSD, IfcOpenShell and OpenUSD
versions, options), so others can reproduce your files byte for byte.
