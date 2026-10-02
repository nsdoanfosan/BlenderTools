"""Optional receipts for an owner-admitted native Unreal operation.

No phase means the existing manual pipeline. This module never admits a phase,
starts a chat, takes another owner's scope, or stores an owner token in Blender.
"""
import importlib.util
import os
from pathlib import Path
import sys
import time
import uuid

NAMESPACE_KEY = "send2ue_workstation_operation"
RESOURCE = "unreal:MyProject2"
_BRIDGE = None


def load_bridge():
    global _BRIDGE
    root = Path(os.environ.get("WORKSTATION_QUEUE_REPO") or
                Path.home() / "Documents" / "GitHub" / "workstation-queue").resolve()
    path = root / "pipeline_bridge.py"
    if not path.is_file():
        raise RuntimeError("Workstation Queue pipeline bridge is unavailable: " + str(path))
    for name in ("queue_store", "work_phases", "wq_paths"):
        existing = sys.modules.get(name)
        if existing is not None and Path(getattr(existing, "__file__", "")).resolve() != root / (name + ".py"):
            raise RuntimeError("Workstation Queue module belongs to another installation: " + name)
    if _BRIDGE is not None and Path(_BRIDGE.__file__).resolve() == path:
        return _BRIDGE
    spec = importlib.util.spec_from_file_location("_send2ue_workstation_bridge", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(root))
    _BRIDGE = module
    return module


def current_operation(namespace=None):
    if namespace is None:
        import bpy
        namespace = bpy.app.driver_namespace
    return namespace.get(NAMESPACE_KEY)


class NativeOperation:
    def __init__(self, bridge, metadata, namespace):
        self.bridge = bridge
        self.metadata = metadata
        self.namespace = namespace
        self.last_heartbeat = time.monotonic()
        self.closed = False
        self.imported_count = 0

    def check(self):
        if self.closed or not self.bridge.can_execute_handoff(self.metadata):
            raise RuntimeError("The coordinated Unreal operation must stop and reconcile its work phase")

    def require_scopes(self, scopes):
        self.check()
        self.bridge.require_scopes(self.metadata["phase_id"], scopes)

    def heartbeat(self):
        if time.monotonic() - self.last_heartbeat < 45:
            return
        if not self.bridge.heartbeat_handoff(self.metadata):
            raise RuntimeError("The coordinated Unreal operation no longer has its owner execution lease")
        self.last_heartbeat = time.monotonic()

    def detach(self):
        self.closed = True
        if self.namespace.get(NAMESPACE_KEY) is self:
            del self.namespace[NAMESPACE_KEY]

    def complete(self):
        # The caller invokes this only after every native job and final save.
        if not self.bridge.complete_handoff(self.metadata, self.metadata["request_id"], self.metadata["target"]):
            raise RuntimeError("Native completion could not be reconciled with the exact work phase request")
        self.detach()

    def fail(self, note):
        try:
            self.bridge.fail_handoff(self.metadata, str(note)[:2000])
        finally:
            self.detach()


def begin_operation(phase_id, namespace, target, pipeline="send2ue"):
    if namespace.get(NAMESPACE_KEY) is not None:
        raise RuntimeError("Another coordinated Unreal operation is already running in this Blender session")
    if not phase_id:
        return None
    bridge = load_bridge()
    bridge.require_active_phase(phase_id, RESOURCE)
    metadata = bridge.bind_handoff(phase_id, pipeline, str(uuid.uuid4()), target)
    operation = NativeOperation(bridge, metadata, namespace)
    namespace[NAMESPACE_KEY] = operation
    try:
        operation.check()
    except BaseException as error:
        operation.fail(error)
        raise
    return operation


def require_asset_scopes(asset_data, properties=None, namespace=None):
    operation = current_operation(namespace)
    if operation is None or asset_data.get("skip"):
        return
    asset_path = str(asset_data.get("asset_path") or "").split(".", 1)[0]
    if not asset_path.startswith("/Game/"):
        raise RuntimeError("The coordinated import has no exact Unreal asset path")
    scopes = ["asset:" + asset_path]
    if asset_data.get('_asset_type') == 'SkeletalMesh':
        # FBX can create a Skeleton/PhysicsAsset beside the mesh. The native
        # importer determines their names; reserve the actual target folder.
        scopes.append("asset:" + asset_path.rsplit('/', 1)[0])
    for field in ("skeleton_asset_path", "physics_asset_path"):
        value = str(asset_data.get(field) or getattr(properties, 'unreal_' + field, '') or "").split(".", 1)[0]
        if value.startswith("/Game/"):
            scopes.append("asset:" + value)
    # Auto materials can modify shared masters and source-matching textures far
    # outside the mesh folder. Do not pretend its sidecar lists every mutation.
    extensions = getattr(properties, "extensions", None)
    materials = getattr(extensions, "material_pipeline", None)
    if (asset_data.get('_ue_groom_adapter') or asset_data.get("_material_pipeline_json_path") or
            (materials is not None and getattr(materials, "enabled", False))):
        scopes = ["editor"]
    operation.require_scopes(scopes)


def require_preparation_scopes(properties, namespace=None):
    operation = current_operation(namespace)
    if operation is None:
        return
    extensions = getattr(properties, "extensions", None)
    materials = getattr(extensions, "material_pipeline", None)
    groom = getattr(extensions, "ue_groom_adapter", None)
    groom_output = (groom is not None and getattr(groom, "enabled", False)
                    and getattr(groom, "output_mode", "CARDS") in {"GROOM", "BOTH"})
    if (materials is not None and getattr(materials, "enabled", False)) or groom_output:
        operation.require_scopes(["editor"])


def validate_import_result(asset_data, result):
    if asset_data.get("skip"):
        return
    expected = str(asset_data.get("asset_path") or "").split(".", 1)[0].casefold()
    paths = result.get("imported_object_paths", []) if isinstance(result, dict) else result
    paths = paths if isinstance(paths, (list, tuple)) else []
    if not expected or expected not in {str(path).split(".", 1)[0].casefold() for path in paths}:
        raise RuntimeError("Unreal import did not produce the expected asset: " + (expected or "<missing path>"))
