"""Preserve explicit pivot-in-pivot export units as one Unreal Blueprint."""

import copy
from pathlib import Path
import uuid

import bpy
from send2ue.constants import ToolInfo, UnrealTypes
from send2ue.core.extension import ExtensionBase
from send2ue.core.nested_pivots import get_assembly_root, iter_assembly_pivots
from send2ue.dependencies.unreal import run_commands


COMPONENT_KEY = '_nested_pivot_component'
RUN_KEY = '_nested_pivot_run_id'
_RUN_ID = None
PIPELINE_FILE = (Path(__file__).resolve().parent.parent / 'pipeline' / 'ue_pivot_assembly.py').as_posix()


class NestedPivotAssembliesExtension(ExtensionBase):
    # Run after material setup and path/name extensions, including post_import.
    name = 'z_nested_pivot_assemblies'

    def pre_operation(self, properties):
        global _RUN_ID
        _RUN_ID = uuid.uuid4().hex

    def pre_mesh_export(self, asset_data, properties):
        asset_data.pop(COMPONENT_KEY, None)
        asset_data.pop(RUN_KEY, None)
        if asset_data.get('_asset_type') != UnrealTypes.STATIC_MESH:
            return
        if not asset_data.get('_nested_pivot_origin'):
            return
        pivot = bpy.data.objects.get(asset_data.get('empty_object_name', ''))
        export_collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
        root = get_assembly_root(pivot, export_collection)
        if root is None:
            return
        parent = None if pivot == root else pivot.parent
        # Combined FBX already bakes world rotation and scale into its vertices.
        # Its origin is the pivot's world position, even when the ordinary global
        # use_object_origin option is off. Applying pivot rotation again would
        # double-transform the geometry, so only encode the position difference.
        delta = (pivot.matrix_world.translation - parent.matrix_world.translation) if parent else (0, 0, 0)
        transform = properties.blender.export_method.fbx.transform
        units = bpy.context.scene.unit_settings
        unit_scale = units.scale_length if transform.apply_unit_scale and units.system != 'NONE' else 1.0
        unit_cm = 100.0 * transform.global_scale * unit_scale
        if not _RUN_ID:
            raise RuntimeError('Nested pivot export requires a Send2UE operation context.')
        asset_data[RUN_KEY] = _RUN_ID
        asset_data[COMPONENT_KEY] = {
            'root': root.name, 'name': pivot.name,
            'parent': parent.name if parent else None,
            'required_pivots': [p.name for p in iter_assembly_pivots(root, export_collection)],
            'location': [float(delta[0] * unit_cm), float(-delta[1] * unit_cm), float(delta[2] * unit_cm)],
            'rotation': [0.0, 0.0, 0.0, 1.0], 'scale': [1.0, 1.0, 1.0],
        }

    def post_import(self, asset_data, properties):
        component = asset_data.get(COMPONENT_KEY)
        if not component or asset_data.get('skip'):
            return
        if asset_data.get('_asset_type') != UnrealTypes.STATIC_MESH:
            return
        path = asset_data.get('asset_path')
        if not path:
            return
        record = copy.deepcopy(component)
        record['mesh_asset_path'] = str(path).split('.')[0]
        run_id = asset_data.get(RUN_KEY)
        if not run_id:
            raise RuntimeError('Nested pivot import is missing its export run identity.')
        # When building a deferred manifest, run_commands only records these
        # lines. A receipt is acknowledged inside Unreal after the corresponding
        # real import, never while Blender is merely planning the operation.
        run_commands([
            'import importlib.util, sys',
            '_assembly_spec = importlib.util.spec_from_file_location("send2ue_pivot_assembly", ' + repr(PIPELINE_FILE) + ')',
            '_assembly_runtime = importlib.util.module_from_spec(_assembly_spec)',
            'sys.modules[_assembly_spec.name] = _assembly_runtime',
            '_assembly_spec.loader.exec_module(_assembly_runtime)',
            '_assembly_receipts = _assembly_runtime.record_imported_pivot(' + repr(run_id) + ', ' + repr(record) + ')',
            'print("[nested_pivot_assemblies] " + repr(_assembly_receipts))',
        ])
