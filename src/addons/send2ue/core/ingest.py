# Copyright Epic Games, Inc. All Rights Reserved.

import bpy
import copy
from . import settings, extension
from ..constants import PathModes, ExtensionTasks, UnrealTypes
from ..dependencies import unreal as unreal_dependency
from ..dependencies.unreal import UnrealRemoteCalls as UnrealCalls
from .utilities import track_progress, get_asset_id
from ..dependencies.rpc.factory import make_remote

UnrealRemoteCalls = make_remote(UnrealCalls)
MANIFEST_SCHEMA_VERSION = 1


def _property_data_for_asset(asset_data, property_data):
    if "_import_materials_and_textures" not in asset_data:
        return property_data

    adjusted = dict(property_data)
    option_data = adjusted.get("import_materials_and_textures")
    if isinstance(option_data, dict):
        option_data = dict(option_data)
    else:
        option_data = {}
    option_data["value"] = bool(asset_data["_import_materials_and_textures"])
    adjusted["import_materials_and_textures"] = option_data
    return adjusted


def build_manifest_items(properties):
    """Serialize the existing extension/import contract for deferred ingest."""
    property_data = settings.get_extra_property_group_data_as_dictionary(
        properties,
        only_key='unreal_type',
    )
    manifest_items = []
    window_properties = bpy.context.window_manager.send2ue

    for asset_id, asset_data in window_properties.asset_data.items():
        window_properties.asset_id = asset_id

        with unreal_dependency.record_commands() as pre_import_commands:
            extension.run_extension_tasks(ExtensionTasks.PRE_IMPORT.value)

        adjusted_property_data = _property_data_for_asset(asset_data, property_data)

        with unreal_dependency.record_commands() as post_import_commands:
            extension.run_extension_tasks(ExtensionTasks.POST_IMPORT.value)

        manifest_items.append({
            'schema_version': MANIFEST_SCHEMA_VERSION,
            'asset_id': asset_id,
            'asset_data': copy.deepcopy(asset_data),
            'property_data': copy.deepcopy(adjusted_property_data),
            'pre_import_commands': copy.deepcopy(pre_import_commands),
            'post_import_commands': copy.deepcopy(post_import_commands),
            'operations': {
                'reset_and_import_lods': bool(asset_data.get('lods')),
                'create_static_mesh_sockets': bool(asset_data.get('sockets')),
            },
        })

    window_properties.asset_id = ''
    return manifest_items


@track_progress(message='Importing asset "{attribute}"...', attribute='file_path')
def import_asset(asset_id, property_data):
    """
    Imports an asset to unreal based on the asset data in the provided dictionary.

    :param str asset_id: The unique id of the asset.
    :param dict property_data: A dictionary representation of the properties.
    """
    # run the pre import extensions
    extension.run_extension_tasks(ExtensionTasks.PRE_IMPORT.value)

    # get the asset data
    asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]

    if not asset_data.get('skip'):
        file_path = asset_data.get('file_path')
        import_result = UnrealRemoteCalls.import_asset(
            file_path,
            asset_data,
            _property_data_for_asset(asset_data, property_data),
        )
        if asset_data.get('_ue_groom_adapter') and isinstance(import_result, dict):
            groom_asset_path = import_result.get('groom_asset_path')
            if groom_asset_path:
                asset_data['asset_path'] = groom_asset_path

        # import fcurves
        if asset_data.get('fcurve_file_path'):
            UnrealRemoteCalls.import_animation_fcurves(
                asset_data.get('asset_path'),
                asset_data.get('fcurve_file_path')
            )

    # run the post import extensions
    extension.run_extension_tasks(ExtensionTasks.POST_IMPORT.value)


@track_progress(message='Creating static mesh sockets for "{attribute}"...', attribute='asset_path')
def create_static_mesh_sockets(asset_id):
    """
    Creates sockets on a static mesh.

    :param str asset_id: The unique id of the asset.
    """
    asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]
    if asset_data.get('skip'):
        return

    UnrealRemoteCalls.set_static_mesh_sockets(
        asset_data.get('asset_path'),
        asset_data
    )


@track_progress(message='Resetting lods for "{attribute}"...', attribute='asset_path')
def reset_lods(asset_id, property_data):
    """
    Removes all lods on the given mesh.

    :param str asset_id: The unique id of the asset.
    :param dict property_data: A dictionary representation of the properties.
    """
    asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]
    asset_path = asset_data.get('asset_path')
    if asset_data.get('skip'):
        return

    if asset_data.get('_asset_type') == UnrealTypes.SKELETAL_MESH:
        UnrealRemoteCalls.reset_skeletal_mesh_lods(asset_path, property_data)
    else:
        UnrealRemoteCalls.reset_static_mesh_lods(asset_path)


@track_progress(message='Importing lods for "{attribute}"...', attribute='asset_path')
def import_lod_files(asset_id):
    """
    Imports lods onto a mesh.

    :param str asset_id: The unique id of the asset.
    """
    asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]
    lods = asset_data.get('lods', {})
    if asset_data.get('skip'):
        return

    for index in range(1, len(lods.keys()) + 1):
        lod_file_path = lods.get(str(index))
        if asset_data.get('_asset_type') == UnrealTypes.SKELETAL_MESH:
            UnrealRemoteCalls.import_skeletal_mesh_lod(asset_data.get('asset_path'), lod_file_path, index)
        else:
            UnrealRemoteCalls.import_static_mesh_lod(asset_data.get('asset_path'), lod_file_path, index)


@track_progress(message='Setting lod build settings for "{attribute}"...', attribute='asset_path')
def set_lod_build_settings(asset_id, property_data):
    """
    Sets the lod build settings.

    :param str asset_id: The unique id of the asset.
    :param dict property_data: A dictionary representation of the properties.
    """
    asset_data = bpy.context.window_manager.send2ue.asset_data[asset_id]
    lods = asset_data.get('lods', {})
    if asset_data.get('skip'):
        return

    for index in range(0, len(lods.keys()) + 1):
        if asset_data.get('_asset_type') == UnrealTypes.SKELETAL_MESH:
            UnrealRemoteCalls.set_skeletal_mesh_lod_build_settings(
                asset_data.get('asset_path'),
                index,
                property_data
            )
        else:
            UnrealRemoteCalls.set_static_mesh_lod_build_settings(
                asset_data.get('asset_path'),
                index,
                property_data
            )


def assets(properties):
    """
    Ingests the assets.

    :param PropertyData properties: A property data instance that contains all property values of the tool.
    """
    if bpy.context.window_manager.send2ue.asset_data:
        property_data = settings.get_extra_property_group_data_as_dictionary(properties, only_key='unreal_type')

        # check path mode to see if exported assets should be imported to unreal
        if properties.path_mode in [
            PathModes.SEND_TO_PROJECT.value,
            PathModes.SEND_TO_DISK_THEN_PROJECT.value
        ]:
            for asset_id, asset_data in bpy.context.window_manager.send2ue.asset_data.items():
                # imports static mesh, skeletal mesh, animation or groom
                import_asset(asset_id, property_data)

                # import lods
                if asset_data.get('lods'):
                    reset_lods(asset_id, property_data)
                    import_lod_files(asset_id)
                    set_lod_build_settings(asset_id, property_data)

                # import sockets
                if asset_data.get('sockets'):
                    create_static_mesh_sockets(asset_id)
