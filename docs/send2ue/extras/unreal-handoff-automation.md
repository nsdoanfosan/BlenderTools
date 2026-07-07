# Unreal Handoff Automation

This project has a few Send to Unreal handoff rules that are applied automatically during export and post-import. They are intentionally narrow so artist-authored Blender data can drive the handoff without creating replacement Unreal assets unexpectedly.

## Blender export preparation

### Hidden export armatures

Armatures that are directly placed in the `Export` collection are temporarily made visible while Send to Unreal prepares the export. Their original viewport visibility is restored during cleanup.

This keeps skeletal mesh exports stable when an artist hides the rig in Blender for viewport work. The export still sees the intended armature, but the Blender scene is returned to the artist's previous hidden/visible state afterward.

## Unreal post-import material setup

### JSON sidecar contract

The Blender material JSON sidecar is the source of truth for slot names, slot indexes, material presets, target material instance names, and whether a missing material instance may be created.

The Unreal post-import script reads that sidecar and applies the matching material instance to the imported Static Mesh or Skeletal Mesh slot.

### Hair material instances

Hair materials whose Blender material names normalize to `M_HT...` are treated as existing hair material instances, not as new AssetSurface materials.

For these entries the JSON writer emits:

* `master_preset: "hair"`
* `material_instance_name: "MI_HT..."`
* `create_if_missing: false`
* no texture entries
* no layer entries

The Unreal post-import step then looks for the existing legacy hair material instance set using the normalized hair base name, including common variants such as:

* `HT...`
* `HT..._Inst`
* `HT..._LWHQ_Inst`

When a matching source `MaterialInstanceConstant` is found outside `/Game/Material/AssetSurface/`, it is moved into the managed hair MI folder:

```text
/Game/Material/AssetSurface/MI/Hair/MI_HT...
```

If a wrong generated target already exists there, it is deleted before the legacy hair instance is moved into place. If the correct target already exists and no legacy source remains, the existing target is reused.

If no legacy source and no target can be found, the slot is skipped with a warning. The script does not create a fresh hair material instance in that case.

### Hair non-goals

Hair handoff deliberately does not:

* create a new material instance from the AssetSurface master
* import or assign texture maps from Blender for hair entries
* assign AssetSurface material layers
* route hair through the translucent/glass material branch
* hard-code one exact source asset path

The source lookup is Asset Registry based, class-filtered to `MaterialInstanceConstant`, and excludes existing AssetSurface paths so the migration prefers the original hair material set.

### Regular AssetSurface materials

Non-hair materials continue through the regular sidecar-driven AssetSurface flow:

* resolve or create the configured material instance when allowed
* import textures with the expected compression and virtual texture settings
* assign flat parameters or material layer instances based on the selected preset
* write material assignments back to the imported mesh slots

