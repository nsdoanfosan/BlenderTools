"""Negatively scaled meshes must not arrive inside out in Unreal.

Mirroring by negative scale leaves ``matrix_world`` with a negative determinant.
Blender negates shading normals for those objects so the viewport looks right,
but the FBX exporter writes the negative transform onto the object's node and
leaves the local winding alone. Any importer that bakes the node transform into
the vertices - which is what Unreal does - reverses the effective winding.

This drives ``export.compensate_negative_scale_winding`` around a real FBX
export, re-imports the file, and bakes the node transform the way an importer
would.
"""

from pathlib import Path
import sys

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "addons"))

from send2ue.core import export


FBX_PATH = Path(bpy.app.tempdir) / "send2ue_negative_scale_smoke.fbx"

# The transform-relevant subset of this add-on's default FBX export settings.
# bake_space_transform stays off, so the object transform lands on the node.
EXPORT_SETTINGS = dict(
    use_selection=True,
    object_types={"ARMATURE", "MESH", "EMPTY"},
    global_scale=1.0,
    apply_scale_options="FBX_SCALE_NONE",
    axis_forward="Y",
    axis_up="Z",
    apply_unit_scale=True,
    bake_space_transform=False,
    mesh_smooth_type="FACE",
    use_mesh_modifiers=True,
    bake_anim=False,
)


def clear_scene():
    for scene_object in list(bpy.data.objects):
        bpy.data.objects.remove(scene_object, do_unlink=True)


def make_quad(name):
    """A single quad wound counter-clockwise in XY, so its normal is +Z."""
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    scene_object = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(scene_object)
    return scene_object


def normal_z_after_transform_bake(scene_object):
    """The face normal once the node transform is baked into the vertices.

    Winding is untouched by the bake, so a negative-determinant matrix inverts
    the effective facing. This is the step that makes the mesh arrive inside out.
    """
    mesh = scene_object.data.copy()
    mesh.transform(scene_object.matrix_world)
    mesh.update()
    normal_z = mesh.polygons[0].normal.z
    bpy.data.meshes.remove(mesh)
    return normal_z


def export_and_bake(scene_object, compensate):
    bpy.ops.object.select_all(action="DESELECT")
    scene_object.select_set(True)
    bpy.context.view_layer.objects.active = scene_object

    if compensate:
        with export.compensate_negative_scale_winding():
            bpy.ops.export_scene.fbx(filepath=str(FBX_PATH), **EXPORT_SETTINGS)
    else:
        bpy.ops.export_scene.fbx(filepath=str(FBX_PATH), **EXPORT_SETTINGS)

    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(FBX_PATH))
    imported = next(
        scene_object for scene_object in bpy.data.objects
        if scene_object.type == "MESH"
    )
    return normal_z_after_transform_bake(imported)


def negative_object_scale():
    scene_object = make_quad("NegativeObjectScale")
    scene_object.scale = (-1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    return scene_object


def negative_parent_scale():
    parent = bpy.data.objects.new("MirrorParent", None)
    bpy.context.scene.collection.objects.link(parent)
    parent.scale = (-1.0, 1.0, 1.0)
    scene_object = make_quad("ChildOfMirroredEmpty")
    scene_object.parent = parent
    bpy.context.view_layer.update()
    return scene_object


def mirror_modifier():
    """A Mirror *modifier* keeps the determinant positive and needs no help."""
    scene_object = make_quad("MirrorModifier")
    modifier = scene_object.modifiers.new(name="Mirror", type="MIRROR")
    modifier.use_axis = (True, False, False)
    bpy.context.view_layer.update()
    return scene_object


# A positive-determinant object must be left exactly as it was.
clear_scene()
positive = make_quad("PositiveScale")
assert positive.matrix_world.to_3x3().determinant() > 0.0
assert export_and_bake(positive, compensate=True) > 0.0

for label, setup in (
    ("negative object scale", negative_object_scale),
    ("negative parent scale", negative_parent_scale),
):
    # Without compensation the bake inverts the face.
    clear_scene()
    scene_object = setup()
    assert scene_object.matrix_world.to_3x3().determinant() < 0.0, label
    uncompensated = export_and_bake(scene_object, compensate=False)
    assert uncompensated < 0.0, (label, uncompensated)

    # With compensation the two negations cancel.
    clear_scene()
    scene_object = setup()
    compensated = export_and_bake(scene_object, compensate=True)
    assert compensated > 0.0, (label, compensated)

# A Mirror modifier already emits correctly wound geometry, so compensation must
# not touch it and must not flip it.
clear_scene()
mirrored = mirror_modifier()
assert mirrored.matrix_world.to_3x3().determinant() > 0.0
assert export_and_bake(mirrored, compensate=True) > 0.0

# The source object, its mesh data and its modifier stack survive unchanged.
clear_scene()
scene_object = negative_object_scale()
original_normal_z = scene_object.data.polygons[0].normal.z
original_modifier_count = len(scene_object.modifiers)
bpy.ops.object.select_all(action="DESELECT")
scene_object.select_set(True)
bpy.context.view_layer.objects.active = scene_object
with export.compensate_negative_scale_winding():
    assert len(scene_object.modifiers) == original_modifier_count + 1
    assert any(
        group.name.startswith("__Send2UE_FlipNegativeScaleWinding__")
        for group in bpy.data.node_groups
    )
assert len(scene_object.modifiers) == original_modifier_count
assert scene_object.data.polygons[0].normal.z == original_normal_z
assert not any(
    group.name.startswith("__Send2UE_FlipNegativeScaleWinding__")
    for group in bpy.data.node_groups
)

# The temporary state is cleaned up even when the export raises.
clear_scene()
scene_object = negative_object_scale()
bpy.ops.object.select_all(action="DESELECT")
scene_object.select_set(True)
bpy.context.view_layer.objects.active = scene_object
try:
    with export.compensate_negative_scale_winding():
        raise RuntimeError("export failed")
except RuntimeError:
    pass
assert len(scene_object.modifiers) == 0
assert not any(
    group.name.startswith("__Send2UE_FlipNegativeScaleWinding__")
    for group in bpy.data.node_groups
)

FBX_PATH.unlink(missing_ok=True)
print("SEND2UE_NEGATIVE_SCALE_EXPORT_SMOKE_OK")
