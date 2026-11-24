# Copyright Epic Games, Inc. All Rights Reserved.

from array import array
from contextlib import contextmanager

import bpy


EXPORT_PROPERTY = 'send2ue_export_subsurface_mask'
COLLISION_PREFIXES = ('UBX_', 'UCP_', 'USP_', 'UCX_')
SURFACE_UV = 'UVChannel_1'
MASK_UV = 'UVChannel_2'


def _package_path(path):
    return path.partition('.')[0].casefold() if isinstance(path, str) else ''


def _pack_mesh(mesh, keep_uv, color_name):
    if mesh.uv_layers.get(keep_uv) is None:
        raise ValueError(f'UV map "{keep_uv}" was not found.')

    packed = array('f', [0.0]) * (2 * len(mesh.loops))
    if color_name:
        attribute = mesh.color_attributes.get(color_name)
        if attribute is None or attribute.domain not in {'POINT', 'CORNER'}:
            raise ValueError(f'Color attribute "{color_name}" must have the Point or Face Corner domain.')
        if attribute.domain == 'POINT':
            for loop in mesh.loops:
                packed[loop.index * 2] = attribute.data[loop.vertex_index].color[2]
        else:
            for index, color in enumerate(attribute.data):
                packed[index * 2] = color.color[2]
        mesh.color_attributes.remove(attribute)

    for name in tuple(layer.name for layer in mesh.uv_layers):
        if name != keep_uv:
            mesh.uv_layers.remove(mesh.uv_layers[name])
    # The FBX importer recognizes these exact names across combined mesh parts.
    kept_layer = mesh.uv_layers[0]
    kept_layer.name = SURFACE_UV
    mask_layer = mesh.uv_layers.new(name=MASK_UV, do_init=False)
    mask_layer.uv.foreach_set('vector', packed)
    kept_layer.active_render = True
    mesh.uv_layers.active_index = 0
    mesh.update()


@contextmanager
def packed_for_export(scene_objects, *, asset_path):
    """Pack only local mesh parts that opt in for this exact Unreal asset.

    Each part declares the source UV to keep and the color attribute to sample.
    An empty color attribute explicitly assigns zero to non-scattering parts.
    """
    target = _package_path(asset_path)
    if not target:
        yield
        return
    meshes = []
    swaps = []
    try:
        for obj in scene_objects:
            if obj.type != 'MESH' or obj.name.upper().startswith(COLLISION_PREFIXES):
                continue
            settings = obj.get(EXPORT_PROPERTY)
            if not settings or _package_path(settings.get('asset_path', '')) != target:
                continue
            if obj.library is not None:
                raise ValueError(f'{obj.name}: the subsurface mask requires a local export object.')
            original = obj.data
            temporary = original.copy()
            meshes.append(temporary)
            try:
                _pack_mesh(temporary, settings['keep_uv'], settings['color_attribute'])
            except (KeyError, ValueError) as error:
                raise ValueError(f'{obj.name}: cannot export the subsurface mask: {error}') from error
            obj.data = temporary
            swaps.append((obj, original))
        if swaps:
            bpy.context.view_layer.update()
        yield
    finally:
        for obj, original in reversed(swaps):
            obj.data = original
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        if swaps:
            bpy.context.view_layer.update()
