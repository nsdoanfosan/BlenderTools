"""Exercise native weight transfer for GuideSim on a twelve-bone tail rig.

Run Blender --background --factory-startup --python-exit-code 23 --python this_file.
No production files or preferences are saved.
"""
import json
from pathlib import Path
import sys

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/addons'))
assert bpy.app.background
assert addon_utils.enable('robust-weight-transfer-libigl26', default_set=False)
assert addon_utils.enable('vertex_data_tools', default_set=False)
assert addon_utils.enable('send2ue', default_set=False)
for name in ('ue_unique_transfer_weights', 'ue_unique_transfer_shape_keys'):
    if not hasattr(bpy.types.Object, name):
        setattr(bpy.types.Object, name, bpy.props.BoolProperty(default=False))
from send2ue.core import hair_guide_cloth as cloth, hair_tool_export as hair


def geometry(name, positions, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def fixture(name, transfer):
    bones = ['Tail_%02d' % i for i in range(12)] if transfer else ['Head']
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    previous = None
    for i, bone_name in enumerate(bones):
        bone = data.edit_bones.new(bone_name)
        bone.head, bone.tail = (0, i * .1, 0), (0, (i + 1) * .1, 0)
        bone.parent = previous
        previous = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.select_set(False)
    positions = [(x, y * .1, 0) for y in range(13) for x in (-.025, .025)]
    faces = [(i * 2, i * 2 + 1, i * 2 + 3, i * 2 + 2) for i in range(12)]
    source = geometry(name + '_Source', positions, faces)
    for i, bone_name in enumerate(bones):
        group = source.vertex_groups.new(name=bone_name)
        indices = [j for j in range(len(positions)) if min(j // 2, len(bones)-1) == i]
        group.add(indices, 1.0, 'REPLACE')
    source.modifiers.new('Armature', 'ARMATURE').object = rig
    render = geometry(name + '_Render', positions, faces)
    render.ue_unique_transfer_weights = transfer
    render.vdt_object_props.transfer_source = source
    hair._bind_export_skin(render, rig)
    cloth._int_attribute(render.data, cloth.GUID_ATTRIBUTE, [1] * len(positions))
    triangles = [[a, b, c] for a, b, c, _ in faces] + [[a, c, d] for a, _, c, d in faces]
    capture = {'vertices': positions, 'triangles': triangles,
               'guide_ids': [1] * len(positions), 'weights': [0, 0] + [1] * 24,
               'authored_weights': [0, 0] + [1] * 24, 'parts': [],
               'source': {'guide': source.name, 'status': 'resolved'},
               'simulation_enabled': True}
    state = {'temporary_object_names': set()}
    cloth.begin()
    cloth.finish_asset(render, [capture], name, rig, bpy.context.scene.collection, state)
    package = next(iter(cloth.state()['packages'].values()))
    sim = bpy.data.objects[name + '_GuideSim']
    def weights(obj):
        names = {g.index: g.name for g in obj.vertex_groups}
        return [{names[g.group]: g.weight for g in v.groups if g.weight > 1e-7}
                for v in obj.data.vertices]
    assert weights(sim) == weights(render), 'Simulation did not use the render skin transfer'
    used = {bone for row in weights(sim) for bone in row}
    assert used == set(bones), (used, bones)
    assert len(sim.data.vertices) == len(positions)
    assert len(sim.data.polygons) == len(triangles)
    assert sim.modifiers[0].object == rig
    rule = package['packet']['armature']['export_skin_rule']
    if transfer:
        assert rule['mode'] == 'TRANSFER_WEIGHTS'
        assert rule['transfer_source'] == source.name
        assert 'head_bone' not in rule
    else:
        assert rule['head_bone'] == 'Head'
    return {'case': name, 'bones': len(used), 'vertices': len(positions), 'rule': rule}


print('GUIDE_SIM_SKIN_SMOKE:' + json.dumps([fixture('TailTransfer', True),
                                           fixture('HeadRigid', False)], sort_keys=True))
