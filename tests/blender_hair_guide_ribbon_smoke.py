"""Small real-Geometry-Nodes regression for export-only guide ribbons.

Run with Blender 5.2 --background --factory-startup --python-exit-code 23
--python tests/blender_hair_guide_ribbon_smoke.py. Supply --hair-library after
``--`` to override the installed Hair Tool 5.2 node library. No scene or user
preferences are saved. The library is read, never edited on disk.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy
from mathutils.kdtree import KDTree


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src/addons'))
assert bpy.app.background, 'This test must run in an isolated background worker'
# Send2UE unregister cleans its data folder. Keep factory QA out of the shared
# GUI export directory, including during Blender shutdown after this script.
_qa_temp = tempfile.TemporaryDirectory(prefix='send2ue-ribbon-smoke-')
tempfile.tempdir = _qa_temp.name
assert addon_utils.enable('send2ue', default_set=False)
from send2ue.core import hair_guide_cloth as cloth, hair_guide_ribbon as ribbon
from send2ue.core import hair_tool_export as hair

parser = argparse.ArgumentParser()
parser.add_argument('--hair-library', type=Path, default=Path(bpy.utils.user_resource('SCRIPTS')) /
                    'addons/hair_tool/hair_baking/hsystem_nodes_lib_5.2.blend')
parser.add_argument('--report', type=Path, help='Optional persistent JSON verification receipt')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
assert args.hair_library.is_file(), 'A Hair Tool 5.2 node library is required: ' + str(args.hair_library)
library_hash = hashlib.sha256(args.hair_library.read_bytes()).hexdigest()
with bpy.data.libraries.load(str(args.hair_library), link=False) as (source, target):
    required = ('Circle_Profile_UV', 'Flat_Profile_UV')
    assert set(required).issubset(source.node_groups)
    target.node_groups = list(required)
CIRCLE, FLAT = [bpy.data.node_groups[name] for name in required]
with bpy.data.libraries.load(str(args.hair_library), link=False) as (source, target):
    target.node_groups = ['HS_Grid_Guide']
GRID = bpy.data.node_groups['HS_Grid_Guide']


def socket_value(socket):
    value = getattr(socket, 'default_value', None)
    if isinstance(value, bpy.types.ID):
        return {'id': value.name_full, 'type': type(value).__name__}
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    try:
        return list(value)
    except TypeError:
        return repr(value)


def graph_signature(root):
    """Inspect shared nested groups too, so a shallow-copy mutation is caught."""
    groups = {}
    def visit(group):
        if group.name_full in groups:
            return
        nodes = []
        groups[group.name_full] = nodes
        for node in group.nodes:
            nested = getattr(node, 'node_tree', None)
            nodes.append({'name': node.name, 'type': node.bl_idname, 'mute': node.mute,
                          'group': nested.name_full if nested else None,
                          'operation': getattr(node, 'operation', None),
                          'domain': getattr(node, 'domain', None),
                          'data_type': getattr(node, 'data_type', None),
                          'inputs': [(s.identifier, socket_value(s)) for s in node.inputs],
                          'outputs': [(s.identifier, socket_value(s)) for s in node.outputs]})
            if nested:
                visit(nested)
        nodes.append({'links': sorted((link.from_node.name, link.from_socket.identifier,
                                       link.to_node.name, link.to_socket.identifier) for link in group.links)})
    visit(root)
    return json.dumps(groups, sort_keys=True)


def connect(group, output, target):
    for link in list(target.links):
        group.links.remove(link)
    group.links.new(output, target)


def store(group, geometry, name, data_type, domain, value):
    node = group.nodes.new('GeometryNodeStoreNamedAttribute')
    node.data_type, node.domain = data_type, domain
    node.inputs['Name'].default_value = name
    group.links.new(geometry, node.inputs['Geometry'])
    if hasattr(value, 'is_output'):
        group.links.new(value, node.inputs['Value'])
    else:
        node.inputs['Value'].default_value = value
    return node.outputs['Geometry']


def fixture(name, profile_group=CIRCLE, profile_res=None):
    curve = bpy.data.curves.new(name + '_Curves', 'CURVE')
    curve.dimensions = '3D'
    for strand in range(3):
        spline = curve.splines.new('POLY')
        spline.points.add(4)
        for index, point in enumerate(spline.points):
            # Three guides cross in projection and approach within millimeters,
            # so a nearest-guide heuristic cannot stand in for A/B/C lineage.
            x = (-.1 + .05 * index, .1 - .05 * index, .0007 + .008 * index)[strand]
            point.co = (x, (index % 3) * .004 + strand * .003,
                        .4 - index * .08 + strand * .002, 1.)
            point.radius = .7 + index * .08
            point.tilt = index * .07
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    group = bpy.data.node_groups.new('Hair_System_Profile_' + name, 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source, output = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
    index = group.nodes.new('GeometryNodeInputIndex')
    factor = group.nodes.new('GeometryNodeSplineParameter')
    geo = store(group, source.outputs['Geometry'], 'fixture_spline_id', 'INT', 'CURVE', index.outputs['Index'])
    geo = store(group, geo, 'fixture_sample_id', 'INT', 'POINT', index.outputs['Index'])
    geo = store(group, geo, 'fixture_sample_parameter', 'FLOAT', 'POINT', factor.outputs['Factor'])
    geo = store(group, geo, 'roundness', 'FLOAT', 'POINT', 1.)
    owner = group.nodes.new('GeometryNodeInputNamedAttribute')
    owner.data_type = 'INT'
    owner.inputs['Name'].default_value = 'fixture_spline_id'
    first = group.nodes.new('ShaderNodeMath')
    first.operation = 'LESS_THAN'
    group.links.new(owner.outputs['Attribute'], first.inputs[0])
    first.inputs[1].default_value = .5
    moving = group.nodes.new('ShaderNodeMath')
    moving.operation = 'GREATER_THAN'
    group.links.new(factor.outputs['Factor'], moving.inputs[0])
    moving.inputs[1].default_value = .3
    mask = group.nodes.new('ShaderNodeMath')
    mask.operation = 'MULTIPLY'
    group.links.new(first.outputs[0], mask.inputs[0])
    group.links.new(moving.outputs[0], mask.inputs[1])
    weight = group.nodes.new('ShaderNodeMath')
    weight.operation = 'MULTIPLY'
    group.links.new(mask.outputs[0], weight.inputs[0])
    group.links.new(factor.outputs['Factor'], weight.inputs[1])
    geo = store(group, geo, 'ChaosWeight', 'FLOAT', 'POINT', weight.outputs[0])
    profile = group.nodes.new('GeometryNodeGroup')
    profile.node_tree, profile.name = profile_group, profile_group.name
    group.links.new(geo, profile.inputs['Curves'])
    if profile.inputs.get('Spline Type'):
        profile.inputs['Spline Type'].default_value = 'Poly'
    if profile_res is None:
        profile_res = 2 if profile_group == CIRCLE else 1
    for key, value in [('Resolution', 1), ('Profile Res', profile_res),
                       ('Width', .018), ('Roundness', 1. if profile_group == CIRCLE else 0.),
                       ('Radius from UV', False), ('Width from UV', False),
                       ('Add Root Caps', False), ('Add Tip Caps', False),
                       ('Bevel Caps', False), ('Use UV Tiling', False)]:
        if profile.inputs.get(key):
            profile.inputs[key].default_value = value
    group.links.new(profile.outputs[0], output.inputs['Geometry'])
    obj.modifiers.new('Profile', 'NODES').node_group = group
    return obj


def renderer(guide, filter_first=False):
    mesh = bpy.data.meshes.new(guide.name + '_RenderData')
    obj = bpy.data.objects.new(guide.name + '_Render', mesh)
    bpy.context.scene.collection.objects.link(obj)
    group = bpy.data.node_groups.new('Hair_System_Setup_' + obj.name, 'GeometryNodeTree')
    source_socket = group.interface.new_socket(name='Source Surface', in_out='INPUT', socket_type='NodeSocketObject')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source, output = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
    info = group.nodes.new('GeometryNodeObjectInfo')
    group.links.new(source.outputs['Source Surface'], info.inputs['Object'])
    owner = group.nodes.new('GeometryNodeInputNamedAttribute')
    owner.data_type = 'INT'
    owner.inputs['Name'].default_value = cloth.STAMP_ATTRIBUTE
    geo = info.outputs['Geometry']
    if filter_first:
        # Prism may remove a source island, then compact its own native index.
        # Emulate that topology operation while preserving the original stamp.
        spline = group.nodes.new('GeometryNodeInputNamedAttribute')
        spline.data_type = 'INT'
        spline.inputs['Name'].default_value = 'fixture_spline_id'
        keep = group.nodes.new('ShaderNodeMath')
        keep.operation = 'GREATER_THAN'
        group.links.new(spline.outputs['Attribute'], keep.inputs[0])
        keep.inputs[1].default_value = .5
        separate = group.nodes.new('GeometryNodeSeparateGeometry')
        separate.domain = 'FACE'
        group.links.new(geo, separate.inputs['Geometry'])
        group.links.new(keep.outputs[0], separate.inputs['Selection'])
        geo = separate.outputs['Selection']
        islands = group.nodes.new('GeometryNodeInputMeshIsland')
        geo = store(group, geo, 'src_island_index', 'INT', 'POINT', islands.outputs['Island Index'])
    geo = store(group, geo, cloth.STAMP_ATTRIBUTE, 'INT', 'POINT', owner.outputs['Attribute'])
    shifted = group.nodes.new('GeometryNodeSetPosition')
    group.links.new(geo, shifted.inputs['Geometry'])
    shifted.inputs['Offset'].default_value = (.02, .003, .001)
    group.links.new(shifted.outputs['Geometry'], output.inputs['Geometry'])
    modifier = obj.modifiers.new('Setup', 'NODES')
    modifier.node_group = group
    hair._modifier_input_set(modifier, source_socket.identifier, guide)
    return obj


def grid_renderer(guide):
    """Real Strands from Grid Surface, using the native installed node group."""
    obj = bpy.data.objects.new(guide.name + '_GridRender', bpy.data.meshes.new('GridRenderData'))
    bpy.context.scene.collection.objects.link(obj)
    group = bpy.data.node_groups.new('Hair_System_Setup_' + obj.name, 'GeometryNodeTree')
    source_socket = group.interface.new_socket(name='Source Surface', in_out='INPUT', socket_type='NodeSocketObject')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source, output = group.nodes.new('NodeGroupInput'), group.nodes.new('NodeGroupOutput')
    info = group.nodes.new('GeometryNodeObjectInfo')
    group.links.new(source.outputs['Source Surface'], info.inputs['Object'])
    parameter = group.nodes.new('GeometryNodeInputNamedAttribute')
    parameter.data_type = 'FLOAT'
    parameter.inputs['Name'].default_value = 'fixture_sample_parameter'
    root = group.nodes.new('ShaderNodeMath')
    root.operation = 'LESS_THAN'
    group.links.new(parameter.outputs['Attribute'], root.inputs[0])
    root.inputs[1].default_value = 1e-7
    geo = store(group, info.outputs['Geometry'], 'sharp_edge', 'BOOLEAN', 'EDGE', root.outputs[0])
    grid = group.nodes.new('GeometryNodeGroup')
    grid.node_tree = GRID
    group.links.new(geo, grid.inputs['Mesh'])
    grid.inputs['Resolution'].default_value = 5
    grid.inputs['Strands Count'].default_value = 3
    grid.inputs['Placement Mask'].default_value = 1.
    grid.inputs['Use Strand Count as Density'].default_value = False
    grid.inputs['Normalize Strand Count'].default_value = False
    profile = group.nodes.new('GeometryNodeGroup')
    profile.node_tree = FLAT
    group.links.new(grid.outputs['Curve'], profile.inputs['Curves'])
    profile.inputs['Profile Res'].default_value = 1
    profile.inputs['Width'].default_value = .001
    profile.inputs['Spline Type'].default_value = 'Poly'
    profile.inputs['Resolution'].default_value = 1
    group.links.new(profile.outputs[0], output.inputs['Geometry'])
    modifier = obj.modifiers.new('Setup', 'NODES')
    modifier.node_group = group
    hair._modifier_input_set(modifier, source_socket.identifier, guide)
    return obj


def geometry(mesh):
    mesh.calc_loop_triangles()
    return ([tuple(v.co) for v in mesh.vertices], [tuple(t.vertices) for t in mesh.loop_triangles])


def point_attribute(mesh, name):
    attr = mesh.attributes.get(name)
    assert attr and attr.domain == 'POINT', (name, attr.domain if attr else None)
    return [d.value for d in attr.data]


def dispose_mesh(mesh):
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def run_plan(source, mode, profile_points=None):
    copy = source.copy()
    bpy.context.scene.collection.objects.link(copy)
    if profile_points is not None:
        copy['send2ue_cloth_ribbon_profile_points'] = profile_points
    plan = ribbon.prepare(copy, mode)
    original = cloth._copy_mesh(copy)
    owners = cloth.components(original)
    try:
        converted, converted_owners, receipt = plan.convert(original, owners, cloth._copy_mesh, cloth.own_weights)
        return original, converted, owners, converted_owners, dict(receipt)
    finally:
        plan.close()
        bpy.data.objects.remove(copy, do_unlink=True)


cfg = cloth.settings()
assert cfg is not None and cfg.simulation_mesh_mode == 'RIBBON', 'Ribbon must be the default export mode'
enum = cfg.bl_rna.properties['simulation_mesh_mode']
assert {'RIBBON', 'ORIGINAL'}.issubset({item.identifier for item in enum.enum_items})
assert next(item.name for item in enum.enum_items if item.identifier == 'RIBBON') == 'Optimized ribbon guides'
guide = fixture('RibbonRegression')
render = renderer(guide)
baseline_group_signature = graph_signature(guide.modifiers['Profile'].node_group)
baseline_guide = cloth._copy_mesh(guide)
baseline_render = cloth._copy_mesh(render)
baseline_geometry = geometry(baseline_guide)
baseline_render_geometry = geometry(baseline_render)
baseline_groups = set(bpy.data.node_groups)
baseline_objects = set(bpy.data.objects)
receipts = []
expected_sim_source_by_position = {}

# Repeat conversion to expose temporary object/group leaks and shallow copies.
for iteration in range(3):
    before_meshes = set(bpy.data.meshes)
    original, converted, owners, converted_owners, receipt = run_plan(guide, 'RIBBON')
    assert converted != original and receipt['effective_mode'] == 'RIBBON', receipt
    assert geometry(original) == baseline_geometry
    assert len(converted.vertices) * 2 == len(original.vertices)
    assert set(converted_owners) == set(owners) and len(set(owners)) == 3
    original_keys, converted_keys = defaultdict(list), defaultdict(list)
    for mesh, keys in [(original, original_keys), (converted, converted_keys)]:
        for index, key in enumerate(zip(point_attribute(mesh, 'fixture_spline_id'),
                                        point_attribute(mesh, 'fixture_sample_id'))):
            keys[key].append(index)
    assert set(original_keys) == set(converted_keys)
    assert {len(v) for v in original_keys.values()} == {4}
    assert {len(v) for v in converted_keys.values()} == {2}
    original_weights, _ = cloth.own_weights(original)
    converted_weights, _ = cloth.own_weights(converted)
    assert any(w == 0 for w in converted_weights) and any(w > 0 for w in converted_weights)
    for key, vertices in converted_keys.items():
        source_vertices = original_keys[key]
        assert len({original_weights[i] for i in source_vertices}) == 1
        assert all(converted_weights[i] == original_weights[source_vertices[0]] for i in vertices)
        for index in vertices:
            error = min((converted.vertices[index].co - original.vertices[j].co).length for j in source_vertices)
            assert error <= 2e-6, (key, error)
            expected_sim_source_by_position[tuple(converted.vertices[index].co)] = key[0]
    assert all(converted_weights[i] == 0 for key, indices in converted_keys.items() if key[0] == 1 for i in indices)
    assert graph_signature(guide.modifiers['Profile'].node_group) == baseline_group_signature
    render_after = cloth._copy_mesh(render)
    assert geometry(render_after) == baseline_render_geometry
    dispose_mesh(render_after)
    dispose_mesh(converted)
    dispose_mesh(original)
    assert set(bpy.data.meshes) == before_meshes
    assert set(bpy.data.node_groups) == baseline_groups
    assert set(bpy.data.objects) == baseline_objects
    receipts.append(receipt)

# Optional center support keeps the sample centers, weights and source graph.
before_meshes = set(bpy.data.meshes)
original, converted, owners, converted_owners, center_receipt = run_plan(guide, 'RIBBON', 3)
assert converted is not original and center_receipt['status'] == 'converted', center_receipt
assert center_receipt['profile_points'] == 3
assert len(converted.vertices) == 45 and len(converted.loop_triangles) == 48
assert set(converted_owners) == set(owners)
assert geometry(original) == baseline_geometry
assert graph_signature(guide.modifiers['Profile'].node_group) == baseline_group_signature
dispose_mesh(converted)
dispose_mesh(original)
assert set(bpy.data.meshes) == before_meshes
assert set(bpy.data.objects) == baseline_objects
assert set(bpy.data.node_groups) == baseline_groups

# Unsupported profile counts fail closed with the source retained.
for count in (1, 4, True):
    original, converted, owners, converted_owners, invalid_receipt = run_plan(guide, 'RIBBON', count)
    assert converted is original and invalid_receipt['status'] == 'fallback'
    assert geometry(original) == baseline_geometry
    dispose_mesh(original)

# Explicit Original is byte-for-byte geometry/weight passthrough.
original, converted, owners, converted_owners, original_receipt = run_plan(guide, 'ORIGINAL')
assert converted is original and converted_owners == owners
assert original_receipt['effective_mode'] == 'ORIGINAL'
assert geometry(original) == baseline_geometry
dispose_mesh(original)

# Flat guides enter the same conversion/identity/weight checks as circular ones.
# High transverse resolution reduces to one segment; an existing one-segment
# guide stays at the same positions. Source/render graphs remain untouched.
planar_receipts = []
for resolution in (1, 4):
    planar = fixture('PlanarRegression' + str(resolution), FLAT, resolution)
    planar_render = renderer(planar)
    planar_signature = graph_signature(planar.modifiers['Profile'].node_group)
    planar_baseline = cloth._copy_mesh(planar)
    planar_render_baseline = cloth._copy_mesh(planar_render)
    baseline_state = (set(bpy.data.objects), set(bpy.data.meshes), set(bpy.data.node_groups))
    for points in (2, 3):
        original, converted, owners, converted_owners, planar_receipt = run_plan(planar, 'RIBBON', points)
        assert converted is not original and planar_receipt['status'] == 'converted', planar_receipt
        assert planar_receipt['profile'] == 'FLAT' and planar_receipt['profile_points'] == points
        assert len(converted.vertices) == 15 * points
        assert len(converted.loop_triangles) == 24 * (points - 1)
        assert set(converted_owners) == set(owners) and len(set(owners)) == 3
        assert geometry(original) == geometry(planar_baseline)
        if resolution == 1 and points == 2:
            assert geometry(converted) == geometry(planar_baseline)
        if resolution == 4 and points == 2:
            assert len(converted.vertices) < len(original.vertices)
            # Reduced rails retain native width, curve radius, tilt and position.
            for vertex in converted.vertices:
                assert min((vertex.co - source.co).length for source in original.vertices) < 2e-6
        dispose_mesh(converted)
        dispose_mesh(original)
        assert baseline_state == (set(bpy.data.objects), set(bpy.data.meshes), set(bpy.data.node_groups))
        planar_receipts.append(dict(planar_receipt, source_profile_res=resolution))
    # Verify the public capture boundary as well as the plan in isolation.
    cloth.begin()
    planar_capture = cloth.capture_source(planar_render, {'temporary_object_names': set(), 'temporary_mesh_names': set()})
    assert planar_capture is not None and planar_capture[1]['simulation_enabled']
    assert len(planar_capture[1]['vertices']) == 30
    assert len(planar_capture[1]['triangles']) == 24
    assert planar_capture[1]['source']['simulation_mesh']['status'] == 'converted'
    assert planar_capture[1]['source']['render_ownership']['spline_mismatches'] == 0
    assert len(planar_capture[1]['parts']) == 3
    assert geometry(planar_capture[0][0].data) == geometry(planar_render_baseline)
    for obj in planar_capture[0]:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        dispose_mesh(mesh)
    assert graph_signature(planar.modifiers['Profile'].node_group) == planar_signature
    assert baseline_state == (set(bpy.data.objects), set(bpy.data.meshes), set(bpy.data.node_groups))
    original, converted, owners, converted_owners, passthrough = run_plan(planar, 'ORIGINAL')
    assert converted is original and converted_owners == owners
    assert geometry(original) == geometry(planar_baseline)
    dispose_mesh(original)
    dispose_mesh(planar_baseline)
    dispose_mesh(planar_render_baseline)

# The native grid generator must preserve exact spline ownership through the
# same public capture boundary, rather than only passing a surrogate renderer.
grid_guide = fixture('NativeGridRegression', FLAT, 4)
grid_render = grid_renderer(grid_guide)
grid_baseline = cloth._copy_mesh(grid_render)
assert len(grid_baseline.vertices) > 0, 'Native grid fixture generated no strands'
cloth.begin()
grid_capture = cloth.capture_source(grid_render, {'temporary_object_names': set(), 'temporary_mesh_names': set()})
assert grid_capture is not None and grid_capture[1]['simulation_enabled'], cloth.state()['diagnostics']
assert len(grid_capture[1]['vertices']) == 30
assert len(grid_capture[1]['triangles']) == 24
assert grid_capture[1]['source']['simulation_mesh']['status'] == 'converted'
assert geometry(grid_capture[0][0].data) == geometry(grid_baseline)
grid_receipt = grid_capture[1]['source']
for obj in grid_capture[0]:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose_mesh(mesh)
dispose_mesh(grid_baseline)

# Curved/high-resolution flat profiles keep their longitudinal evaluation and
# native rail width even with transverse bow and center bevel enabled upstream.
shaped = fixture('ShapedFlatRegression', FLAT, 6)
shaped_profile = next(n for n in shaped.modifiers['Profile'].node_group.nodes if getattr(n, 'node_tree', None) == FLAT)
for name, value in [('Spline Type', 'Catmull Rom'), ('Resolution', 3), ('Roundness', .4),
                    ('Uplift', .7), ('Bevel Center Axis', 2)]:
    shaped_profile.inputs[name].default_value = value
shaped_signature = graph_signature(shaped.modifiers['Profile'].node_group)
shaped_baseline = cloth._copy_mesh(shaped)
original, converted, owners, converted_owners, shaped_receipt = run_plan(shaped, 'RIBBON')
assert converted is not original and shaped_receipt['status'] == 'converted', shaped_receipt
assert geometry(original) == geometry(shaped_baseline), 'Flat sample stamps changed authored geometry'
assert shaped_receipt['sample_count'] > 15
for vertex in converted.vertices:
    assert min((vertex.co - source.co).length for source in original.vertices) < 2e-6
assert graph_signature(shaped.modifiers['Profile'].node_group) == shaped_signature
dispose_mesh(converted)
dispose_mesh(original)
shaped_render = grid_renderer(shaped)
shaped_render_baseline = cloth._copy_mesh(shaped_render)
cloth.begin()
shaped_capture = cloth.capture_source(shaped_render, {'temporary_object_names': set(), 'temporary_mesh_names': set()})
assert shaped_capture is not None and shaped_capture[1]['simulation_enabled'], cloth.state()['diagnostics']
shaped_capture_receipt = shaped_capture[1]['source']['simulation_mesh']
assert shaped_capture_receipt['status'] == 'converted', shaped_capture_receipt
assert shaped_capture_receipt['original_guide_geometry_unchanged'] and shaped_capture_receipt['render_geometry_unchanged']
assert geometry(shaped_capture[0][0].data) == geometry(shaped_render_baseline)
for obj in shaped_capture[0]:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose_mesh(mesh)
dispose_mesh(shaped_render_baseline)
dispose_mesh(shaped_baseline)

# An unknown flat layout uses the same safe fallback as an unknown circle.
unsupported_flat = fixture('UnsupportedFlatRegression', FLAT, 4)
flat_profile = next(n for n in unsupported_flat.modifiers['Profile'].node_group.nodes if getattr(n, 'node_tree', None) == FLAT)
flat_profile.node_tree = FLAT.copy()
flat_profile.node_tree.nodes['Set Position'].name = 'UnknownFlatShaping'
original, converted, owners, converted_owners, flat_fallback = run_plan(unsupported_flat, 'RIBBON')
assert converted is original and converted_owners == owners
assert flat_fallback['status'] == 'fallback' and flat_fallback['fallback_reason']
dispose_mesh(original)

# A Circle-shaped graph with an unsupported contract must retain original sim
# data and explain fallback; it must not alter the artist or shared vendor graph.
unsupported = fixture('UnsupportedRegression')
unsupported_profile = next(n for n in unsupported.modifiers['Profile'].node_group.nodes if getattr(n, 'node_tree', None) == CIRCLE)
broken = CIRCLE.copy()
unsupported_profile.node_tree = broken
broken.nodes['Set Position.002'].name = 'Unsupported_Post_Sweep_Name'
unsupported_signature = graph_signature(unsupported.modifiers['Profile'].node_group)
original, converted, owners, converted_owners, fallback_receipt = run_plan(unsupported, 'RIBBON')
assert converted is original and converted_owners == owners
assert fallback_receipt['effective_mode'] == 'ORIGINAL' and fallback_receipt.get('fallback_reason'), fallback_receipt
assert graph_signature(unsupported.modifiers['Profile'].node_group) == unsupported_signature
dispose_mesh(original)

# Test through the existing capture boundary: the renderer must be evaluated
# with its original guide, even though the sim packet uses a reduced ribbon.
cfg.simulation_mesh_mode = 'RIBBON'
cloth.begin()
export_state = {'temporary_object_names': set(), 'temporary_mesh_names': set()}
capture = cloth.capture_source(render, export_state)
assert capture is not None and capture[1]['simulation_enabled']
assert len(capture[1]['vertices']) * 2 == len(baseline_guide.vertices)
assert len(capture[0]) == 1 and geometry(capture[0][0].data) == baseline_render_geometry
assert len(capture[1]['parts']) == 3, 'All-zero islands within active source must be retained'
gid_to_spline = defaultdict(set)
for point, gid in zip(capture[1]['vertices'], capture[1]['guide_ids']):
    gid_to_spline[gid].add(expected_sim_source_by_position[tuple(point)])
assert len(gid_to_spline) == 3 and all(len(values) == 1 for values in gid_to_spline.values())
assert {next(iter(values)) for values in gid_to_spline.values()} == {0, 1, 2}
captured_render = capture[0][0].data
render_splines = point_attribute(captured_render, 'fixture_spline_id')
render_gids = point_attribute(captured_render, cloth.GUID_ATTRIBUTE)
for source_spline, gid in zip(render_splines, render_gids):
    assert gid_to_spline[gid] == {source_spline}, 'A/B/C children were assigned to another source spline'
native_crosswalk = capture[1]['source']['simulation_mesh']['native_island_to_spline']
assert set(native_crosswalk.values()) == {0, 1, 2} and len(native_crosswalk) == 3
tree = KDTree(len(baseline_guide.vertices))
for index, vertex in enumerate(baseline_guide.vertices):
    tree.insert(vertex.co, index)
tree.balance()
guide_splines = point_attribute(baseline_guide, 'fixture_spline_id')
nearer_foreign_source_vertices = sum(guide_splines[tree.find(vertex.co)[1]] != source_spline
                                   for vertex, source_spline in zip(captured_render.vertices, render_splines))
assert nearer_foreign_source_vertices > 0, 'Fixture must expose incorrect nearest-guide assignment'
for obj in capture[0]:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose_mesh(mesh)

# A generator's compact post-filter IDs must never override the original
# source-island stamp: native {0,1} here means original spline {1,2}.
filtered_render = renderer(guide, filter_first=True)
cloth.begin()
filtered_capture = cloth.capture_source(filtered_render, export_state)
assert filtered_capture is not None and filtered_capture[1]['simulation_enabled']
filtered_mesh = filtered_capture[0][0].data
filtered_splines = point_attribute(filtered_mesh, 'fixture_spline_id')
filtered_native = point_attribute(filtered_mesh, 'src_island_index')
assert set(filtered_splines) == {1, 2} and set(filtered_native) == {0, 1}
filtered_gid_spline = {}
for point, gid in zip(filtered_capture[1]['vertices'], filtered_capture[1]['guide_ids']):
    filtered_gid_spline[gid] = expected_sim_source_by_position[tuple(point)]
for gid, spline, native in zip(point_attribute(filtered_mesh, cloth.GUID_ATTRIBUTE),
                               filtered_splines, filtered_native):
    assert filtered_gid_spline[gid] == spline == native + 1
filtered_ownership = filtered_capture[1]['source']['render_ownership']
assert filtered_ownership['spline_mismatches'] == 0
assert filtered_ownership['generator_native_index_to_source_islands'] == {'0': [1], '1': [2]}
for obj in filtered_capture[0]:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose_mesh(mesh)

# If a generator actually overwrites the original stamp with its compact ID,
# the independent spline stamp must stop capture instead of misbinding a child.
filtered_group = filtered_render.modifiers['Setup'].node_group
native_lookup = filtered_group.nodes.new('GeometryNodeInputNamedAttribute')
native_lookup.data_type = 'INT'
native_lookup.inputs['Name'].default_value = 'src_island_index'
ownership_store = next(n for n in filtered_group.nodes if n.bl_idname == 'GeometryNodeStoreNamedAttribute'
                       and n.inputs['Name'].default_value == cloth.STAMP_ATTRIBUTE)
connect(filtered_group, native_lookup.outputs['Attribute'], ownership_store.inputs['Value'])
cloth.begin()
before_rejected = (set(bpy.data.objects), set(bpy.data.meshes), set(bpy.data.node_groups))
assert cloth.capture_source(filtered_render, export_state) is None
assert any('independently captured spline identity' in row['reason'] for row in cloth.state()['diagnostics'])
assert before_rejected == (set(bpy.data.objects), set(bpy.data.meshes), set(bpy.data.node_groups))

# Verify the fallback also survives the public capture path and emits the
# diagnostic consumed by export manifests; a receipt alone is insufficient.
unsupported_render = renderer(unsupported)
unsupported_baseline = cloth._copy_mesh(unsupported)
unsupported_render_baseline = cloth._copy_mesh(unsupported_render)
cloth.begin()
fallback_capture = cloth.capture_source(unsupported_render, export_state)
assert fallback_capture is not None and fallback_capture[1]['simulation_enabled']
assert len(fallback_capture[1]['vertices']) == len(unsupported_baseline.vertices)
assert geometry(fallback_capture[0][0].data) == geometry(unsupported_render_baseline)
assert fallback_capture[1]['source']['simulation_mesh']['effective_mode'] == 'ORIGINAL'
assert any(row['status'] == 'original_simulation_retained' and row['reason']
           for row in cloth.state()['diagnostics'])
assert graph_signature(unsupported.modifiers['Profile'].node_group) == unsupported_signature
dispose_mesh(unsupported_baseline)
dispose_mesh(unsupported_render_baseline)
for obj in fallback_capture[0]:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    dispose_mesh(mesh)

# Whole-source all-zero behavior remains the existing render-only policy.
static = fixture('StaticRegression')
static_render = renderer(static)
static_group = static.modifiers['Profile'].node_group
weight_store = next(n for n in static_group.nodes if n.bl_idname == 'GeometryNodeStoreNamedAttribute'
                    and n.inputs['Name'].default_value == 'ChaosWeight')
for link in list(weight_store.inputs['Value'].links):
    static_group.links.remove(link)
weight_store.inputs['Value'].default_value = 0.
static_signature = graph_signature(static_group)
cloth.begin()
static_capture = cloth.capture_source(static_render, export_state)
assert static_capture is not None and not static_capture[1]['simulation_enabled']
assert static_capture[1]['source']['status'] == 'guide_weights_all_zero'
assert static_capture[1]['vertices'] == static_capture[1]['triangles'] == []
assert graph_signature(static_group) == static_signature
assert not cloth.state()['diagnostics'], 'A static source must not be reported as a failed conversion'
assert graph_signature(guide.modifiers['Profile'].node_group) == baseline_group_signature
assert hashlib.sha256(args.hair_library.read_bytes()).hexdigest() == library_hash
report = {
    'blender_version': bpy.app.version_string,
    'hair_library_sha256': library_hash,
    'default_ribbon_and_original_option': True,
    'optional_three_point_center_support': center_receipt,
    'invalid_profile_count_retains_original': True,
    'shared_artist_nodegroups_unchanged': True,
    'repeated_conversion_cleanup': True,
    'same_spline_samples_and_own_G': True,
    'partial_zero_island_preserved': True,
    'three_crossing_splines_keep_exclusive_child_ownership': True,
    'render_vertices_nearer_foreign_guide_but_keep_authored_owner': nearer_foreign_source_vertices,
    'post_filter_compact_generator_ids_do_not_replace_source_identity': True,
    'conflicting_compact_id_stamp_is_rejected_and_cleaned_up': True,
    'renderer_unchanged': True,
    'original_passthrough': True,
    'planar_same_optimization_path': True,
    'planar_original_passthrough': True,
    'native_grid_surface_capture': grid_receipt,
    'shaped_flat_longitudinal_samples_and_rails_preserved': shaped_receipt,
    'shaped_flat_native_grid_capture': shaped_capture_receipt,
    'unsupported_flat_original_fallback': flat_fallback,
    'unsupported_original_fallback_with_reason': True,
    'unsupported_capture_retains_geometry_and_emits_diagnostic': True,
    'whole_zero_source_render_only': True,
    'receipts': receipts,
    'planar_receipts': planar_receipts,
    'fallback_receipt': fallback_receipt,
}
if args.report:
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('HAIR_GUIDE_RIBBON_SMOKE ' + json.dumps(report), flush=True)
