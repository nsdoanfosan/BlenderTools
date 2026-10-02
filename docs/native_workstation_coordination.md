# Optional native Workstation Queue coordination

Send2UE retains its manual workflow. Agents can attach an already admitted heavy
`unreal:MyProject2` work phase through the hidden operator argument:

```python
bpy.ops.wm.send2ue('EXEC_DEFAULT', workstation_phase_id=phase_id)
# INVOKE_DEFAULT runs the same contract through Blender's native modal queue.
```

The addon does not create/admit phases or start chat work. The supplied phase must
have its current owner's execution lease and exclusive mutation scopes. Automatic
material processing requires exclusive `editor` before preparation because it can
modify shared masters and source-matching textures outside the mesh folder. Without
that processing, each import checks its actual mesh, Skeleton/PhysicsAsset targets;
generated skeletal dependencies require the mesh's target folder. Groom adapters
require `editor` because their material creation has additional targets.

`WORKSTATION_QUEUE_REPO` selects the repository containing `pipeline_bridge.py`.
Its default is `~/Documents/GitHub/workstation-queue`. Conflicting loaded queue
modules from another installation are rejected. UUID request metadata stays in
Blender's driver namespace for this operation and contains no owner token.

Completion requires native import paths and all queued jobs plus final dependency
saves. Empty validations, early `FINISHED`, cancellation, remote exceptions and
timeouts never supply completion. Coordinated remote Python commands return exact
structured execution receipts; uncertainty retains a recovery reservation. Modal
boundaries emit a quiet heartbeat at most every 45 seconds, without chat messages.
The addon does not force an editor stop after a timeout.

Ordinary static/skeletal imports also reject an empty or wrong returned import path,
including manual mode: an older existing asset is not proof this import succeeded.
Animation/groom manual contracts are otherwise unchanged.

Tests use fake Blender/remote modules and never register addons or alter preferences:

```text
python -m unittest discover -s tests -p test_native_coordination.py -v
python -m unittest discover -s tests -p test_send2ue_remote_execution.py -v
```
