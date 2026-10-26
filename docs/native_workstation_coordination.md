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

Character callers that still need to bind components and validate motion can pass
`workstation_postprocess_pending=True` with their admitted phase. Successful native
import then returns `send2ue_workstation_import_receipt` in the driver namespace,
with exact metadata and `phase_completed=False`. It detaches the local Blender
context but retains the same owner/phase/request binding. This is not a save or
character-completion receipt. The caller verifies its exact packages, BP and motion,
then calls `complete_handoff` with that same metadata/request/target; failure retains
recovery ownership. The default manual and automatic-completion paths are unchanged.

When postprocessing discovers a correction requiring another native import,
call `coordination.prepare_import_postprocess_retry(receipt,
bpy.app.driver_namespace)` before invoking the operator with the same phase and
target. It accepts only the still-active exact import receipt and retains its
request ID. Completed or lost bindings are rejected. A successful retry still
leaves final character verification and completion with the owner.

SpeedTree Batch can defer native completion when it exports through Send2UE to
disk and performs the authoritative Unreal import/postprocessing later via RPC:

```python
bpy.ops.wm.send2ue('EXEC_DEFAULT', workstation_phase_id=phase_id,
                   workstation_deferred_handoff=json.dumps(metadata))
receipt = bpy.app.driver_namespace[
    'send2ue_workstation_deferred_export_receipt']
```

The caller first binds its admitted phase with the existing queue bridge, using
`pipeline='speedtree_batch_send2ue'`. The JSON carries the exact eight native
binding fields plus `action='send2ue_manifest_export_rpc_ingest'` and
`project='unreal:MyProject2'`. The addon checks this existing binding and each
actual Blender source scope without rebinding it. Multiple providers can share
one binding and a final target. Only native disk export jobs are allowed, and the
receipt contains the successful native job count and exact output file hashes.
Remote post-operation calls are recorded/discarded during local cleanup.

An export receipt detaches only the local Blender context and explicitly leaves
the phase active. The Batch caller must carry the same metadata to Unreal,
require its native import and postprocessing receipts plus every dependency's
durable clean save and runtime verification, and only then call the existing
`complete_handoff(metadata, metadata['request_id'], metadata['target'])`. Errors,
timeouts, missing files, or a stale binding retain recovery ownership; no retry
may replace the request automatically. A no-phase manual export is unchanged.

Tests use fake Blender/remote modules and never register addons or alter preferences:

```text
python -m unittest discover -s tests -p test_native_coordination.py -v
python -m unittest discover -s tests -p test_send2ue_remote_execution.py -v
```
