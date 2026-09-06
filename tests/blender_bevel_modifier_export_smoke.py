"""Run only in a disposable background Blender; exports and reimports actual FBX."""

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import bpy

repo=Path(__file__).resolve().parents[1]
source=repo/'src/addons/send2ue/core/bevel_modifier_export.py'
spec=importlib.util.spec_from_file_location('review_bevel',source)
helper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
temporary_output=tempfile.TemporaryDirectory(prefix='send2ue-bevel-test-')
out=Path(temporary_output.name)

def requested(enabled=None, viewport=True, render=True, static=True):
    props=SimpleNamespace(blender=SimpleNamespace(export_method=SimpleNamespace(fbx=SimpleNamespace(geometry=SimpleNamespace(use_mesh_modifiers=viewport,use_mesh_modifiers_render=render)))))
    if enabled is not None:
        props.export_render_bevels=enabled
    return helper.requested_for_export(props,is_static_mesh=static)

gates={
    'missing_option':requested(),
    'default_off':requested(False),
    'skeletal':requested(True,static=False),
    'viewport_modifiers_off':requested(True,viewport=False),
    'render_modifiers_off':requested(True,render=False),
    'explicit_static_opt_in':requested(True),
}
assert gates=={'missing_option':False,'default_off':False,'skeletal':False,'viewport_modifiers_off':False,'render_modifiers_off':False,'explicit_static_opt_in':True}
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj,do_unlink=True)

def cube(name, render):
    bpy.ops.mesh.primitive_cube_add()
    obj=bpy.context.object
    obj.name=name
    mod=obj.modifiers.new('ReviewBevel','BEVEL')
    mod.width=0.1
    mod.segments=2
    mod.show_viewport=False
    mod.show_render=render
    return obj,mod

objects=[cube('OrdinaryRenderBevel',True),cube('FullyDisabledBevel',False),cube('UCX_Collision',True)]

def count(obj):
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh=evaluated.to_mesh()
    try:return len(mesh.vertices)
    finally:evaluated.to_mesh_clear()

def export_case(label,enabled):
    bpy.ops.object.select_all(action='DESELECT')
    for obj,_ in objects:obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[0][0]
    baseline={obj.name:count(obj) for obj,_ in objects}
    with helper.enabled_for_export([obj for obj,_ in objects],update=bpy.context.evaluated_depsgraph_get().update,enabled=enabled):
        evaluated={obj.name:count(obj) for obj,_ in objects}
        bpy.ops.export_scene.fbx(filepath=str(out/(label+'.fbx')),use_selection=True,object_types={'MESH'},use_mesh_modifiers=True,use_mesh_modifiers_render=True,bake_anim=False)
    assert all(not mod.show_viewport for _,mod in objects)
    assert {obj.name:count(obj) for obj,_ in objects}==baseline
    before=set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(out/(label+'.fbx')),use_anim=False)
    imported=[obj for obj in set(bpy.data.objects)-before if obj.type=='MESH']
    imported_counts={obj.name.rsplit('.',1)[0]:len(obj.data.vertices) for obj in imported}
    assert imported_counts==evaluated,(imported_counts,evaluated)
    for obj in imported:bpy.data.objects.remove(obj,do_unlink=True)
    return {'baseline':baseline,'evaluated':evaluated,'fbx_imported':imported_counts,'restored':True}

cases={'default_off':export_case('default_off',False),'opt_in':export_case('opt_in',True)}
assert cases['default_off']['evaluated']=={'OrdinaryRenderBevel':8,'FullyDisabledBevel':8,'UCX_Collision':8}
assert cases['opt_in']['evaluated']['OrdinaryRenderBevel']>8
assert cases['opt_in']['evaluated']['FullyDisabledBevel']==8
assert cases['opt_in']['evaluated']['UCX_Collision']==8
try:
    with helper.enabled_for_export([obj for obj,_ in objects],update=bpy.context.evaluated_depsgraph_get().update,enabled=True):
        raise RuntimeError('injected export failure')
except RuntimeError:
    pass
assert all(not mod.show_viewport for _,mod in objects)
receipt={'helper_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'gates':gates,'cases':cases,'exception_restoration':True,'prefs_saved':False,'gui_accessed':False}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print('BEVEL_REVIEW='+json.dumps(receipt))

temporary_output.cleanup()
