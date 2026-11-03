"""PARK-only opt-in, native FBX roundtrip, and success/failure source restoration.

Run in disposable Blender memory with --factory-startup --background --python
<this file> -- <output-directory> <park_spline_tools parent directory>.
Never saves a blend file or user preferences.
"""
import addon_utils
import bpy
import hashlib
import json
from pathlib import Path
import sys
import traceback

args = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, 'C:/UnrealProjects/MyProject2/Docs/Tools/Media')
from review_storage import output_path
OUT = output_path(args[0], directory=True)
sys.path.insert(0, str(Path(args[1]).resolve()))
report = {}


def scene_state():
    return {obj.name: {
        'type': obj.type, 'data': obj.data.name if obj.data else None,
        'parent': obj.parent.name if obj.parent else None,
        'matrix': [list(row) for row in obj.matrix_world],
        'hidden': obj.hide_get(), 'render': obj.hide_render,
        'collections': sorted(c.name for c in obj.users_collection),
        'modifiers': [(m.name, m.type, m.show_viewport, m.show_render,
                       m.node_group.name if m.type == 'NODES' and m.node_group else None)
                      for m in obj.modifiers],
        'points': [[list(p.co) for p in s.points] for s in obj.data.splines]
                  if obj.type == 'CURVE' else None,
    } for obj in bpy.data.objects}


def mesh_info(mesh):
    return {'vertices': len(mesh.vertices), 'polygons': len(mesh.polygons),
            'uvs': {uv.name: hashlib.sha256(b''.join(
                str(tuple(item.uv)).encode() for item in uv.data)).hexdigest()
                for uv in mesh.uv_layers},
            'materials': [m.name if m else None for m in mesh.materials]}


