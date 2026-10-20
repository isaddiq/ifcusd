# API reference

The public API is importable from the top-level package:

```python
from ifcusd import (
    convert, convert_file, IfcToUsdConverter, ConversionResult,
    ConversionOptions, ConversionReport, validate_usd,
    IfcUsdError, InputError, UnsupportedSchemaError, OutputError, GeometryError,
    MissingUsdDependencyError, PROFILES, SCHEMA_VERSION, __version__,
)
```

Modules under `ifcusd.ifc`, `ifcusd.ifcx` and `ifcusd.usd` are internal and
may change between minor releases.

## Converting

::: ifcusd.converter.convert_file

::: ifcusd.converter.IfcToUsdConverter

::: ifcusd.converter.ConversionResult

## Options

::: ifcusd.options.ConversionOptions
    options:
      members:
        - for_profile
        - with_changes
        - to_dict
        - output_meters_per_unit
        - output_scale

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
