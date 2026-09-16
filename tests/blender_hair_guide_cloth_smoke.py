"""Factory-startup capture policy smoke; never saves a scene or preferences."""
import addon_utils
import bpy
import json
from pathlib import Path
import sys
import tempfile

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'src/addons'))
assert bpy.app.background
assert addon_utils.enable('send2ue', default_set=False)
from send2ue.core import hair_guide_cloth as cloth, hair_tool_export as hair


def passthrough(name):
    group = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    socket = group.interface.new_socket(name='Source Surface', in_out='INPUT', socket_type='NodeSocketObject')
    source, output = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
    group.links.new(source.outputs['Geometry'], output.inputs['Geometry'])
    return group, socket.identifier


mesh = bpy.data.meshes.new('FallbackSource')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)], [], [(0, 1, 2), (1, 3, 2)])
source = bpy.data.objects.new('FallbackRender', mesh)
bpy.context.scene.collection.objects.link(source)
setup, identifier = passthrough('Hair_System_Setup_Smoke')
source.modifiers.new('Setup', 'NODES').node_group = setup
source.modifiers.new('Profile', 'NODES').node_group = passthrough('Hair_System_Profile_Smoke')[0]
original_counts = (len(mesh.vertices), len(mesh.polygons), len(source.modifiers))
cloth.begin()
state = {'temporary_object_names': set(), 'temporary_mesh_names': set()}
first = cloth.capture_source(source, state)
assert first is not None
assert first[1]['source']['status'] == 'no_guide_after_search'
assert not first[1]['simulation_enabled']
assert first[1]['vertices'] == first[1]['triangles'] == first[1]['weights'] == first[1]['parts'] == []
assert len(first[0]) == 1 and len(first[0][0].data.vertices) == 4
assert [d.value for d in first[0][0].data.attributes[cloth.GUID_ATTRIBUTE].data] == [-1] * 4
assert not cloth.state()['diagnostics']

# An all-render-only package exports one ordinary render FBX, and no sim object.
ordinary_render = hair._join_objects(first[0])
before_finish = set(bpy.data.objects)
cloth.finish_asset(ordinary_render, [first[1]], 'RenderOnly', None, bpy.context.scene.collection, state)
render_only_packet = cloth.state()['packages'][ordinary_render[cloth.PACKAGE_PROPERTY]]['packet']
assert set(bpy.data.objects) == before_finish
assert render_only_packet['version'] == 2 and not render_only_packet['simulation_enabled']
assert render_only_packet['meshes']['render']['render_only_vertex_indices'] == list(range(4))
assert render_only_packet['meshes']['sim']['vertices'] == []
assert not cloth.state()['diagnostics']
with tempfile.TemporaryDirectory(prefix='send2ue-render-only-smoke-') as temp:
    fbx = Path(temp) / 'RenderOnly.fbx'
    fbx.write_bytes(b'export-receipt-fixture')
    asset_data = {'_mesh_object_name': ordinary_render.name, 'file_path': str(fbx),
                  'asset_path': '/Game/Smoke/RenderOnly'}
    cloth.record_export(asset_data)
    record = cloth.import_record(asset_data, None)
    assert record['version'] == 2 and not record['simulation_enabled']
    assert 'sim_asset_path' not in record and 'sim_fbx' not in record
    assert json.loads(Path(record['manifest_path']).read_text()) == render_only_packet
    assert cloth.sha256(record['manifest_path']) == record['manifest_sha256']

# A found source whose generator does not propagate ownership must not quietly
# turn into the no-guide fallback. Ordinary export data remains untouched.
guide = bpy.data.objects.new('FoundGuide', mesh.copy())
bpy.context.scene.collection.objects.link(guide)
hair._modifier_input_set(source.modifiers['Setup'], identifier, guide)
second = cloth.capture_source(source, state)
assert second is None
assert cloth.state()['diagnostics'][-1]['status'] == 'cloth_deferred'
assert (len(mesh.vertices), len(mesh.polygons), len(source.modifiers)) == original_counts
assert hair._modifier_input_get(source.modifiers['Setup'], identifier) == guide

# A GN result can contain a direct mesh and instances simultaneously. The
# ordinary export includes both; optional capture must not replace it with
# only the direct mesh. A no-guide output uses that complete ordinary evaluator.
mixed_mesh = bpy.data.meshes.new('MixedSource')
mixed_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
mixed = bpy.data.objects.new('MixedRender', mixed_mesh)
bpy.context.scene.collection.objects.link(mixed)
mixed_group, _ = passthrough('Hair_System_Setup_MixedSmoke')
group_input = next(n for n in mixed_group.nodes if n.type == 'GROUP_INPUT')
group_output = next(n for n in mixed_group.nodes if n.type == 'GROUP_OUTPUT')
instances = mixed_group.nodes.new('GeometryNodeGeometryToInstance')
join = mixed_group.nodes.new('GeometryNodeJoinGeometry')
mixed_group.links.new(group_input.outputs['Geometry'], instances.inputs['Geometry'])
mixed_group.links.new(group_input.outputs['Geometry'], join.inputs['Geometry'])
mixed_group.links.new(instances.outputs['Instances'], join.inputs['Geometry'])
mixed_group.links.new(join.outputs['Geometry'], group_output.inputs['Geometry'])
mixed.modifiers.new('Setup', 'NODES').node_group = mixed_group
bpy.context.view_layer.update()
ordinary = hair._evaluated_mesh_objects(mixed, state)
assert sum(len(obj.data.vertices) for obj in ordinary) == 6
mixed_capture = cloth.capture_source(mixed, state)
assert sum(len(obj.data.vertices) for obj in mixed_capture[0]) == 6
assert all(d.value == -1 for obj in mixed_capture[0]
           for d in obj.data.attributes[cloth.GUID_ATTRIBUTE].data)
