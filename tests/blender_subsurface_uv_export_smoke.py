"""Verify the ordinary skeletal FBX path in an isolated background Blender."""

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import addon_utils
import bpy
from io_scene_fbx import parse_fbx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'addons'))
addon_utils.enable('send2ue', default_set=False)
from send2ue.core import export, hair_tool_export, subsurface_uv_export


ASSET = '/Game/Meshes/SubsurfaceTest'
OPTS = subsurface_uv_export.EXPORT_PROPERTY
FIRST = [(0.125, 0.25), (0.5, 0.25), (0.5, 0.75)]
SECOND = [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75)]


def make_mesh(name, rig, *, keep='UVMap', domain='POINT'):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    for uv_name, values in [('UVMap', FIRST), ('RenderUV', SECOND), ('Spare', FIRST)]:
        uv = data.uv_layers.new(name=uv_name, do_init=False)
        for corner, value in zip(uv.uv, values):
            corner.vector = value
    existing = data.color_attributes.new(name='Existing', type='FLOAT_COLOR', domain='POINT')
    for color in existing.data:
        color.color = (0.25, 0.5, 0.75, 1.0)
    if domain:
        color = data.color_attributes.new(name='SubsurfaceMask', type='FLOAT_COLOR', domain=domain)
        for item, blue in zip(color.data, (0.25, 0.5, 1.0)):
            item.color = (0.8, 0.9, blue, 1.0)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.parent = rig
    obj.vertex_groups.new(name='Bone').add([0, 1, 2], 1.0, 'REPLACE')
    obj.modifiers.new('Skin', 'ARMATURE').object = rig
    obj[OPTS] = {
        'asset_path': ASSET, 'keep_uv': keep,
        'color_attribute': 'SubsurfaceMask' if domain else '',
    }
    obj.select_set(True)
    return obj


def find(element, name):
    return next(child for child in element.elems if child.id == name)


def fbx_contents(path):
    root, _ = parse_fbx.parse(str(path))
    objects = find(root, b'Objects').elems
    models = {element.props[0]: element.props[1].partition(b'\0')[0].decode()
              for element in objects if element.id == b'Model'}
    geometries = {element.props[0]: element for element in objects if element.id == b'Geometry'}
    result = {}
    for link in find(root, b'Connections').elems:
        kind, child, parent, *_ = link.props
        if kind != b'OO' or child not in geometries or parent not in models:
            continue
        geometry = geometries[child]
        uvs = {}
        colors = set()
        for layer in geometry.elems:
            if layer.id == b'LayerElementUV':
                uv = find(layer, b'UV').props[0]
                values = [(uv[index * 2], uv[index * 2 + 1])
                          for index in find(layer, b'UVIndex').props[0]]
                uvs[find(layer, b'Name').props[0].decode()] = values
            if layer.id == b'LayerElementColor':
                colors.add(find(layer, b'Name').props[0].decode())
        result[models[parent]] = (uvs, colors)
    assert any(element.id == b'Deformer' and element.props[-1] == b'Cluster' for element in objects)
    return result


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.object.armature_add()
rig = bpy.context.object
point = make_mesh('PointMask', rig, keep='RenderUV')
corner = make_mesh('CornerMask', rig, domain='CORNER')
trim = make_mesh('Trim', rig, domain=None)
collision = make_mesh('UCX_Collision', rig)
unmarked = make_mesh('Unmarked', rig)
del unmarked[OPTS]
originals = {obj: obj.data for obj in (point, corner, trim, collision, unmarked)}
before_mesh_ids = set(bpy.data.meshes)
state = bpy.context.window_manager.send2ue
state.asset_id = 'subsurface-test'
properties = bpy.context.scene.send2ue
properties.skip_animation_export = True


def write(path, asset=ASSET):
    state.asset_data[state.asset_id] = {
        '_asset_type': 'SkeletalMesh', 'asset_path': asset,
        'file_path': str(path), '_mesh_object_name': point.name,
    }
    export.export_file(properties)


def assert_restored():
    assert set(bpy.data.meshes) == before_mesh_ids
    for obj, data in originals.items():
        assert obj.data == data
        assert [layer.name for layer in data.uv_layers] == ['UVMap', 'RenderUV', 'Spare']


with tempfile.TemporaryDirectory(prefix='send2ue-subsurface-test-') as output:
    output = Path(output)
    baseline_path = output / 'other-asset.fbx'
    write(baseline_path, '/Game/Meshes/OtherAsset')
    baseline = fbx_contents(baseline_path)
    assert_restored()

    missing_path = output / 'missing-asset-path.fbx'
    write(missing_path, None)
    assert fbx_contents(missing_path) == baseline
    assert_restored()

    packed_path = output / 'packed.fbx'
    write(packed_path, ASSET.lower())
    packed = fbx_contents(packed_path)
    for name, source in [('PointMask', 'RenderUV'), ('CornerMask', 'UVMap'), ('Trim', 'UVMap')]:
        uvs, colors = packed[name]
        assert list(uvs) == ['UVChannel_1', 'UVChannel_2'], (name, uvs)
        assert uvs['UVChannel_1'] == baseline[name][0][source], (name, uvs)
        expected = [(0.0, 0.0)] * 3 if name == 'Trim' else [(0.25, 0.0), (0.5, 0.0), (1.0, 0.0)]
        assert uvs['UVChannel_2'] == expected, (name, uvs)
        assert colors == {'Existing'}, (name, colors)
    assert packed['UCX_Collision'] == baseline['UCX_Collision']
    assert packed['Unmarked'] == baseline['Unmarked']
    assert_restored()

    unmarked[hair_tool_export.TEMP_PROPERTY] = True
    hair_path = output / 'hair-gate.fbx'
    write(hair_path)
    assert fbx_contents(hair_path) == baseline
    del unmarked[hair_tool_export.TEMP_PROPERTY]
    assert_restored()

    with patch.object(export, 'export_fbx_file', side_effect=RuntimeError('test export failure')):
        try:
            write(output / 'failure.fbx')
        except RuntimeError as error:
            assert str(error) == 'test export failure'
        else:
            raise AssertionError('Expected the export error to propagate.')
    assert_restored()

print('SUBSURFACE_UV_FBX_PASS')
