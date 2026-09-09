"""Check mixed evaluated/proximity card joins without saving preferences."""
import pathlib
import sys
import bpy
import addon_utils
from mathutils import Matrix

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src' / 'addons'))
for module in ('vertex_data_tools', 'ue_unique_export_names_addon'):
    assert addon_utils.enable(module, default_set=False, persistent=False)
from send2ue.core import hair_tool_export as hair


def mesh_object(name, offset=0.0):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(offset, 0, 0), (offset+1, 0, 0), (offset, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


face = mesh_object('Face')
face.shape_key_add(name='Basis')
move = face.shape_key_add(name='Move')
for point in move.data:
    point.co.z += 0.2
face.vdt_object_props.transfer_source = face
face.ue_unique_transfer_shape_keys = True
ordinary = mesh_object('OrdinaryCards', 0.1)
ordinary.vdt_object_props.transfer_source = face
ordinary.ue_unique_transfer_shape_keys = True
static = mesh_object('StaticCards', 2.0)
state = {'temporary_object_names': set()}
entries = [(source, hair._evaluated_mesh_objects(source, state)) for source in (face, ordinary, static)]
assert not hair._bake_group_shape_keys(entries)
assert all(not part.data.shape_keys for _, parts in entries for part in parts)
face['vdt_evaluated_shape_keys'] = True
assert hair._bake_group_shape_keys(entries)
assert entries[0][1][0].data.shape_keys and entries[1][1][0].data.shape_keys
assert not entries[2][1][0].data.shape_keys
parts = [part for _, group in entries for part in group]
parts[0].matrix_world = Matrix.Translation((0.1, 0.2, 0.3)) @ Matrix.Scale(0.01, 4)
expected = [
    part.matrix_world.to_3x3() @ (p.co-b.co)
    for part in parts[:2]
    for p, b in zip(part.data.shape_keys.key_blocks['Move'].data, part.data.shape_keys.reference_key.data)
]
joined = hair._join_objects(parts)
keys = joined.data.shape_keys.key_blocks
assert len(keys) == 2
deltas = [p.co-b.co for p, b in zip(keys['Move'].data, keys['Basis'].data)]
assert all((a-b).length < 1e-6 for a, b in zip(expected, deltas[:6]))
assert all(delta.length < 1e-6 for delta in deltas[6:])
assert face.ue_unique_transfer_shape_keys and ordinary.ue_unique_transfer_shape_keys
print('HAIR_EVALUATED_SHAPES_SMOKE_PASSED')
