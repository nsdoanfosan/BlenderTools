"""Consume linked-opening placement intent in the ordinary Send to Unreal flow.

The authoring addon owns linking and live wall cuts. Send2UE owns import and
Blueprint assembly; this extension never exports linked window/door sources.
"""

import copy
import importlib
import json
from pathlib import Path
import sys

from send2ue.constants import UnrealTypes
from send2ue.core.extension import ExtensionBase
from send2ue.dependencies.unreal import run_commands


ASSEMBLY_KEY = '_linked_opening_assembly'
RECEIPT_PREFIX = 'SEND2UE_LINKED_OPENINGS_OK:'
PIPELINE_FILE = (Path(__file__).resolve().parent.parent / 'pipeline' /
                 'ue_linked_opening_assembly.py').as_posix()


def _authoring_api():
    # Do not activate another addon or change ordinary exports when it is absent.
    if 'linked_opening_assembly' not in sys.modules:
        return None
    import bpy
    # Disabling a Blender addon leaves its modules cached but removes its RNA.
    # Guard actual registration so unrelated exports still work after disable.
    if (
        not hasattr(bpy.types.Scene, 'loa_settings')
        or not hasattr(bpy.types.Object, 'loa_settings')
    ):
        return None
    return importlib.import_module('linked_opening_assembly.send2ue_manifest')


class LinkedOpeningAssembliesExtension(ExtensionBase):
    # Asset names, folders, combined selections and material contracts are final.
    name = 'zz_linked_opening_assemblies'

    def filter_objects(self, armature_objects, mesh_objects, hair_objects):
        api = _authoring_api()
        if api is not None and callable(getattr(api, 'filter_objects', None)):
            return api.filter_objects(armature_objects, mesh_objects, hair_objects)
        return armature_objects, mesh_objects, hair_objects

    def pre_mesh_export(self, asset_data, properties):
        asset_data.pop(ASSEMBLY_KEY, None)
        if asset_data.get('_asset_type') != UnrealTypes.STATIC_MESH:
            return
        api = _authoring_api()
        if api is None:
            return
        manifest = api.for_export(asset_data, properties)
        if manifest is None:
            return
        if asset_data.get('_nested_pivot_component'):
            raise RuntimeError('A house export cannot use both nested-pivot and linked-opening Blueprint ownership.')
        # Existing native origin handling subtracts the owning Empty position,
        # preserving the world rotation/scale already baked into FBX vertices.
        asset_data['_nested_pivot_origin'] = True
        asset_data[ASSEMBLY_KEY] = copy.deepcopy(manifest)

    def post_import(self, asset_data, properties):
        manifest = asset_data.get(ASSEMBLY_KEY)
        if not manifest or asset_data.get('skip'):
            return
        if asset_data.get('_asset_type') != UnrealTypes.STATIC_MESH:
            return
        path = asset_data.get('asset_path')
        if not path:
            raise RuntimeError('Linked-opening assembly is missing its imported house mesh path.')
        record = copy.deepcopy(manifest)
        record['root_mesh_asset_path'] = str(path).split('.', 1)[0]
        # run_commands records these for deferred manifests, or executes them
        # only after a real successful import in the normal wm.send2ue queue.
        dependency = sys.modules.get('send2ue.dependencies.unreal')
        recording = bool(getattr(dependency, '_COMMAND_RECORDING_STACK', []))
        response = run_commands([
            'import importlib.util, sys',
            '_loa_spec = importlib.util.spec_from_file_location("send2ue_linked_opening_assembly", ' + repr(PIPELINE_FILE) + ')',
            '_loa_runtime = importlib.util.module_from_spec(_loa_spec)',
            'sys.modules[_loa_spec.name] = _loa_runtime',
            '_loa_spec.loader.exec_module(_loa_runtime)',
            '_loa_receipt = _loa_runtime.apply_assembly(' + repr(record) + ')',
            'import json as _loa_json',
            'print(' + repr(RECEIPT_PREFIX) + ' + _loa_json.dumps(_loa_receipt, sort_keys=True))',
        ])
        if recording:
            return
        # Native run_commands prints remote exceptions instead of raising them.
        # Require our completed receipt so a missing source or rejected ownership
        # is not reported as a successful normal Send2UE operation.
        receipts = []
        for line in str(response or '').splitlines():
            if RECEIPT_PREFIX in line:
                try:
                    receipts.append(json.loads(line.split(RECEIPT_PREFIX, 1)[1]))
                except (TypeError, ValueError):
                    pass
        expected = record['root_mesh_asset_path'].rsplit('/', 1)[0] + '/bc_' + record['root']
        if not any(isinstance(receipt, dict) and receipt.get('verified') is True
                   and receipt.get('blueprint_asset_path') == expected
                   and receipt.get('placement_count') == len(record['placements'])
                   for receipt in receipts):
            raise RuntimeError('Linked-opening Blueprint assembly did not complete: ' + str(response or 'missing completion receipt')[-1500:])
