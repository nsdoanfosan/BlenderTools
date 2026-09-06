# Optional prototype identity

Prototype lineage is carried only when the exported objects explicitly provide
`speedtree_cluster_prototype_identity` and its validated member identities, or a
sidecar explicitly supplies the prototype handoff. Existing SpeedTree and ordinary
assets keep their previous material and transfer behavior without these markers.

The handoff binds lineage to the current exported geometry, FBX bytes and JSON.
Subsequent assets in the same operation preserve completed sidecars only after
rechecking source lineage, material intent and the FBX hash. Each new operation
starts with an empty cache. Unreal rejects malformed or stale optional payloads
before changing assets, including when the material is not a tree material.

Unit tests cover parsing, legacy compatibility, preflight and metadata writes.
`tests/blender_prototype_identity_smoke.py` exercises two consecutive native
Send2UE disk exports with a marked parent and an unmarked nested child. It requires
UE Unique Names installed or supplied through `UEUN_REPO`; use a disposable
background Blender with `--factory-startup`, never a production scene.
