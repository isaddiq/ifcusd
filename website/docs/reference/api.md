# API reference

The public API is importable from the top-level package:

```python
from ifcusd import (
    convert, convert_file, IfcToUsdConverter, ConversionResult,
    ConversionOptions, ConversionReport, validate_usd,
    convert_usd_to_ifc, UsdToIfcConverter, IfcExportResult,
    IfcExportOptions, IfcExportReport,
    IfcUsdError, InputError, UnsupportedSchemaError, OutputError, GeometryError,
    MissingUsdDependencyError, PROFILES, SCHEMA_VERSION, __version__,
)
```

Modules under `ifcusd.ifc`, `ifcusd.ifcx`, `ifcusd.usd` and `ifcusd.reverse`
(other than the converter and report documented here) are internal and may
change between minor releases.

## Converting

::: ifcusd.converter.convert_file

::: ifcusd.converter.IfcToUsdConverter

::: ifcusd.converter.ConversionResult

## Converting USD to IFC

::: ifcusd.reverse.converter.convert_usd_to_ifc

::: ifcusd.reverse.converter.UsdToIfcConverter

::: ifcusd.reverse.converter.IfcExportResult

## Options

::: ifcusd.options.ConversionOptions
    options:
      members:
        - for_profile
        - with_changes
        - to_dict
        - output_meters_per_unit
        - output_scale

::: ifcusd.options.IfcExportOptions
    options:
      members:
        - with_changes
        - to_dict

## Reports

::: ifcusd.report.ConversionReport
    options:
      members:
        - complete
        - missing_count
        - coverage_totals
        - summary_lines
        - to_dict
        - to_json

::: ifcusd.reverse.report.IfcExportReport
    options:
      members:
        - valid
        - introduced_errors
        - summary_lines
        - to_dict
        - to_json

## Validation

::: ifcusd.validate.validate_usd

::: ifcusd.validate.ValidationReport

::: ifcusd.validate.ValidationIssue

## USD schema

::: ifcusd.schema
    options:
      members:
        - plugin_path
        - register

## Errors

::: ifcusd.errors
