"""Background Blender regression: GP_Mirror followed by a realizing modifier.

Run with Blender --background --python this_file. No preferences or blend saved.
"""
import json
import tempfile
from pathlib import Path
import bpy
from send2ue.core import export

assert bpy.app.background
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
group = export.new_geometry_node_group('GP_Mirror')
inp = group.nodes.new('NodeGroupInput')
inst = group.nodes.new('GeometryNodeGeometryToInstance')
transform = group.nodes.new('GeometryNodeTransform')
transform.inputs['Scale'].default_value = (-1, 1, 1)
transform.inputs['Translation'].default_value = (3, 0, 0)
join = group.nodes.new('GeometryNodeJoinGeometry')
out = group.nodes.new('NodeGroupOutput')
for a,b in [(inp.outputs[0],inst.inputs[0]),(inst.outputs[0],transform.inputs['Geometry']),
            (transform.outputs[0],join.inputs[0]),(inp.outputs[0],join.inputs[0]),
            (join.outputs[0],out.inputs[0])]:
    group.links.new(a,b)
obj.modifiers.new('Mirror','NODES').node_group = group
ffd = export.new_geometry_node_group('FFD realizing input')
a=ffd.nodes.new('NodeGroupInput');b=ffd.nodes.new('GeometryNodeRealizeInstances');c=ffd.nodes.new('NodeGroupOutput')
ffd.links.new(a.outputs[0],b.inputs[0]);ffd.links.new(b.outputs[0],c.inputs[0])
consumer=obj.modifiers.new('FFD','NODES');consumer.node_group=ffd

def measure(target):
    bpy.context.view_layer.update()
    ev=target.evaluated_get(bpy.context.evaluated_depsgraph_get());me=ev.to_mesh()
    try:
        me.calc_loop_triangles()
        volume=sum(me.vertices[t.vertices[0]].co.dot(me.vertices[t.vertices[1]].co.cross(me.vertices[t.vertices[2]].co))/6 for t in me.loop_triangles)
        return len(me.vertices),volume
    finally:ev.to_mesh_clear()

original=list(obj.modifiers.keys())
assert abs(measure(obj)[1])<1e-5,measure(obj)
rows=[]
for enabled in (True,False):
    consumer.show_viewport=enabled
    with export.realize_selected_geometry_node_instances():
        v=measure(obj);assert v[0]==16 and abs(v[1]-16)<1e-5,v
        rows.append({'downstream_realizes':enabled,'measurement':v})
    assert list(obj.modifiers.keys())==original
consumer.show_viewport=True
try:
    with export.realize_selected_geometry_node_instances():
        raise RuntimeError('cleanup probe')
except RuntimeError:pass
assert list(obj.modifiers.keys())==original
with tempfile.TemporaryDirectory(prefix='send2ue-gp-mirror-') as folder:
    path=str(Path(folder)/'mirror.fbx')
    with export.realize_selected_geometry_node_instances():
        bpy.ops.export_scene.fbx(filepath=path,use_selection=True,add_leaf_bones=False)
    bpy.data.objects.remove(obj,do_unlink=True)
    bpy.ops.import_scene.fbx(filepath=path)
    imported=next(o for o in bpy.context.selected_objects if o.type=='MESH')
    v=measure(imported);assert v[0]==16 and abs(v[1]-16)<1e-4,v
print('GP_MIRROR_EXPORT_SMOKE_PASS',json.dumps({'cases':rows,'fbx_roundtrip':v,'cleanup':True}))
