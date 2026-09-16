"""Run in factory-startup background Blender; never save preferences.

Exports physically identical metre/cm rigs through Send2UE for Unreal bind QA.
Pass an output directory after --. Source coordinates differ by their unit basis;
neither source has an object scale or export-size override.
"""
import sys
from pathlib import Path
import bpy
import addon_utils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'addons'))
addon_utils.enable('send2ue', default_set=False)
from send2ue.core.export import export_fbx_file

out = Path(sys.argv[sys.argv.index('--') + 1])
out.mkdir(parents=True, exist_ok=True)
for label, unit in [('metres', 1.0), ('centimetres', 0.01)]:
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = unit
    data = bpy.data.armatures.new('Rig')
    rig = bpy.data.objects.new('Armature', data)
    scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    root = data.edit_bones.new('root')
    root.head = (0, 0, 0); root.tail = (0, 0, 0.5 / unit)
    child = data.edit_bones.new('child')
    child.parent = root
    child.head = (0, 0, 1.0 / unit); child.tail = (0, 0, 1.5 / unit)
    bpy.ops.object.mode_set(mode='OBJECT')
    md = bpy.data.meshes.new('Triangle')
    md.from_pydata([(0, 0, 1 / unit), (0.1 / unit, 0, 1 / unit), (0, 0, 1.1 / unit)], [], [(0, 1, 2)])
    mesh = bpy.data.objects.new('Triangle', md)
    scene.collection.objects.link(mesh)
    mesh.parent = rig
    mesh.vertex_groups.new(name='child').add([0, 1, 2], 1.0, 'REPLACE')
    mesh.modifiers.new('Rig', 'ARMATURE').object = rig
    mesh.select_set(True)
    second = mesh.copy()
    second.data = md.copy()
    scene.collection.objects.link(second)
    second.select_set(True)
    export_fbx_file(str(out / (label + '.fbx')), dict(
        global_scale=1.0, apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS',
        axis_forward='Y', axis_up='Z', bake_space_transform=False,
        bake_anim=False, add_leaf_bones=False, mesh_smooth_type='FACE',
    ))
    assert tuple(rig.scale) == (1.0, 1.0, 1.0)
    assert tuple(mesh.scale) == (1.0, 1.0, 1.0)
    assert abs(data.bones['child'].head_local.z * unit - 1.0) < 1e-6
    # Every cluster must agree with the bind-pose record for its linked bone.
    from io_scene_fbx import parse_fbx as parse
    import numpy as np
    tree, _ = parse.parse(str(out / (label + '.fbx')))
    objects = next(e for e in tree.elems if e.id == b'Objects')
    connections = next(e for e in tree.elems if e.id == b'Connections')
    clusters = {e.props[0]: e for e in objects.elems if e.id == b'Deformer' and e.props[-1] == b'Cluster'}
    poses = {}
    for pose in (e for e in objects.elems if e.id == b'Pose'):
        for node in (e for e in pose.elems if e.id == b'PoseNode'):
            uid = next(e.props[0] for e in node.elems if e.id == b'Node')
            poses[uid] = next(e.props[0] for e in node.elems if e.id == b'Matrix')
    for conn in connections.elems:
        if conn.props[0] == b'OO' and conn.props[2] in clusters:
            cluster = clusters[conn.props[2]]
            link = next(e.props[0] for e in cluster.elems if e.id == b'TransformLink')
            assert np.allclose(link, poses[conn.props[1]], atol=1e-7)
print('SKELETAL_UNIT_EXPORT_PASS')
