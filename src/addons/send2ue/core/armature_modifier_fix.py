# Copyright Epic Games, Inc. All Rights Reserved.

import bpy
from . import utilities
from ..constants import BlenderTypes, ToolInfo


STATE_KEY = 'send2ue_armature_modifier_fix_state'


def _export_armature_objects():
    export_collection = bpy.data.collections.get(ToolInfo.EXPORT_COLLECTION.value)
    if not export_collection:
        return []
    return [
        scene_object
        for scene_object in export_collection.all_objects
        if (
            scene_object.type == BlenderTypes.SKELETON
            and export_collection in scene_object.users_collection
        )
    ]


def _make_export_armatures_visible(state):
    """
    Temporarily unhides armatures that are explicitly in Export.

    Send to Unreal collects rigs through visible objects, but artists often hide
    armatures in the viewport while working. If a hidden export armature is left
    hidden, the skeletal mesh export can lose the intended rig/skeleton context.
    """
    for rig_object in _export_armature_objects():
        hide_get = rig_object.hide_get()
        hide_viewport = rig_object.hide_viewport
        if not hide_get and not hide_viewport:
            continue
        state['visibility'].append((rig_object.name, hide_get, hide_viewport))
        rig_object.hide_set(False)
        rig_object.hide_viewport = False


def get_top_parent_rig_object(scene_object, rig_objects):
    """
    Gets the highest exported armature in an object's parent chain.

    A mesh that lives "inside" an armature like this but has no armature modifier
    still gets bundled into the skeletal mesh export by ``set_parent_rig_selection``.
    Without skin binding the resulting fbx makes Unreal's importer crash, so we
    need to detect this case and give it a valid binding before export.

    :param object scene_object: A Blender object.
    :param list rig_objects: The armature objects that are being exported.
    :return object: The highest parent armature object, or None.
    """
    rig_objects = set(rig_objects)
    top_rig_object = None
    parent = scene_object.parent
    while parent:
        if parent.type == BlenderTypes.SKELETON and parent in rig_objects:
            top_rig_object = parent
        parent = parent.parent
    return top_rig_object


def _get_bind_bone_name(mesh_object, rig_object):
    """
    Picks the bone to rigidly bind a mesh to when it has no vertex groups of its own.

    Bone-parented meshes follow a specific bone, so we bind to that bone. Object
    parented meshes follow the whole armature, so the root deform bone reproduces
    that rigid attachment.

    :param object mesh_object: A object of type mesh.
    :param object rig_object: The parent armature object.
    :return str: A bone name, or None if the armature has no bones.
    """
    bones = rig_object.data.bones
    if not bones:
        return None

    # a mesh parented directly to a bone follows that bone
    if mesh_object.parent_type == 'BONE' and mesh_object.parent_bone in bones:
        return mesh_object.parent_bone

    deform_bones = [bone for bone in bones if bone.use_deform]
    candidate_bones = deform_bones or list(bones)

    # prefer a root bone (no parent) so the mesh follows the armature as a whole
    for bone in candidate_bones:
        if bone.parent is None:
            return bone.name
    return candidate_bones[0].name


def prepare():
    """
    Adds a temporary armature modifier to any export mesh that is parented to an
    exported armature but is missing one. Meshes with no vertex groups are also
    given a single full-weight group bound to a bone, so the fbx exports with a
    valid skin instead of crashing Unreal on import.

    Unlike the Hair Tool conversion, this only acts when an armature modifier is
    actually missing. Everything it adds is tracked and removed again in cleanup,
    so the user's scene is left untouched.
    """
    cleanup()

    state = {
        'modifiers': [],        # list of (object_name, modifier_name)
        'vertex_groups': [],    # list of (object_name, vertex_group_name)
        'visibility': [],       # list of (object_name, hide_get, hide_viewport)
    }
    bpy.app.driver_namespace[STATE_KEY] = state

    try:
        _make_export_armatures_visible(state)

        mesh_objects = utilities.get_from_collection(BlenderTypes.MESH)
        rig_objects = utilities.get_from_collection(BlenderTypes.SKELETON)
        if not rig_objects:
            return

        for mesh_object in mesh_objects:
            # leave meshes that already have an armature modifier alone
            if any(modifier.type == 'ARMATURE' for modifier in mesh_object.modifiers):
                continue

            rig_object = get_top_parent_rig_object(mesh_object, rig_objects)
            if not rig_object:
                continue

            # if the mesh has no weights at all, bind every vertex to a single bone
            # so the fbx exporter writes valid skin weights
            if not mesh_object.vertex_groups:
                bone_name = _get_bind_bone_name(mesh_object, rig_object)
                # no bone to bind to means there is nothing we can do to make this a
                # valid skin, so leave the mesh untouched rather than add a dangling
                # modifier with no weights
                if not bone_name:
                    continue
                vertex_group = mesh_object.vertex_groups.new(name=bone_name)
                vertex_group.add(
                    range(len(mesh_object.data.vertices)),
                    1.0,
                    'REPLACE',
                )
                state['vertex_groups'].append((mesh_object.name, vertex_group.name))

            armature_modifier = mesh_object.modifiers.new(name='Armature', type='ARMATURE')
            armature_modifier.object = rig_object
            state['modifiers'].append((mesh_object.name, armature_modifier.name))

    except Exception:
        cleanup()
        raise


def cleanup():
    """
    Removes the armature modifiers and vertex groups added by ``prepare``.
    """
    state = bpy.app.driver_namespace.pop(STATE_KEY, None)
    if not state:
        return

    for object_name, modifier_name in state.get('modifiers', []):
        scene_object = bpy.data.objects.get(object_name)
        if not scene_object:
            continue
        modifier = scene_object.modifiers.get(modifier_name)
        if modifier:
            scene_object.modifiers.remove(modifier)

    for object_name, vertex_group_name in state.get('vertex_groups', []):
        scene_object = bpy.data.objects.get(object_name)
        if not scene_object:
            continue
        vertex_group = scene_object.vertex_groups.get(vertex_group_name)
        if vertex_group:
            scene_object.vertex_groups.remove(vertex_group)

    for object_name, hide_get, hide_viewport in state.get('visibility', []):
        scene_object = bpy.data.objects.get(object_name)
        if not scene_object:
            continue
        scene_object.hide_set(hide_get)
        scene_object.hide_viewport = hide_viewport