try:
    assert addon_utils.enable('send2ue', default_set=False)
    # Disposable in-memory preference entry for setup_project; never persisted.
    if bpy.context.preferences.addons.get('send2ue') is None:
        bpy.context.preferences.addons.new().module = 'send2ue'
    from send2ue.core import utilities, export_selection, park_spline_export as patch
    utilities.setup_project()
    import park_spline_tools as park
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    collection = bpy.data.collections.get('Export')
    if collection is None:
        collection = bpy.data.collections.new('Export')
        bpy.context.scene.collection.children.link(collection)
    props = bpy.context.scene.send2ue
    props.path_mode = 'send_to_disk'
    props.disk_mesh_folder_path = str(OUT)
    props.disk_animation_folder_path = str(OUT)
    props.disk_groom_folder_path = str(OUT)
    props.import_animations = False
    props.extensions.combine_assets.combine = 'child_meshes'
    props.extensions.material_pipeline.enabled = False
    material = bpy.data.materials.new('M_ParkSplineQA')
    parent = bpy.data.objects.new('PARK_Render_Group', None)
    collection.objects.link(parent)
    parent.location = (2, 3, 4)
    parent.rotation_euler = (0.1, -0.2, 0.3)
    parent.scale = (1.2, 0.8, 1.1)

    def curve(name, kind=None):
        data = bpy.data.curves.new(name, 'CURVE')
        data.dimensions = '3D'
        data.use_path = False
        spline = data.splines.new('POLY')
        spline.points.add(2)
        for point, co in zip(spline.points, [(0, 0, 0, 1), (0, 0, 1, 1), (0.3, 0, 2, 1)]):
            point.co = co
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.parent = parent
        obj.location = (len(collection.objects) * 0.4, 0, 0)
        obj.data.materials.append(material)
        if kind:
            mod = obj.modifiers.new('Renamed modifier', 'NODES')
            mod.node_group = park.build(kind)
            # Set a material on the evaluated faces, as in the real GN workflow.
            group = mod.node_group
            output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
            if not any(n.bl_idname == 'GeometryNodeSetMaterial' for n in group.nodes):
                previous = output.inputs['Geometry'].links[0].from_socket
                setmat = group.nodes.new('GeometryNodeSetMaterial')
                setmat.inputs['Material'].default_value = material
                group.links.new(previous, setmat.inputs['Geometry'])
                group.links.new(setmat.outputs['Geometry'], output.inputs['Geometry'])
            if kind == 'Sweep Profile':
                profile = park.sweep_profile.create_profile(bpy.context, obj)
                for socket in mod.node_group.interface.items_tree:
                    if socket.item_type == 'SOCKET' and socket.in_out == 'INPUT' and socket.name == 'Profile Curve':
                        getattr(mod.properties.inputs, socket.identifier).value = profile
            obj.update_tag()
        return obj

    supported = [curve('Cube.003', 'Renderable Spline UV'),
                 curve('Tube', 'Renderable Tube'), curve('Sweep', 'Sweep Profile')]
    disabled_view = curve('DisabledView', 'Renderable Tube')
    disabled_view.modifiers[0].show_viewport = False
    disabled_render = curve('DisabledRender', 'Renderable Spline UV')
    disabled_render.modifiers[0].show_render = False
    plain = curve('PlainCurve')
    generic = curve('GenericGN', 'Renderable Tube')
    generic.modifiers[0].node_group = generic.modifiers[0].node_group.copy()
    generic.modifiers[0].node_group.name = 'Unrelated Tube Generator'
    spoof = curve('NameOnly', 'Renderable Tube')
    spoof.modifiers[0].node_group = spoof.modifiers[0].node_group.copy()
    del spoof.modifiers[0].node_group['park_spline_version']
    mixed = curve('MixedHair', 'Renderable Tube')
    mixed.modifiers.new('Hair setup', 'NODES').node_group = bpy.data.node_groups.new(
        'Hair_System_Setup_QA', 'GeometryNodeTree')
    hairdata = bpy.data.hair_curves.new('HairToolCurves')
    hair = bpy.data.objects.new('HairToolCurves', hairdata)
    collection.objects.link(hair)
    hair.modifiers.new('PARK name on Hair Curves', 'NODES').node_group = supported[0].modifiers[0].node_group
    hidden = curve('HiddenPark', 'Renderable Tube')
    hidden.hide_set(True)
    nested = curve('NestedOnly', 'Renderable Tube')
    working = bpy.data.collections.new('Working')
    collection.children.link(working)
    working.objects.link(nested)
    collection.objects.unlink(nested)
    rejected = [disabled_view, disabled_render, plain, generic, spoof, mixed, hair]
    assert all(patch.has_enabled_renderer(obj) for obj in supported)
    assert not any(patch.has_enabled_renderer(obj) for obj in rejected)
    report['scope_exclusions'] = [obj.name for obj in rejected] + [hidden.name, nested.name]
    bpy.context.view_layer.update()
    before = scene_state()
    mesh_count = len(bpy.data.meshes)
    expected = {}
    for obj in supported:
        mesh = patch._capture_mesh(obj, bpy.context.evaluated_depsgraph_get())
        expected[obj.name] = mesh_info(mesh)
        assert len(mesh.polygons) > 0, obj.name
        bpy.data.meshes.remove(mesh)
    report['expected'] = expected
    assert patch.prepare(props) == 3
    for row in patch._prepared:
        assert mesh_info(row['proxy'].data) == expected[row['name']]
        assert row['proxy'].parent == parent
        assert row['proxy'].matrix_world == row['source'].matrix_world
    assert len(utilities.get_from_collection('MESH')) == 3
    patch.cleanup()
    assert scene_state() == before and len(bpy.data.meshes) == mesh_count
    report['normal_cleanup'] = True

    # Partial failure must roll back both the completed and failing sources.
    capture = patch._capture_mesh
    calls = [0]
    def fail_second(*args):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError('injected capture failure')
        return capture(*args)
    patch._capture_mesh = fail_second
    try:
        try:
            patch.prepare(props)
            raise AssertionError('Expected capture failure')
        except RuntimeError as error:
            assert 'injected capture failure' in str(error)
    finally:
        patch._capture_mesh = capture
    assert scene_state() == before and len(bpy.data.meshes) == mesh_count
    report['partial_failure_cleanup'] = True

    parent['send2ue_export_source_names'] = json.dumps([supported[0].name])
    export_selection.prepare()
    assert patch.prepare(props) == 1
    assert len(utilities.get_from_collection('MESH')) == 1
    patch.cleanup()
    export_selection.cleanup()
    del parent['send2ue_export_source_names']
    assert scene_state() == before
    report['explicit_source_selection'] = True

    # Remove only the fixture's unsupported systems before native validation.
    for obj in rejected + [hidden, nested]:
        bpy.data.objects.remove(obj, do_unlink=True)
    before = scene_state()
    context_before = utilities.get_current_context()
    from send2ue.core import export
    export_fbx = export.export_fbx_file
    def fail_export(*args, **kwargs):
        raise RuntimeError('injected FBX failure')
    export.export_fbx_file = fail_export
    try:
        try:
            bpy.ops.wm.send2ue('EXEC_DEFAULT')
            raise AssertionError('Expected native export failure')
        except RuntimeError as error:
            assert 'injected FBX failure' in str(error)
    finally:
        export.export_fbx_file = export_fbx
    assert scene_state() == before and not patch._prepared
    assert utilities.get_current_context() == context_before
    report['native_export_failure_cleanup'] = True
    result = bpy.ops.wm.send2ue('EXEC_DEFAULT')
    assert result == {'FINISHED'}, result
    assert scene_state() == before and not patch._prepared
    assert utilities.get_current_context() == context_before
    report['native_operator_cleanup'] = True
    paths = list(OUT.glob('*.fbx'))
    assert len(paths) == 1 and paths[0].stem == parent.name, paths
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    material.name = 'OriginalMaterialBeforeRoundtrip'
    bpy.ops.import_scene.fbx(filepath=str(paths[0]))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    report['fbx_meshes'] = {obj.name: mesh_info(obj.data) for obj in meshes}
    assert len(meshes) == 3, report['fbx_meshes']
    assert sum(len(obj.data.polygons) for obj in meshes) == sum(x['polygons'] for x in expected.values())
    assert any(obj.data.uv_layers for obj in meshes)
    assert all('M_ParkSplineQA' in [m.name for m in obj.data.materials if m] for obj in meshes)
    for name, original in expected.items():
        actual = report['fbx_meshes'][name.replace('.', '_')]
        assert actual['uvs'] == original['uvs'], (name, actual['uvs'], original['uvs'])
    paths[0].unlink()  # Consumed roundtrip output; keep only the small receipt.
    report['status'] = 'passed'
except BaseException:
    report['status'] = 'failed'
    report['error'] = traceback.format_exc()
finally:
    (OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('PARK_SPLINE_QA=' + json.dumps(report), flush=True)
if report['status'] != 'passed':
    raise RuntimeError(report['error'])
