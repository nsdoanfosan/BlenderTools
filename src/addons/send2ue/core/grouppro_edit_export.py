"""Temporarily expose open GroupPro groups without committing their edit session.

Use the provider's link/transform helpers, but do not invoke Close Group: that
operator can delete empty groups or write edited libraries. No mesh data, edit
stack, local view or collection IDs are replaced by this adapter.
"""
import sys
import bpy

_state = None


def prepare(gp):
    global _state
    if _state is not None:
        return
    context = bpy.context
    editing = getattr(context.scene, 'storedGroupSettings', ())
    if not editing:
        return
    provider = sys.modules.get(gp.__package__ + '.group_pro')
    if provider is None:
        raise RuntimeError('GroupPro edit helpers are unavailable.')
    groups = []
    for storage in editing:
        obj = bpy.data.objects.get(storage.currentEmptyName)
        collection = gp.get_group_collection_shared(obj) if obj else None
        if obj is None or collection is None:
            raise RuntimeError('Cannot export a broken GroupPro edit: ' + storage.currentEmptyName)
        groups.append((obj, collection, storage))
    objects = {}
    for obj in bpy.data.objects:
        if not obj.is_editable:
            continue
        objects[obj] = dict(
            parent=obj.parent, parent_type=obj.parent_type, parent_bone=obj.parent_bone,
            inverse=obj.matrix_parent_inverse.copy(), rotation_mode=obj.rotation_mode,
            location=obj.location.copy(), rotation_euler=obj.rotation_euler.copy(),
            rotation_quaternion=obj.rotation_quaternion.copy(),
            rotation_axis_angle=tuple(obj.rotation_axis_angle), scale=obj.scale.copy(),
            hide_viewport=obj.hide_viewport, fake_user=obj.use_fake_user,
            hidden=obj.hide_get() if obj.name in context.view_layer.objects else None,
            selected=obj.select_get() if obj.name in context.view_layer.objects else None,
            flip_cursor=obj.gp_props.flip_props.cursor_mat.copy(),
        )
    collections = {}
    for collection in list(bpy.data.collections) + [s.collection for s in bpy.data.scenes]:
        if collection.is_editable:
            collections[collection] = (set(collection.objects), set(collection.children),
                                       collection.instance_offset.copy())
    _state = dict(objects=objects, collections=collections,
                  created_collections=set(),
                  active=context.view_layer.objects.active,
                  layer=context.view_layer.active_layer_collection.collection,
                  mode=context.mode)
    try:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj, collection, storage in reversed(groups):
            members = list(collection.all_objects)
            before_collections = set(bpy.data.collections)
            try:
                mod_objs, children = provider.link_connected_objects(collection, members)
            finally:
                _state['created_collections'].update(set(bpy.data.collections) - before_collections)
            members.extend(mod_objs)
            members.extend(children)
            for member in members:
                if member.name in context.view_layer.objects and member.hide_get():
                    member.hide_viewport = True
            if storage.isolated_edit:
                provider._restore_group_links(context.scene, obj, collection, storage)
            else:
                # Older saved edit sessions replaced the group under its parent.
                parents = [c for c in bpy.data.collections if collection in c.children[:]]
                for parent in parents:
                    parent.children.unlink(collection)
                    if obj.name not in parent.objects:
                        parent.objects.link(obj)
                if collection in context.scene.collection.children[:]:
                    context.scene.collection.children.unlink(collection)
                    if obj.name not in context.scene.collection.objects:
                        context.scene.collection.objects.link(obj)
            for member in members:
                if member.parent and member.parent not in members:
                    gp.clear_parent(member)
                for child in list(member.children):
                    if child not in members:
                        gp.set_parent(child, obj)
            valid = {collection, *collection.children_recursive}
            for member in collection.all_objects:
                for parent in list(member.users_collection):
                    if parent not in valid:
                        parent.objects.unlink(member)
            collection.instance_offset = (0, 0, 0)
            inverse = storage.edit_matrix.inverted()
            gp.apply_mat_to_inner_coll_objs(collection, inverse)
            inverse.translation = (0, 0, 0)
            for instance in gp.coll_instances_empties(collection):
                if instance != obj:
                    gp.apply_col_mat_inv_transform(instance, inverse)
        context.view_layer.update()
    except BaseException:
        cleanup()
        raise


def cleanup():
    global _state
    state = _state
    if state is None:
        return
    # Restore saved values directly; reopening through an operator would perform
    # another matrix decomposition and accumulate rounding errors on every send.
    for collection, (objects, children, offset) in state['collections'].items():
        for obj in set(collection.objects) - objects:
            collection.objects.unlink(obj)
        for child in set(collection.children) - children:
            collection.children.unlink(child)
    for collection, (objects, children, offset) in state['collections'].items():
        for obj in objects - set(collection.objects):
            collection.objects.link(obj)
        for child in children - set(collection.children):
            collection.children.link(child)
        collection.instance_offset = offset
    for obj, saved in state['objects'].items():
        obj.parent = saved['parent']
        obj.parent_type = saved['parent_type']
        obj.parent_bone = saved['parent_bone']
        obj.matrix_parent_inverse = saved['inverse']
        for attr in ('rotation_mode', 'location', 'rotation_euler',
                     'rotation_quaternion', 'rotation_axis_angle', 'scale', 'hide_viewport'):
            setattr(obj, attr, saved[attr])
        obj.use_fake_user = saved['fake_user']
        obj.gp_props.flip_props.cursor_mat = tuple(
            value for row in saved['flip_cursor'].transposed() for value in row)
    for collection in state['created_collections']:
        bpy.data.collections.remove(collection)
    bpy.context.view_layer.update()
    for obj, saved in state['objects'].items():
        if saved['hidden'] is not None and obj.name in bpy.context.view_layer.objects:
            obj.hide_set(saved['hidden'])
            obj.select_set(saved['selected'])
    bpy.context.view_layer.objects.active = state['active']
    def find(layer):
        if layer.collection == state['layer']:
            return layer
        for child in layer.children:
            found = find(child)
            if found:
                return found
    layer = find(bpy.context.view_layer.layer_collection)
    if layer:
        bpy.context.view_layer.active_layer_collection = layer
    if state['active'] and state['mode'] != bpy.context.mode:
        bpy.ops.object.mode_set(mode='EDIT' if state['mode'].startswith('EDIT') else state['mode'])
    _state = None
