"""Export-only meshes for the three render modifiers in PARK Spline Tools.

Ordinary Curve/GN and Hair Tool collection/export rules remain unchanged.
Nothing is applied to, or saved into, the editable source curve.
"""
import re

import bpy

from . import export_selection, utilities
from ..constants import ToolInfo


RENDER_GROUPS = (
    'PARK Spline · Renderable Spline UV',
    'PARK Spline · Renderable Tube',
    'PARK Spline · Sweep Profile',
)
_prepared = []


def has_enabled_renderer(source):
    """Require a native PARK group and both viewport/render modifier switches."""
    if source.type != 'CURVE':
        return False
    groups = [mod.node_group for mod in source.modifiers
              if mod.type == 'NODES' and mod.node_group is not None]
    # A mixed source must stay with its existing Hair Tool route.
    if any(group.name.startswith('Hair_System_') for group in groups):
        return False
    return any(
        mod.type == 'NODES' and mod.show_viewport and mod.show_render
        and mod.node_group is not None
        and mod.node_group.get('park_spline_version', 0) == 1
        and any(re.fullmatch(re.escape(name) + r'(?:\.\d{3,})?', mod.node_group.name)
                for name in RENDER_GROUPS)
        for mod in source.modifiers
    )


def source_for_export(obj):
    """Keep an explicit source allowlist bound to its original object identity."""
    return next((row['source'] for row in _prepared if row['proxy'] == obj), obj)


def _capture_mesh(source, depsgraph):
    return bpy.data.meshes.new_from_object(
        source.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)


def prepare(properties):
    if _prepared:
        raise RuntimeError('PARK Spline export preparation is already active.')
    collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    if collection is None or not properties.import_meshes:
        return 0
    sources = [obj for obj in collection.objects
               if has_enabled_renderer(obj) and obj.visible_get()
               and export_selection.includes(obj)]
    if not sources:
        return 0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    try:
        for source in sources:
            export_name = utilities.get_asset_name(source.name, properties, lod=True)
            conflict = bpy.data.objects.get(export_name)
            if conflict is not None and conflict != source:
                raise RuntimeError('PARK Spline export name collides with another object: ' + export_name)
            mesh = _capture_mesh(source, depsgraph)
            row = dict(source=source, name=source.name, hidden=source.hide_get(),
                       mesh=mesh, proxy=None)
            _prepared.append(row)
            if mesh is None or not mesh.polygons:
                raise RuntimeError('Enabled PARK Spline renderer produced no mesh faces: ' + source.name)
            proxy = bpy.data.objects.new('__Send2UE_PARK_Spline__', mesh)
            row['proxy'] = proxy
            for key, value in source.items():
                proxy[key] = value
            proxy.parent = source.parent
            proxy.matrix_parent_inverse = source.matrix_parent_inverse.copy()
            proxy.matrix_world = source.matrix_world.copy()
            collection.objects.link(proxy)
            source.name = row['name'] + '__Send2UE_PARK_Source__'
            proxy.name = export_name
            source.hide_set(True)
        bpy.context.view_layer.update()
    except BaseException:
        cleanup()
        raise
    return len(_prepared)


def cleanup():
    while _prepared:
        row = _prepared.pop()
        if row['proxy'] is not None:
            bpy.data.objects.remove(row['proxy'], do_unlink=True)
        mesh = row['mesh']
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        row['source'].name = row['name']
        row['source'].hide_set(row['hidden'])
    bpy.context.view_layer.update()
