"""Native nested edit, edited mesh contents, and failure restoration regression.

blender --factory-startup --background --python-exit-code 1 --python <script> -- <blend>
No preferences or source blend files are saved.
"""
import addon_utils, bpy, sys, json
from pathlib import Path
from mathutils import Matrix
sys.path.insert(0,'C:/Users/PARK/AppData/Roaming/Blender Foundation/Blender/5.2/extensions/user_default')
bpy.ops.wm.open_mainfile(filepath=sys.argv[sys.argv.index('--')+1])
assert addon_utils.enable('GroupPro',default_set=False)
import GroupPro
for cls in GroupPro.auto_load.ordered_classes:
    try: bpy.utils.register_class(cls)
    except ValueError: pass
GroupPro.gp_props.register_props()
# An in-memory preference entry is needed by the native GroupPro helpers. It is
# deliberately not persisted and addon registration still uses default_set=False.
entry=bpy.context.preferences.addons.new();entry.module='GroupPro'
prefs=entry.preferences
prefs.auto_link_modifier_objs=False
prefs.auto_link_children=False
prefs.auto_manage_ignored_realize_inst=False
GroupPro.helpers_group.get_addon_preferences=lambda:prefs
GroupPro.group_pro.get_addon_preferences=lambda:prefs
assert addon_utils.enable('send2ue',default_set=False)
from send2ue.core import grouppro_export, grouppro_edit_export
from GroupPro import helpers_group as gp

for o in list(bpy.context.scene.collection.objects):
    bpy.context.scene.collection.objects.unlink(o)
for c in list(bpy.context.scene.collection.children):
    bpy.context.scene.collection.children.unlink(c)
old_export=bpy.data.collections.get('Export')
if old_export:old_export.name='Export_not_in_test_scene'
export=bpy.data.collections.new('Export');bpy.context.scene.collection.children.link(export)
inner=bpy.data.collections.new('FixtureInnerSource')
mesh=bpy.data.meshes.new('FixtureMesh')
mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(0,0,1)],[],[(0,2,1),(0,1,3),(1,2,3),(2,0,3)])
source=bpy.data.objects.new('FixtureSource',mesh);inner.objects.link(source)
child=gp.create_empty_group_obj('FixtureInner',inner)
gp.get_any_group_mod(child).properties.inputs[gp.realize_inst_input]['value']=True
from GroupPro import gpro_empty_source
gpro_empty_source.ensure_modifier(child,inner)
outer=bpy.data.collections.new('FixtureOuterSource');outer.objects.link(child)
child.location=(2,3,1)
root=gp.create_empty_group_obj('FixtureOuter',outer);export.objects.link(root)
gp.get_any_group_mod(root).properties.inputs[gp.realize_inst_input]['value']=True
gpro_empty_source.ensure_modifier(root,outer)
root.location=(5,6,7);root.rotation_euler=(.2,-.1,.4);root.scale=(-1,1.2,.7)
props=bpy.context.scene.send2ue
props.import_meshes=True
def snapshot():
    return {'objects':{o.as_pointer():(o.name,tuple(o.location),tuple(o.rotation_euler),tuple(o.rotation_quaternion),tuple(o.scale),o.rotation_mode,o.parent.as_pointer() if o.parent else 0,tuple(tuple(r) for r in o.matrix_parent_inverse),tuple(sorted(c.as_pointer() for c in o.users_collection)),o.hide_viewport) for o in bpy.data.objects},
            'collections':{c.as_pointer():(tuple(sorted(o.as_pointer() for o in c.objects)),tuple(sorted(x.as_pointer() for x in c.children)),tuple(c.instance_offset)) for c in bpy.data.collections},
            'edit_stack':[s.currentEmptyName for s in bpy.context.scene.storedGroupSettings],
            'vertices':[tuple(v.co) for v in source.data.vertices]}
def geom():
    bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get();points=[]
    for inst in dg.object_instances:
        original=getattr(inst.object,'original',None)
        parent=getattr(getattr(inst,'parent',None),'original',None)
        if inst.object.type=='MESH' and (original==root or parent==root):
            points += [tuple(inst.matrix_world @ v.co) for v in inst.object.data.vertices]
    return sorted(points)
closed=geom()
GroupPro.group_pro.GP_OT_GroupProEdit.edit_group(bpy.context,root)
GroupPro.group_pro.GP_OT_GroupProEdit.edit_group(bpy.context,child)
source.data.vertices[0].co.x+=.125 # Keep a real geometry edit across the send.
bpy.context.view_layer.update()
for o in bpy.context.selected_objects:o.select_set(False)
source.select_set(True);bpy.context.view_layer.objects.active=source
bpy.ops.object.mode_set(mode='EDIT')
before=snapshot()
grouppro_export.prepare(props)
assert bpy.data.objects.get('FixtureOuter').type=='MESH'
assert len(bpy.data.objects['FixtureOuter'].evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)==4
proxy=bpy.data.objects['FixtureOuter']
prepared_points=sorted(tuple(proxy.matrix_world @ v.co) for v in proxy.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
grouppro_export.cleanup()
assert before==snapshot(),'Nested open edit state changed'
assert bpy.context.mode=='EDIT_MESH' and bpy.context.object==source and source.select_get()
copy=gp.copy_modifiers
def fail(*args,**kwargs):raise RuntimeError('Injected export failure')
gp.copy_modifiers=fail
try:
    try:grouppro_export.prepare(props)
    except RuntimeError as e:assert str(e)=='Injected export failure'
    else:raise AssertionError('Expected injected failure')
finally:
    gp.copy_modifiers=copy
    grouppro_export.cleanup()
assert before==snapshot(),'Failure changed the editable scene'
assert not grouppro_export._prepared and grouppro_edit_export._state is None
assert bpy.context.mode=='EDIT_MESH' and bpy.context.object==source and source.select_get()
from send2ue.core import export as send_export
send_original=send_export.send2ue
props.path_mode='send_to_disk'
def fail_export(*args,**kwargs):raise RuntimeError('Injected operator export failure')
send_export.send2ue=fail_export
try:
    try:bpy.ops.wm.send2ue('EXEC_DEFAULT')
    except RuntimeError as e:assert 'Injected operator export failure' in str(e)
    else:raise AssertionError('Expected operator failure')
finally:
    send_export.send2ue=send_original
assert before==snapshot(),'Operator failure changed group edit state'
assert bpy.context.mode=='EDIT_MESH' and bpy.context.object==source and source.select_get()
assert not grouppro_export._prepared and grouppro_edit_export._state is None
# Compare with the provider's real Close Group result, including the changed
# source vertex and the nested, mirrored/non-uniform group transforms.
for _ in range(2):
    bpy.ops.object.close_grouppro('EXEC_DEFAULT')
native_points=geom()
assert len(native_points)==len(prepared_points)==4,(native_points,prepared_points)
assert max(abs(a-b) for x,y in zip(native_points,prepared_points) for a,b in zip(x,y))<1e-5
print('OPEN_GROUP_EXPORT_PASS '+json.dumps({'nested_groups':2,'source_mesh_edit_preserved':True,'state_restored':True,'failure_restored':True}))
