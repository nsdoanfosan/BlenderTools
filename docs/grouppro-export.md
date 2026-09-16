# GroupPro collection instances

Send to Unreal now includes GroupPro's Blender 5.2 Empty groups in mesh exports.
It builds temporary groups using GroupPro's native Mesh + Instance Modifier
representation before mesh validation, material discovery and FBX selection.
Collection geometry, realization/proxy settings, downstream modifiers, transforms
and custom properties are retained. The editable Empties and source collections
are restored after successful exports, cancellation and preparation failures.

GroupPro must be enabled. Open group edits, including nested edits in Mesh Edit
Mode, are temporarily exposed under their original Export parents. The adapter
uses GroupPro's link/transform helpers and restores the saved transforms and
collection links directly after success or failure. It preserves edited mesh
contents, the edit stack, active object, selection and mode; it does not invoke
Close Group's library-writing or empty-group deletion behavior. Export-only names
use Send2UE's normal name cleanup and reject collisions with existing objects.

For older GroupPro Empty groups whose geometry intermittently vanishes in
Blender 5.2, ensure their first modifier is GroupPro's explicit collection source,
pointing to their instance collection. This is separate from export preparation;
the exporter does not rewrite the source modifier stack.

Linked door/window assemblies prefer their native `bc_` Blueprint when it exists.
Single-pivot sources can use StaticMesh instead. When a backup has the same name
as the production asset, set the placement's exact Unreal asset path. Missing
meshes and ambiguous duplicate meshes now produce distinct messages, with the
candidate paths included for duplicates.

Native verification (requires the installed GroupPro addon and a fixture with a
single combined export pivot):

```text
blender --factory-startup --background --python-exit-code 1 --python tests/blender_grouppro_export_smoke.py -- <source.blend> <output-directory> <GroupPro-parent-directory>
```

The smoke test compares generated geometry and material sets, checks rollback
after an injected preparation failure, runs the regular Send2UE disk export, and
reads the FBX back to verify every group is present. It does not save the source
blend or user preferences.

`tests/blender_grouppro_open_export.py -- <source.blend>` additionally checks a
nested mirrored/non-uniform fixture against native Close Group geometry, keeps a
new source-vertex edit, and verifies Edit Mode restoration after injected failure.
