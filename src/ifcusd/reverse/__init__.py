"""OpenUSD to IFC: the reverse direction of IfcUSD.

A stage written by IfcUSD carries every IFC identifier, attribute, property
and relationship, so it can be turned back into IFC. Anything added in USD on
top of it (property values, new prims, robot and simulation data such as
physics, semantic labels and time-sampled sensor readings) is written into the
IFC model using IFC's own extension mechanisms. See
[`convert_usd_to_ifc`][ifcusd.reverse.converter.convert_usd_to_ifc].
"""
