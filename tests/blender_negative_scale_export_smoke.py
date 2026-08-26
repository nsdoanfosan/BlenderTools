"""Negatively scaled meshes must not arrive inside out in Unreal.

Mirroring by negative scale leaves ``matrix_world`` with a negative determinant.
Blender negates shading normals for those objects so the viewport looks right,
but the FBX exporter writes the negative transform onto the object's node and
leaves the local winding alone. An importer that bakes the node transform into
the vertices - which is what Unreal does - mirrors the positions and reverses
the effective winding.

Two independent properties have to survive that bake:

  winding  - the index-buffer order, which the bake reverses
  normals  - the explicit per-corner normals, which a mirror transforms
             correctly on its own

Checking only the winding is the trap this test exists to close: a plain
``Flip Faces`` fixes the winding and inverts the normals, and a winding-only
assertion passes on that broken output. Verified against a real Unreal 5.8.2
import; see ``export.compensate_negative_scale_winding``.
"""

from pathlib import Path
import sys

import bpy
from mathutils import Vector


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "addons"))

from send2ue.core import export


def clear_scene():
    for scene_object in list(bpy.data.objects):
        bpy.data.objects.remove(scene_object, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def make_cube(name):
    """A closed cube with outward normals, asymmetric in X so a mirror shows."""
    verts = [
        (0.0, -1.0, -1.0), (2.0, -1.0, -1.0), (2.0, 1.0, -1.0), (0.0, 1.0, -1.0),
        (0.0, -1.0, 1.0), (2.0, -1.0, 1.0), (2.0, 1.0, 1.0), (0.0, 1.0, 1.0),
    ]
    faces = [
        (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
        (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3),
    ]
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    scene_object = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(scene_object)
    return scene_object


def distinct_corner_normals(scene_object):
    """How many unique corner normals the exporter would write.

    A flat-shaded cube has 6. If a compensation approach welds them to one per
    vertex it becomes 8, hard edges are lost, and Unreal imports 8 vertices
    instead of 24. Set Mesh Normal in FREE mode does exactly that.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = scene_object.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len({
            tuple(round(value, 4) for value in corner.vector)
            for corner in mesh.corner_normals
        })
    finally:
        evaluated.to_mesh_clear()


def measure(mesh, matrix):
    """Return (winding_outward, shading_outward) means in baked world space.

    ``matrix`` is the node transform an importer would bake in. Positions are
    transformed, the index order is left alone, and explicit corner normals are
    carried through the same rotation/mirror, which mirrors what Unreal does.
    """
    normal_matrix = matrix.to_3x3()
    centre = Vector((0.0, 0.0, 0.0))
    for vertex in mesh.vertices:
        centre += matrix @ vertex.co
    centre /= float(len(mesh.vertices))

    corner_normals = [corner.vector.copy() for corner in mesh.corner_normals]

    winding = 0
    shading = 0
    for polygon in mesh.polygons:
        loop_indices = list(polygon.loop_indices)
        positions = [
            matrix @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
            for loop_index in loop_indices
        ]
        centroid = sum(positions, Vector((0.0, 0.0, 0.0))) / float(len(positions))
        outward = centroid - centre

        geometric = (positions[1] - positions[0]).cross(positions[2] - positions[0])
        winding += 1 if geometric.dot(outward) >= 0.0 else -1

        explicit = Vector((0.0, 0.0, 0.0))
        for loop_index in loop_indices:
            explicit += normal_matrix @ corner_normals[loop_index]
        shading += 1 if explicit.dot(outward) >= 0.0 else -1

    faces = float(len(mesh.polygons))
    return winding / faces, shading / faces


def evaluated_measurement(scene_object):
    """Measure the mesh the exporter would write, with the node transform baked."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = scene_object.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return measure(mesh, scene_object.matrix_world)
    finally:
        evaluated.to_mesh_clear()


def negative_object_scale(name="NegativeObjectScale"):
    scene_object = make_cube(name)
    scene_object.scale = (-1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    return scene_object


def negative_parent_scale():
    parent = bpy.data.objects.new("MirrorParent", None)
    bpy.context.scene.collection.objects.link(parent)
    parent.scale = (-1.0, 1.0, 1.0)
    scene_object = make_cube("ChildOfMirroredEmpty")
    scene_object.parent = parent
    bpy.context.view_layer.update()
    return scene_object


def select_only(scene_object):
    bpy.ops.object.select_all(action="DESELECT")
    scene_object.select_set(True)
    bpy.context.view_layer.objects.active = scene_object


# The reference: no negative scale, nothing to compensate.
clear_scene()
baseline = make_cube("Baseline")
select_only(baseline)
with export.compensate_negative_scale_winding():
    reference = evaluated_measurement(baseline)
REFERENCE_WINDING, REFERENCE_SHADING = reference
# Blender is right-handed, so a correct front face has its winding-derived
# normal pointing outward and the two measures agree here. Unreal reports the
# winding with the opposite sign after the FBX axis conversion; the invariant
# that carries across both is "match the no-negative-scale baseline".
assert REFERENCE_WINDING == 1.0, reference
assert REFERENCE_SHADING == 1.0, reference

for label, setup in (
    ("negative object scale", negative_object_scale),
    ("negative parent scale", negative_parent_scale),
):
    # Without compensation the bake inverts the winding but not the normals.
    clear_scene()
    scene_object = setup()
    assert scene_object.matrix_world.to_3x3().determinant() < 0.0, label
    raw_winding, raw_shading = evaluated_measurement(scene_object)
    assert raw_winding == -REFERENCE_WINDING, (label, raw_winding)
    assert raw_shading == REFERENCE_SHADING, (label, raw_shading)

    # With compensation both match the reference. Asserting the shading value is
    # what distinguishes this from a bare Flip Faces, which would give
    # winding == REFERENCE_WINDING but shading == -REFERENCE_SHADING.
    clear_scene()
    scene_object = setup()
    select_only(scene_object)
    flat_normals_before = distinct_corner_normals(scene_object)
    assert flat_normals_before == 6, (label, flat_normals_before)
    with export.compensate_negative_scale_winding():
        winding, shading = evaluated_measurement(scene_object)
        # Hard edges must survive. Set Mesh Normal in FREE mode reports 8 here.
        assert distinct_corner_normals(scene_object) == flat_normals_before, (
            label,
            "split normals were welded",
            distinct_corner_normals(scene_object),
        )
    assert winding == REFERENCE_WINDING, (label, "winding", winding)
    assert shading == REFERENCE_SHADING, (label, "shading", shading)

# A smooth-shaded mesh keeps its per-vertex normals too.
clear_scene()
smooth_object = make_cube("SmoothNegativeScale")
for polygon in smooth_object.data.polygons:
    polygon.use_smooth = True
smooth_object.data.update()
smooth_object.scale = (-1.0, 1.0, 1.0)
bpy.context.view_layer.update()
select_only(smooth_object)
smooth_before = distinct_corner_normals(smooth_object)
assert smooth_before == 8, smooth_before
with export.compensate_negative_scale_winding():
    assert distinct_corner_normals(smooth_object) == smooth_before, (
        "smooth normals changed",
        distinct_corner_normals(smooth_object),
    )
    winding, shading = evaluated_measurement(smooth_object)
assert winding == REFERENCE_WINDING, winding
assert shading == REFERENCE_SHADING, shading

# A Mirror modifier keeps the determinant positive and is already correct, so
# compensation must not touch it.
clear_scene()
mirrored = make_cube("MirrorModifier")
modifier = mirrored.modifiers.new(name="Mirror", type="MIRROR")
modifier.use_axis = (True, False, False)
bpy.context.view_layer.update()
assert mirrored.matrix_world.to_3x3().determinant() > 0.0
select_only(mirrored)
with export.compensate_negative_scale_winding():
    assert len(mirrored.modifiers) == 1, "compensation must skip positive determinants"
    winding, shading = evaluated_measurement(mirrored)
assert winding == REFERENCE_WINDING, winding
assert shading == REFERENCE_SHADING, shading

# The source object, its mesh data and its modifier stack survive unchanged.
clear_scene()
scene_object = negative_object_scale()
original_corner_normals = [
    tuple(round(value, 6) for value in corner.vector)
    for corner in scene_object.data.corner_normals
]
original_loop_order = [loop.vertex_index for loop in scene_object.data.loops]
original_modifier_count = len(scene_object.modifiers)
original_mesh = scene_object.data
original_mesh_name = original_mesh.name
original_matrix = scene_object.matrix_world.copy()
mesh_count_before = len(bpy.data.meshes)
select_only(scene_object)
with export.compensate_negative_scale_winding():
    # The object points at a temporary datablock, not the original.
    assert scene_object.data is not original_mesh
    assert scene_object.data.name.endswith("__Send2UE_ReversedWinding")
    # The object transform is untouched, so sockets/LOD/collision stay valid.
    assert scene_object.matrix_world == original_matrix
    assert len(scene_object.modifiers) == original_modifier_count
assert scene_object.data is original_mesh
assert scene_object.data.name == original_mesh_name
assert len(bpy.data.meshes) == mesh_count_before
assert scene_object.matrix_world == original_matrix
assert len(scene_object.modifiers) == original_modifier_count
assert [loop.vertex_index for loop in scene_object.data.loops] == original_loop_order
assert [
    tuple(round(value, 6) for value in corner.vector)
    for corner in scene_object.data.corner_normals
] == original_corner_normals

# Temporary state is cleaned up even when the export raises.
clear_scene()
scene_object = negative_object_scale()
select_only(scene_object)
original_mesh = scene_object.data
mesh_count_before = len(bpy.data.meshes)
try:
    with export.compensate_negative_scale_winding():
        raise RuntimeError("export failed")
except RuntimeError:
    pass
assert scene_object.data is original_mesh
assert len(bpy.data.meshes) == mesh_count_before

print("SEND2UE_NEGATIVE_SCALE_EXPORT_SMOKE_OK")
