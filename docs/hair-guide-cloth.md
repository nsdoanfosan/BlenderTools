# Hair Tool guides to Chaos Cloth

Enable **Hair guides to Chaos Cloth** in Send to Unreal's Export settings. Choose
an existing cloth physics template, body skeletal mesh and physics asset. The
normal Export collection, naming, material handoff and FBX importer remain in use.
The hair meshes must use the body's existing Skeleton import setting.

Each final Hair Tool output is paired with its actual immediate **Source Surface**
generator input. A two-stage system uses its first source; a three-stage system
uses its intermediate source. Hierarchy depth and object naming do not choose the
simulation mesh. A disposable guide copy stamps source mesh island ownership
before the disposable render generator is evaluated. Hair Tool's grid generator
uses its native `src_island_index` when it does not propagate the custom stamp.
Every connected output strand must retain one generating source island.

An evaluated guide with any positive own `ChaosWeight` is exported separately as
`<asset>_GuideSim`, including all its vertices and islands. Zero-weight roots and
zero-weight islands within that active source are preserved. No Remesh, polygon
reduction, profile edits or paint generation occur. The simulation FBX G channel
comes from that guide's own `ChaosWeight`, never the render mesh's colors.

If the entire evaluated source has zero weights, or the attribute is absent, its
complete ordinary render output is retained with no simulation geometry. The
manifest records `status=guide_weights_all_zero`, the actual resolved guide and
search receipt, and `excluded_sim_vertices`. An absent attribute remains distinct
from authored zero through `authored_weight_present=false`. This whole-source
decision happens before byte-color quantization; even a tiny positive authored
weight keeps the entire source. Render colors follow the existing material packing.
Both outputs follow the existing Hair Tool exporter rule of full head-bone binding
to the same complete armature; this feature does not add a skin transfer rule.
The final cloth uses the chosen body's reference skeleton after checking bone
names and parents. This keeps the imported bone indices and weights while avoiding
FBX reference-scale differences when the cloth follows that body in Unreal.

When no guide exists after generator-input, Hair Tool registry and hierarchy
searches, that output remains ordinary skinned render geometry with **no
simulation**. Render-as-simulation fallback is removed. The version 2 packet
records `guide_ids=-1`, exact `render_only_vertex_indices`, and the completed
search receipts in `render_only_sources`. Mixed exports keep the full render
mesh while their simulation FBX contains only actual guides. Native binding
keeps unguided vertices fully skinned with zero effective simulation influence;
it never assigns them to a nearby guide. With no guides anywhere in an export,
`simulation_enabled=false`: no simulation FBX or Cloth build is made, and the
receipt reports `render_only` with no current cloth asset path. The same path is
used when every resolved guide is wholly zero-weight or unpainted. Mixed packages
keep the complete render mesh and bind those static outputs to skinning only,
with no proxy influence from nearby active guides. Previously
generated assets are retained; this exporter does not assign actor components.
An unresolved or unsupported existing guide is not classified as absent.
Optional cloth generation is deferred with a diagnostic while ordinary Send to
Unreal export continues. Old render-as-simulation packets need re-export; they
cannot recreate the removed fallback.

The `.hair_guide_cloth.json` sidecar contains world-space positions in meters,
actual exported triangles, per-vertex generating guide IDs, simulation byte G,
unquantized source paint, render RGBA/UV data, source search receipts, bone rest
matrices and a content hash. The final exported member carries a portable
`_hair_guide_cloth_record`; ordinary or deferred post-import executes
`resources/pipeline/ue_hair_guide_cloth.py:apply_hair_guide_cloth(record)` after all
required FBXs have been imported (render only when simulation is disabled).
Changed geometry, paint, ownership, render payload or
armature state changes the packet hash and requires a new binding bake.

Only the cloth command receives a scoped 900-second response timeout. Existing
global RPC preferences and all normal import settings are preserved. Cloth
failures do not abort the original FBX import or publish an invalid replacement.

Validation: `python tests/test_hair_guide_cloth.py` and
`python tests/test_ue_hair_guide_cloth.py`; Blender registration tests
must use `default_set=False` and never save preferences from factory startup.
