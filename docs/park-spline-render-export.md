# PARK Spline render curves

Send2UE accepts a `CURVE` directly linked to `Export` when it has an enabled
PARK Spline Tools `Renderable Spline UV`, `Renderable Tube`, or `Sweep Profile`
Geometry Nodes modifier. Both modifier viewport and render switches must be on.
The group must have its native `park_spline_version=1` marker and canonical name
(Blender numeric duplicate suffixes are accepted). Renaming the modifier is safe.

Only these PARK groups opt in. Ordinary Curve/Geometry Nodes, Hair Tool `CURVES`,
and curves carrying `Hair_System_*` groups retain their existing behavior.
Visibility, directly linked Export membership, and explicit export-unit/source
selection still apply. An enabled PARK renderer with no final mesh faces fails
explicitly instead of silently exporting an empty asset.

The operation captures the evaluated mesh with all data layers, uses its existing
name, world transform, materials, UVs and Empty parent for native FBX export, then
removes the temporary mesh and restores the original name/visibility on success,
failure or cancellation. Source curves and their modifier stacks are not applied
or saved. A persistent `ExportMesh` object is not required for this route.

Existing legacy ExportMesh objects are not automatically removed or migrated.
Link only the authoritative source curve when opting an existing scene into this
route; do not export both the legacy output and its source.
