"""Disposable numeric-R handoff smoke; never saves a scene or preferences."""
from pathlib import Path
import json
import math
import runpy
import sys
import tempfile

import bpy

assert bpy.app.background
repo = Path(__file__).resolve().parents[1]
# Keep add-on shutdown cleanup away from every live GUI export directory.
_qa_temp = tempfile.TemporaryDirectory(prefix='send2ue-tip-response-worker-')
tempfile.tempdir = _qa_temp.name
# Reuse the verified real GN ownership fixture and existing G/static regressions.
fixture = runpy.run_path(str(repo / 'tests/blender_hair_guide_cloth_smoke.py'))
cloth, hair = fixture['cloth'], fixture['hair']
guide, guided, rig, state = (fixture[key] for key in ('guide', 'guided', 'rig', 'state'))
mesh = guide.data
for datum, value in zip(mesh.attributes['ChaosWeight'].data, (0., .1, .2, .4, .6, .8, 1.)):
    datum.value = value
attribute = mesh.attributes.new('HairPixelDepthOffset', 'BYTE_COLOR', 'POINT')
for datum, value in zip(attribute.data, (.0, .25, .5, .75, 1., .25, .75)):
    # Hair Tool's Store Named Attribute receives a scene-linear grayscale field.
    datum.color = (value, value, value, 1.)
mesh.attributes.new('ChaosDepthMask', 'FLOAT', 'POINT')
original_r = [datum.color[0] for datum in attribute.data]
original_g = [datum.value for datum in mesh.attributes['ChaosWeight'].data]
original_geometry = ([list(vertex.co) for vertex in mesh.vertices], [list(p.vertices) for p in mesh.polygons])
contract = cloth.response_mask_contract({cloth.RESPONSE_MASK_PROPERTY: 'sim_only_R'})
capture = cloth.capture_source(guided, state, response_contract=contract)
assert capture is not None and capture[1]['simulation_enabled']
assert capture[1]['response_mask']['authored_values'] == original_r
for part in capture[0]:
    assert 'HairPixelDepthOffset' not in part.data.attributes
    assert 'ChaosDepthMask' not in part.data.attributes
render = hair._join_objects(capture[0])
hair._pack_rfaos(render.data)
cloth.finish_asset(render, [capture[1]], 'TipResponseSmoke', rig, bpy.context.scene.collection, state)
key = render[cloth.PACKAGE_PROPERTY]
packet = cloth.state()['packages'][key]['packet']
mask = packet['response_mask']
expected_r = [round(value * 255.) / 255. for value in original_r]
assert all(abs(a-b) < 1e-6 for a,b in zip(mask['values'], expected_r))
assert all(abs(color[0] - 1.) < 1e-6 for color in packet['meshes']['render']['rgba_srgb'])
expected_g = [round(value * 255.) / 255. for value in original_g]
assert all(abs(a-b) < 1e-6 for a,b in zip(packet['meshes']['sim']['weights'], expected_g))
sim = next(obj for obj in bpy.data.objects
           if obj.get(cloth.PACKAGE_PROPERTY) == key and obj.get(cloth.ROLE_PROPERTY) == 'sim')
assert [list(vertex.co) for vertex in sim.data.vertices] == original_geometry[0]
before_fbx = [tuple(item.color_srgb) for item in sim.data.color_attributes['RFAOS'].data]
with tempfile.TemporaryDirectory(prefix='send2ue-tip-response-smoke-') as temporary:
    path = str(Path(temporary) / 'TipResponseSmoke.fbx')
    bpy.ops.object.select_all(action='DESELECT')
    sim.select_set(True)
    bpy.context.view_layer.objects.active = sim
    assert bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={'MESH'},
                                    bake_anim=False, add_leaf_bones=False) == {'FINISHED'}
    objects = set(bpy.data.objects)
    assert bpy.ops.import_scene.fbx(filepath=path) == {'FINISHED'}
    imported = [obj for obj in bpy.data.objects if obj not in objects and obj.type == 'MESH']
    assert len(imported) == 1
    actual_mesh = imported[0].data
    colors = actual_mesh.color_attributes['RFAOS']
    for loop, datum in zip(actual_mesh.loops, colors.data):
        index = loop.vertex_index
        expected = (expected_r[index], expected_g[index], 1., 1.)
        assert math.isclose(datum.color_srgb[0], expected[0], abs_tol=1e-6), (index, datum.color_srgb[0], expected[0])
        assert all(math.isclose(a,b,abs_tol=1./255.+1e-6)
                   for a,b in zip(datum.color_srgb,expected)), (index, tuple(datum.color_srgb), expected)
assert [datum.color[0] for datum in mesh.attributes['HairPixelDepthOffset'].data] == original_r
assert [datum.value for datum in mesh.attributes['ChaosWeight'].data] == original_g
assert ([list(vertex.co) for vertex in mesh.vertices], [list(p.vertices) for p in mesh.polygons]) == original_geometry
assert 'ChaosDepthMask' in mesh.attributes
# A response-enabled unit may also contain entirely static physical sources.
# They still render normally, with simulation-only R kept neutral on the copy.
try:
    for datum in mesh.attributes['ChaosWeight'].data:
        datum.value = 0.
    bpy.context.view_layer.update()
    static_capture = cloth.capture_source(guided, state, response_contract=contract)
    assert static_capture[1]['simulation_enabled'] is False
    assert 'response_mask' not in static_capture[1]
    for part in static_capture[0]:
        assert 'HairPixelDepthOffset' not in part.data.attributes
        assert 'ChaosDepthMask' not in part.data.attributes
        hair._pack_rfaos(part.data)
        assert all(abs(item.color_srgb[0] - 1.) < 1e-6 for item in part.data.color_attributes['RFAOS'].data)
finally:
    for datum, value in zip(mesh.attributes['ChaosWeight'].data, original_g):
        datum.value = value
    bpy.context.view_layer.update()
assert [datum.color[0] for datum in mesh.attributes['HairPixelDepthOffset'].data] == original_r
print('HAIR_TIP_RESPONSE_SMOKE ' + json.dumps(dict(
    evaluated_byte_color_r=original_r, packed_sim_r=mask['values'],
    render_r_neutral_one=True, g_preserved=True, geometry_preserved=True,
    original_attributes_preserved=True, actual_fbx_roundtrip=True,
    mixed_static_sources_render_neutral=True)), flush=True)
