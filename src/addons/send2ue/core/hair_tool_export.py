# Copyright Epic Games, Inc. All Rights Reserved.

import bpy


STATE_KEY = 'send2ue_hair_tool_export_state'
SOURCE_NAME_PROPERTY = '_send2ue_hair_tool_source_name'
TEMP_PROPERTY = '_send2ue_hair_tool_temp'
RSAO_NAME = 'RSAO'


def is_hair_tool_object(scene_object):
    """Return whether an object is a live Hair Tool geometry-nodes system."""
    if not scene_object or scene_object.type != 'CURVES':
        return False

    node_group_names = {
        modifier.node_group.name
        for modifier in scene_object.modifiers
        if modifier.type == 'NODES' and modifier.node_group
    }
    return (
        'Hair_System_Setup' in node_group_names
        and any(name.startswith('Hair_System_Profile') for name in node_group_names)
    )


def is_prepared_source(scene_object):
    state = bpy.app.driver_namespace.get(STATE_KEY, {})
    return scene_object.name in state.get('source_names', set())


def _get_armature(scene_object):
    for modifier in scene_object.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object and modifier.object.type == 'ARMATURE':
            return modifier.object

    parent = scene_object.parent
    while parent:
        if parent.type == 'ARMATURE':
            return parent
        parent = parent.parent
    return None


def _get_head_bone_name(armature_object):
    deform_bones = [bone for bone in armature_object.data.bones if bone.use_deform]

    for bone in deform_bones:
        if bone.name.casefold() == 'head':
            return bone.name
    for bone in deform_bones:
        if bone.name.casefold().endswith('_head'):
            return bone.name
    for bone in deform_bones:
        if 'head' in bone.name.casefold():
            return bone.name
    if len(deform_bones) == 1:
        return deform_bones[0].name

    raise RuntimeError(
        f'Unable to find a head bone on armature "{armature_object.name}". '
        'Expected "head", a name ending in "_Head", or a single deform bone.'
    )


def _attribute_scalar(mesh, attribute, loop_index):
    if not attribute:
        return 0.0

    loop = mesh.loops[loop_index]
    if attribute.domain == 'POINT':
        data_index = loop.vertex_index
    elif attribute.domain == 'CORNER':
        data_index = loop_index
    elif attribute.domain == 'FACE':
        data_index = loop.polygon_index
    else:
        return 0.0

    item = attribute.data[data_index]
    if hasattr(item, 'value'):
        return float(item.value)
    if hasattr(item, 'color'):
        return float(item.color[0])
    if hasattr(item, 'vector'):
        return float(item.vector[0])
    return 0.0


def _pack_rsao(mesh):
    random_attribute = mesh.attributes.get('Random')
    system_color_attribute = mesh.attributes.get('SystemColor')
    ao_attribute = mesh.attributes.get('AO')

    existing_rsao = mesh.attributes.get(RSAO_NAME)
    if existing_rsao:
        mesh.attributes.remove(existing_rsao)

    rsao = mesh.color_attributes.new(
        name=RSAO_NAME,
        type='BYTE_COLOR',
        domain='CORNER',
    )
    for loop_index, color_item in enumerate(rsao.data):
        color_item.color = (
            _attribute_scalar(mesh, random_attribute, loop_index),
            _attribute_scalar(mesh, system_color_attribute, loop_index),
            _attribute_scalar(mesh, ao_attribute, loop_index),
            1.0,
        )

    for color_attribute in list(mesh.color_attributes):
        if color_attribute.name != RSAO_NAME:
            mesh.color_attributes.remove(color_attribute)

    mesh.color_attributes.active_color = mesh.color_attributes[RSAO_NAME]
    mesh.color_attributes.render_color_index = mesh.color_attributes.find(RSAO_NAME)


def _write_uv_layer(mesh, source_attribute_name, target_uv_name):
    source_attribute = mesh.attributes.get(source_attribute_name)
    if (
        not source_attribute
        or source_attribute.domain != 'CORNER'
        or source_attribute.data_type not in {'FLOAT_VECTOR', 'FLOAT2'}
    ):
        return False

    existing_target = mesh.attributes.get(target_uv_name)
    if existing_target and existing_target != source_attribute:
        mesh.attributes.remove(existing_target)

    uv_layer = mesh.uv_layers.get(target_uv_name)
    if not uv_layer:
        uv_layer = mesh.uv_layers.new(name=target_uv_name)

    for loop_index, uv_item in enumerate(uv_layer.data):
        source_item = source_attribute.data[loop_index]
        source_vector = source_item.vector
        uv_item.uv = (source_vector[0], source_vector[1])
    return True


