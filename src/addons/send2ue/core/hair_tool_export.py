# Copyright Epic Games, Inc. All Rights Reserved.

import bpy
from . import armature_modifier_fix, utilities
from ..constants import BlenderTypes, ToolInfo


STATE_KEY = 'send2ue_hair_tool_export_state'
SOURCE_NAME_PROPERTY = '_send2ue_hair_tool_source_name'
TEMP_PROPERTY = '_send2ue_hair_tool_temp'
RSAO_NAME = 'RSAO'


def is_hair_tool_object(scene_object):
    """Return whether an object is a live Hair Tool geometry-nodes system."""
    if not scene_object or scene_object.type not in {'CURVES', 'MESH'}:
        return False

    if any(_is_edit_mesh_modifier(modifier) for modifier in scene_object.modifiers):
        return False

    node_group_names = {
        modifier.node_group.name
        for modifier in scene_object.modifiers
        if modifier.type == 'NODES' and modifier.node_group
    }
    return (
        any(name.startswith('Hair_System_Setup') for name in node_group_names)
        and any(name.startswith('Hair_System_Profile') for name in node_group_names)
    )


def _is_edit_mesh_modifier(modifier):
    modifier_name = str(getattr(modifier, 'name', '') or '').strip().replace(' ', '_').casefold()
    node_group = getattr(modifier, 'node_group', None)
    node_group_name = str(getattr(node_group, 'name', '') or '').strip().replace(' ', '_').casefold()
    return 'edit_mesh' in {modifier_name, node_group_name}


def is_prepared_source(scene_object):
    state = bpy.app.driver_namespace.get(STATE_KEY, {})
    return scene_object.name in state.get('source_names', set())


def _get_hair_tool_input_object(scene_object):
    """Return the upstream Hair Tool object referenced by Hair System Setup."""
    for modifier in scene_object.modifiers:
        if (
            modifier.type != 'NODES'
            or not modifier.node_group
            or not modifier.node_group.name.startswith('Hair_System_Setup')
        ):
            continue
        try:
            input_object = modifier.get('Input_3')
        except (KeyError, TypeError):
            input_object = None
        if isinstance(input_object, bpy.types.Object):
            return input_object
    return None


def _get_armature(scene_object):
    for modifier in scene_object.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object and modifier.object.type == 'ARMATURE':
            return modifier.object

    exported_rig_objects = utilities.get_from_collection(BlenderTypes.SKELETON)
    return armature_modifier_fix.get_top_parent_rig_object(
        scene_object,
        exported_rig_objects,
    )


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


def _loop_to_polygon_indices(mesh):
    loop_to_polygon = [0] * len(mesh.loops)
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            loop_to_polygon[loop_index] = polygon.index
    return loop_to_polygon


def _attribute_scalar(mesh, attribute, loop_index, loop_to_polygon=None):
    if not attribute:
        return 0.0

    loop = mesh.loops[loop_index]
    if attribute.domain == 'POINT':
        data_index = loop.vertex_index
    elif attribute.domain == 'CORNER':
        data_index = loop_index
    elif attribute.domain == 'FACE':
        data_index = loop_to_polygon[loop_index] if loop_to_polygon else 0
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
    loop_to_polygon = _loop_to_polygon_indices(mesh)

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
            _attribute_scalar(mesh, random_attribute, loop_index, loop_to_polygon),
            _attribute_scalar(mesh, system_color_attribute, loop_index, loop_to_polygon),
            _attribute_scalar(mesh, ao_attribute, loop_index, loop_to_polygon),
            1.0,
        )

    for color_attribute in list(mesh.color_attributes):
        if color_attribute.name != RSAO_NAME:
            mesh.color_attributes.remove(color_attribute)

    mesh.color_attributes.active_color = mesh.color_attributes[RSAO_NAME]
    mesh.color_attributes.render_color_index = mesh.color_attributes.find(RSAO_NAME)


def _remove_empty_material_slots(scene_object):
    mesh = scene_object.data
    materials = [slot.material for slot in scene_object.material_slots]
    if not materials or all(material is not None for material in materials):
        return

    valid_materials = []
    old_to_new_index = {}
    fallback_index = None
    for old_index, material in enumerate(materials):
        if material is None:
            continue
        if fallback_index is None:
            fallback_index = old_index
        old_to_new_index[old_index] = len(valid_materials)
        valid_materials.append(material)

    if fallback_index is None:
        return

    fallback_new_index = old_to_new_index[fallback_index]
    for old_index, material in enumerate(materials):
        if material is None:
            old_to_new_index[old_index] = fallback_new_index

    for polygon in mesh.polygons:
        polygon.material_index = old_to_new_index.get(
            polygon.material_index,
            fallback_new_index,
        )

    mesh.materials.clear()
    for material in valid_materials:
        mesh.materials.append(material)


def _evaluate_combined_ao(scene_object, state):
    """Evaluate Hair Tool AO once, after all systems for an asset are joined."""
    node_group = bpy.data.node_groups.get('HT_Mesh_AO')
    if not node_group:
        raise RuntimeError(
            'Hair Tool node group "HT_Mesh_AO" was not found. '
            'Load the Hair Tool AO node group before exporting.'
        )

    modifier = scene_object.modifiers.new(name='__S2U_HAIR_AO', type='NODES')
    modifier.node_group = node_group
    if 'Input_7' in modifier:
        modifier['Input_7'] = 'AO'

    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated_object = scene_object.evaluated_get(depsgraph)
    evaluated_mesh = bpy.data.meshes.new_from_object(evaluated_object)

    old_mesh = scene_object.data
    scene_object.data = evaluated_mesh
    scene_object.modifiers.remove(modifier)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    if not evaluated_mesh.attributes.get('AO'):
        raise RuntimeError(
            f'Hair Tool AO evaluation produced no "AO" attribute for "{scene_object.name}".'
        )

    state['temporary_mesh_names'].add(evaluated_mesh.name)


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
        if not mesh.vertices or not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            continue

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


