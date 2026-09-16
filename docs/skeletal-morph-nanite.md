# Skeletal morphs and Nanite

On this UE 5.8.2 project, the eyelash mesh contained 47 morph targets and received
the blink curves, but rendered its open-eye shape through skeletal Nanite. Switching
only the component to classic skinned rendering restored the blink with the existing
leader-pose connection. No shape-key retransfers or animation changes were needed.

Send2UE now checks actual imported `morph_targets`. Any skeletal mesh with morphs
uses classic rendering. Rigid hair without morphs retains the existing Nanite policy.
This is a conservative deformation-preservation policy for this engine version,
not a mesh-name exception or proof of missing FBX data.

The importer checks known reimport targets before import, then enforces the policy
on every imported skeletal mesh. Legacy FBX bindings can omit `build_nanite`; the
post-import check is authoritative. Failure to apply/save that guard is an import
failure. The material pipeline's `_set_nanite` applies the same rule, so its later
hair/voxel settings cannot turn Nanite back on.

Validated with 128 focused unit tests in the installed worktree, and the actual
Send2UE importer processing the recorded eyelash FBX twice into an isolated UE
asset: 47 morphs and Nanite disabled after both import and material passes. Blender's
loaded dependency module was reloaded without saving the dirty production blend.
This verification replayed the saved FBX; it did not re-export the live Blender scene.

The production eyelash asset now has Nanite disabled, and its Blueprint component
also disallows Nanite. Existing source mesh, weights, morphs and leader wiring remain.
The production map was not saved. Production assets and verification evidence are
tracked separately in the project's blink-validation task and Perforce record.