def _write_hair_tool_uvs(mesh):
    if not _write_uv_layer(mesh, 'UVMapGN', 'UVMap'):
        raise RuntimeError(
            f'Hair Tool mesh "{mesh.name}" has no usable UVMapGN corner attribute.'
        )
    _write_uv_layer(mesh, 'UVHelperGN', 'HairTool_UV')
    mesh.uv_layers.active = mesh.uv_layers.get('UVMap')
    mesh.uv_layers['UVMap'].active_render = True


def _evaluated_mesh_objects(source_object, state):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    created_objects = []

    for instance in depsgraph.object_instances:
        parent = instance.parent
        original_object = instance.object.original
        original_parent = parent.original if parent else None
        if instance.object.type != 'MESH':
            continue
        if original_object != source_object and original_parent != source_object:
            continue

        instance_matrix = instance.matrix_world.copy()
        mesh = bpy.data.meshes.new_from_object(instance.object)
        _write_hair_tool_uvs(mesh)
        _pack_rsao(mesh)

        mesh_object = bpy.data.objects.new(
            f'__S2U_HAIR_PART_{source_object.name}',
            mesh,
        )
        bpy.context.scene.collection.objects.link(mesh_object)
        mesh_object.matrix_world = instance_matrix
        created_objects.append(mesh_object)
        state['temporary_object_names'].add(mesh_object.name)

    return created_objects


def _join_objects(objects):
    if not objects:
        return None
    if len(objects) == 1:
        return objects[0]

    bpy.ops.object.select_all(action='DESELECT')
    for scene_object in objects:
        scene_object.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return objects[0]


def _link_to_export_collection(scene_object, export_collection):
    for collection in list(scene_object.users_collection):
        collection.objects.unlink(scene_object)
    export_collection.objects.link(scene_object)


def prepare():
    """Create export-only mesh copies for Hair Tool systems in the Export collection."""
    cleanup()

    export_collection = bpy.data.collections.get('Export')
    if not export_collection:
        return

    source_objects = [
        scene_object
        for scene_object in export_collection.all_objects
        if is_hair_tool_object(scene_object)
    ]
    state = {
        'source_names': {scene_object.name for scene_object in source_objects},
        'temporary_object_names': set(),
    }
    bpy.app.driver_namespace[STATE_KEY] = state

    try:
        for source_object in source_objects:
            parts = _evaluated_mesh_objects(source_object, state)
            if not parts:
                raise RuntimeError(
                    f'Hair Tool object "{source_object.name}" produced no evaluated mesh geometry.'
                )

            temporary_object = _join_objects(parts)
            temporary_object.name = f'{source_object.name}__S2U_HAIR'
            temporary_object.data.name = f'{source_object.data.name}__S2U_HAIR'
            temporary_object[SOURCE_NAME_PROPERTY] = source_object.name
            temporary_object[TEMP_PROPERTY] = True
            _link_to_export_collection(temporary_object, export_collection)
            state['temporary_object_names'].add(temporary_object.name)

            armature_object = _get_armature(source_object)
            if armature_object:
                head_bone_name = _get_head_bone_name(armature_object)
                vertex_group = temporary_object.vertex_groups.new(name=head_bone_name)
                vertex_group.add(
                    range(len(temporary_object.data.vertices)),
                    1.0,
                    'REPLACE',
                )
                armature_modifier = temporary_object.modifiers.new(
                    name='Armature',
                    type='ARMATURE',
                )
                armature_modifier.object = armature_object

    except Exception:
        cleanup()
        raise


def cleanup():
    """Remove temporary Hair Tool export meshes without touching source systems."""
    state = bpy.app.driver_namespace.pop(STATE_KEY, None)
    if not state:
        return

    for object_name in state.get('temporary_object_names', set()):
        scene_object = bpy.data.objects.get(object_name)
        if not scene_object:
            continue
        mesh = scene_object.data if scene_object.type == 'MESH' else None
        bpy.data.objects.remove(scene_object, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def get_asset_source_name(mesh_object):
    return mesh_object.get(SOURCE_NAME_PROPERTY, mesh_object.name)