def _copy_transfer_settings(temporary_object, source_objects):
    transfer_source = None
    shape_keys = False
    weights = False

    for source_object in source_objects:
        shape_keys = shape_keys or bool(
            getattr(source_object, 'ue_unique_transfer_shape_keys', False)
        )
        weights = weights or bool(
            getattr(source_object, 'ue_unique_transfer_weights', False)
        )
        if transfer_source is None and hasattr(source_object, 'vdt_object_props'):
            transfer_source = source_object.vdt_object_props.transfer_source

    if hasattr(temporary_object, 'ue_unique_transfer_shape_keys'):
        temporary_object.ue_unique_transfer_shape_keys = shape_keys
    if hasattr(temporary_object, 'ue_unique_transfer_weights'):
        temporary_object.ue_unique_transfer_weights = weights
    if (
        transfer_source is not None
        and hasattr(temporary_object, 'vdt_object_props')
    ):
        temporary_object.vdt_object_props.transfer_source = transfer_source


def _link_to_export_collection(scene_object, export_collection):
    for collection in list(scene_object.users_collection):
        collection.objects.unlink(scene_object)
    export_collection.objects.link(scene_object)


def _asset_group_key(scene_object):
    """Group a Hair Tool system by its nearest exported Empty ancestor."""
    export_collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    exported_objects = set(export_collection.all_objects) if export_collection else set()

    parent = scene_object.parent
    while parent:
        if parent.type == 'EMPTY' and parent in exported_objects:
            return parent
        parent = parent.parent

    return scene_object.parent or scene_object


def prepare():
    """Create export-only mesh copies for Hair Tool systems in the Export collection."""
    cleanup()

    export_collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    if not export_collection:
        return

    source_candidates = [
        scene_object
        for scene_object in export_collection.all_objects
        if (
            export_collection in scene_object.users_collection
            and scene_object.visible_get()
            and is_hair_tool_object(scene_object)
        )
    ]
    # If an enabled final Hair Tool output consumes another enabled Hair Tool
    # object, export only the downstream output. The upstream Surface/Curve is
    # construction data, not an additional hair result.
    upstream_sources = {
        input_object
        for input_object in (
            _get_hair_tool_input_object(scene_object)
            for scene_object in source_candidates
        )
        if input_object in source_candidates
    }
    source_objects = [
        scene_object
        for scene_object in source_candidates
        if scene_object not in upstream_sources
    ]
    state = {
        'source_names': {scene_object.name for scene_object in source_objects},
        'temporary_object_names': set(),
        'temporary_mesh_names': set(),
    }
    bpy.app.driver_namespace[STATE_KEY] = state

    try:
        grouped_sources = {}
        for source_object in source_objects:
            grouped_sources.setdefault(_asset_group_key(source_object), []).append(source_object)

        for asset_parent, asset_sources in grouped_sources.items():
            parts = []
            for source_object in asset_sources:
                source_parts = _evaluated_mesh_objects(source_object, state)
                parts.extend(source_parts)

            if not parts:
                continue

            # Joining before AO is intentional: nearby cards and all Hair Tool
            # systems in the final asset must occlude each other.
            temporary_object = _join_objects(parts)
            asset_name = asset_parent.name if asset_parent != asset_sources[0] else asset_sources[0].name
            temporary_object.name = f'{asset_name}__S2U_HAIR'
            temporary_object.data.name = f'{asset_name}__S2U_HAIR'
            temporary_object[SOURCE_NAME_PROPERTY] = asset_name
            temporary_object[TEMP_PROPERTY] = True
            _copy_transfer_settings(temporary_object, asset_sources)
            _link_to_export_collection(temporary_object, export_collection)
            state['temporary_object_names'].add(temporary_object.name)

            # Keep the export copy in the same hierarchy as the source asset.
            if asset_parent != asset_sources[0]:
                world_matrix = temporary_object.matrix_world.copy()
                temporary_object.parent = asset_parent
                temporary_object.parent_type = 'OBJECT'
                temporary_object.matrix_parent_inverse = (
                    asset_parent.matrix_world.inverted()
                )
                temporary_object.matrix_world = world_matrix

            _evaluate_combined_ao(temporary_object, state)
            _write_hair_tool_uvs(temporary_object.data)
            _pack_rsao(temporary_object.data)
            _remove_empty_material_slots(temporary_object)

            armatures = {
                armature
                for armature in (_get_armature(source) for source in asset_sources)
                if armature
            }
            if len(armatures) > 1:
                raise RuntimeError(
                    f'Hair Tool asset "{asset_name}" is bound to multiple armatures.'
                )
            armature_object = next(iter(armatures), None)
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

    for mesh_name in state.get('temporary_mesh_names', set()):
        mesh = bpy.data.meshes.get(mesh_name)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def get_asset_source_name(mesh_object):
    return mesh_object.get(SOURCE_NAME_PROPERTY, mesh_object.name)
