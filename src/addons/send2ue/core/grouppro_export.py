"""Export-only native Mesh groups for GroupPro's collection-instance Empties.

Blender 5.2 displays generated meshes on Empties, while Send2UE validates and
collects Mesh objects. Keep the editable Empty and all its source collections;
expose the equivalent GroupPro Mesh representation only during an export.
"""

import sys
import bpy

from . import utilities
from ..constants import ToolInfo


_prepared = []


def _provider():
    for name, module in tuple(sys.modules.items()):
        if name.endswith('GroupPro.helpers_group'):
            package = sys.modules.get(name.rsplit('.', 1)[0])
            if getattr(package, '__addon_enabled__', False):
                return module
    return None


def prepare(properties):
    if _prepared:
        raise RuntimeError('GroupPro export preparation is already active.')
    export = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    if export is None or not properties.import_meshes:
        return
    editing = getattr(bpy.context.scene, 'storedGroupSettings', ())
    if editing:
        raise RuntimeError('Close the edited GroupPro groups before sending to Unreal: '
                           + ', '.join(item.currentEmptyName for item in editing))
    sources = [obj for obj in export.all_objects
               if obj.type == 'EMPTY' and obj.instance_collection
               and obj.modifiers.get('GPro_RealizeAndProxy')
               and obj.visible_get()]
    if not sources:
        return
    gp = _provider()
    if gp is None:
        raise RuntimeError('Enable GroupPro before exporting its collection-instance groups.')
    try:
        for source in sources:
            name = source.name
            export_name = utilities.get_asset_name(name, properties, lod=True)
            conflict = bpy.data.objects.get(export_name)
            if conflict is not None and conflict != source:
                raise RuntimeError('GroupPro export name collides with another object: ' + export_name)
            state = dict(source=source, name=name, hidden=source.hide_get(), proxy=None)
            _prepared.append(state)
            use_proxy, proxy_collection = gp.get_shared_proxy_info(source)
            proxy = gp.create_mesh_group_obj('__Send2UE_Group__', source.instance_collection)
            state['proxy'] = proxy
            gp.set_shared_instance_proxy_group_mod(proxy, use_proxy, proxy_collection)
            group_mod = gp.get_mesh_group_mod(proxy)
            group_mod.properties.inputs[gp.realize_inst_input]['value'] = gp.use_realize_instances(source)
            # The Mesh group already supplies collection geometry. Copying the
            # Empty's explicit source would overwrite that native input again.
            gp.copy_modifiers(source, proxy, ignore_by_name=[
                gp.gp_group_mesh_inst_node_gr, gp.gp_empty_inst_node_gr,
                'GPro Collection Source',
            ])
            for key, value in source.items():
                proxy[key] = value
            proxy.parent = source.parent
            proxy.matrix_parent_inverse = source.matrix_parent_inverse.copy()
            proxy.matrix_world = source.matrix_world.copy()
            proxy.hide_render = source.hide_render
            for collection in source.users_collection:
                collection.objects.link(proxy)
            source.name = name + '__Send2UE_Source__'
            proxy.name = export_name
            source.hide_set(True)
        bpy.context.view_layer.update()
    except BaseException:
        cleanup()
        raise


def cleanup():
    if not _prepared:
        return
    while _prepared:
        state = _prepared.pop()
        proxy = state['proxy']
        if proxy is not None:
            bpy.data.objects.remove(proxy, do_unlink=True)
        source = state['source']
        source.name = state['name']
        source.hide_set(state['hidden'])
    bpy.context.view_layer.update()