assert not mixed_capture[1]['simulation_enabled'] and not mixed_capture[1]['vertices']
after_defer = hair._evaluated_mesh_objects(mixed, state)
assert sum(len(obj.data.vertices) for obj in after_defer) == 6
assert mixed.data == mixed_mesh and len(mixed_mesh.vertices) == 3

# Valid source ownership and render-only geometry can coexist in one export.
# The guide is copied through the generator with its exact island stamp.
guided = bpy.data.objects.new('GuidedRender', mesh.copy())
bpy.context.scene.collection.objects.link(guided)
guided_group, guide_socket = passthrough('Hair_System_Setup_GuidedSmoke')
guided_input = next(n for n in guided_group.nodes if n.type == 'GROUP_INPUT')
guided_output = next(n for n in guided_group.nodes if n.type == 'GROUP_OUTPUT')
info = guided_group.nodes.new('GeometryNodeObjectInfo')
named = guided_group.nodes.new('GeometryNodeInputNamedAttribute')
named.data_type = 'INT'
named.inputs['Name'].default_value = cloth.STAMP_ATTRIBUTE
store = guided_group.nodes.new('GeometryNodeStoreNamedAttribute')
store.domain, store.data_type = 'POINT', 'INT'
store.inputs['Name'].default_value = cloth.STAMP_ATTRIBUTE
guided_group.links.new(guided_input.outputs['Source Surface'], info.inputs['Object'])
guided_group.links.new(info.outputs['Geometry'], store.inputs['Geometry'])
guided_group.links.new(named.outputs['Attribute'], store.inputs['Value'])
guided_group.links.new(store.outputs['Geometry'], guided_output.inputs['Geometry'])
guided.modifiers.new('Setup', 'NODES').node_group = guided_group
hair._modifier_input_set(guided.modifiers['Setup'], guide_socket, guide)
bpy.context.view_layer.update()
guided_capture = cloth.capture_source(guided, state)
assert guided_capture is not None and guided_capture[1]['simulation_enabled']
assert len(guided_capture[1]['vertices']) == 4
assert guided_capture[1]['weights'] == [0.] * 4
assert not guided_capture[1]['parts'][0]['authored_weight_present']
joined = hair._join_objects(guided_capture[0] + mixed_capture[0])
bpy.ops.object.armature_add()
rig = bpy.context.object
cloth.finish_asset(joined, [guided_capture[1], mixed_capture[1]], 'MixedGuideRender', rig,
                   bpy.context.scene.collection, state)
mixed_packet = cloth.state()['packages'][joined[cloth.PACKAGE_PROPERTY]]['packet']
assert mixed_packet['simulation_enabled']
assert len(mixed_packet['meshes']['sim']['vertices']) == 4
assert len(mixed_packet['meshes']['render']['vertices']) == 10
assert len(mixed_packet['meshes']['render']['render_only_vertex_indices']) == 6
assert all(g > 0 for g in mixed_packet['meshes']['sim']['guide_ids'])
assert mixed_packet['render_only_sources'][0]['status'] == 'no_guide_after_search'
assert len(mixed_packet['parts']) == 1
assert len(guide.data.vertices) == 4 and 'ChaosWeight' not in guide.data.attributes

# A fully filtered no-guide source contributes no geometry. Its absence is
# ordinary output and cannot defer a sibling's valid guide cloth package.
empty = bpy.data.objects.new('EmptyFilteredRender', mesh.copy())
bpy.context.scene.collection.objects.link(empty)
empty_group, _ = passthrough('Hair_System_Setup_EmptySmoke')
for link in list(empty_group.links):
    empty_group.links.remove(link)
empty.modifiers.new('Setup', 'NODES').node_group = empty_group
bpy.context.view_layer.update()
empty_capture = cloth.capture_source(empty, state)
assert empty_capture is not None and empty_capture[0] == []
assert empty_capture[1]['render_vertex_count'] == 0
guided_again = cloth.capture_source(guided, state)
filtered_joined = hair._join_objects(guided_again[0] + empty_capture[0])
cloth.finish_asset(filtered_joined, [guided_again[1], empty_capture[1]], 'GuidedPlusEmpty', rig,
                   bpy.context.scene.collection, state)
filtered_packet = cloth.state()['packages'][filtered_joined[cloth.PACKAGE_PROPERTY]]['packet']
assert filtered_packet['simulation_enabled'] and len(filtered_packet['meshes']['sim']['vertices']) == 4
assert len(filtered_packet['meshes']['render']['vertices']) == 4
assert filtered_packet['render_only_sources'] == []
assert filtered_packet['meshes']['render']['render_only_vertex_indices'] == []
print('HAIR_GUIDE_POLICY_SMOKE ' + json.dumps({
    'no_guide_render_only_after_full_search': True, 'missing_guide_weight_static': True,
    'found_unsupported_guide_deferred': True, 'original_data_preserved': True,
    'no_guide_preserves_mesh_and_instances': True, 'pure_render_only_no_sim_object': True,
    'mixed_guided_and_render_only_keeps_full_render_and_only_real_guides': True,
    'empty_filtered_source_preserves_sibling_guide_export': True,
}), flush=True)
