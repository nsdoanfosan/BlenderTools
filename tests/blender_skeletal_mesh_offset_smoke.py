"""Export real FBX and inspect the node transforms Unreal reads for vertices.

Factory startup QA only; no blend or preferences are saved.
"""
import sys
from pathlib import Path
import bpy, addon_utils
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/addons'))
addon_utils.enable('send2ue',default_set=False)
from send2ue.core.export import export_fbx_file
from io_scene_fbx import parse_fbx
path=Path(sys.argv[sys.argv.index('--')+1])
for ob in list(bpy.data.objects):bpy.data.objects.remove(ob,do_unlink=True)
bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
bpy.context.scene.send2ue.use_object_origin=True
data=bpy.data.armatures.new('Rig');rig=bpy.data.objects.new('Armature',data);bpy.context.scene.collection.objects.link(rig)
rig.select_set(True);bpy.context.view_layer.objects.active=rig;bpy.ops.object.mode_set(mode='EDIT')
bone=data.edit_bones.new('anchor');bone.head=(0,0,0);bone.tail=(0,0,.1);bpy.ops.object.mode_set(mode='OBJECT')
for name,offset in [('OffsetMesh',(0,0,-1)),('OriginMesh',(0,0,0))]:
 md=bpy.data.meshes.new(name);md.from_pydata([(0,0,1),(.1,0,1),(0,.1,1)],[],[(0,1,2)])
 ob=bpy.data.objects.new(name,md);bpy.context.scene.collection.objects.link(ob);ob.parent=rig;ob.location=offset
 ob.vertex_groups.new(name='anchor').add([0,1,2],1,'REPLACE');ob.modifiers.new('Rig','ARMATURE').object=rig;ob.select_set(True)
bpy.context.window_manager.send2ue.asset_id='offset_test'
bpy.context.window_manager.send2ue.asset_data.update({'offset_test':{'_asset_type':'SkeletalMesh'}})
export_fbx_file(str(path),dict(global_scale=1,apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',axis_forward='Y',axis_up='Z',bake_space_transform=False,bake_anim=False,add_leaf_bones=False,mesh_smooth_type='FACE'))
tree,_=parse_fbx.parse(str(path));models=next(e for e in tree.elems if e.id==b'Objects')
translations={}
for model in models.elems:
 if model.id!=b'Model' or model.props[-1]!=b'Mesh':continue
 pp=next(e for e in model.elems if e.id==b'Properties70')
 translations[model.props[1].split(b'\x00')[0].decode()]=next((p.props[4:] for p in pp.elems if p.props[0]==b'Lcl Translation'),[0,0,0])
assert abs(translations['OffsetMesh'][2]+100)<1e-6,translations
assert max(abs(x) for x in translations['OriginMesh'])<1e-6,translations
print('SKELETAL_MESH_OFFSET_PASS',translations)
