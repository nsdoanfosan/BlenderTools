"""Disposable Blender native integration test; UEUN_REPO optionally selects UE Unique Names."""
import bpy,addon_utils,hashlib,json,os,sys,tempfile,traceback
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
temporary_output=tempfile.TemporaryDirectory(prefix='send2ue-prototype-test-')
out=Path(temporary_output.name)
if os.environ.get('UEUN_REPO'):
    sys.path.insert(0, os.environ['UEUN_REPO'])
sys.path.insert(0,str(repo/'src/addons'))
for mod in ('ue_unique_export_names_addon','send2ue'):
    bpy.context.preferences.addons.new().module=mod
    assert addon_utils.enable(mod,default_set=False)
from send2ue.core import utilities
utilities.setup_project()
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
export=bpy.data.collections.get('Export') or bpy.data.collections.new('Export')
if export.name not in bpy.context.scene.collection.children:bpy.context.scene.collection.children.link(export)
def empty(name,parent=None):
    o=bpy.data.objects.new(name,None);export.objects.link(o);o.parent=parent;return o
def cube(name,parent,pos):
    bpy.ops.mesh.primitive_cube_add(location=pos)
    o=bpy.context.object;o.name=name
    for c in list(o.users_collection):c.objects.unlink(o)
    export.objects.link(o);o.parent=parent;o.data.materials.append(mat);return o
mat=bpy.data.materials.new('M_glass');mat.use_nodes=True;mat.surface_render_method='BLENDED'
parent=empty('proto_parent');child=empty('proto_child',parent)
a=cube('frame_a',parent,(0,0,0));b=cube('frame_b',parent,(3,0,0));c=cube('glass',child,(0,3,0))
fixture=json.loads((repo/'tests/fixtures/prototype_identity_v1.json').read_text())
a['speedtree_cluster_prototype_identity']=json.dumps(fixture['single_member_lineage'])
a['speedtree_cluster_prototype_identity_members']=json.dumps([fixture['identity']])
p=bpy.context.scene.send2ue
p.path_mode='send_to_disk';p.import_animations=False;p.export_all_actions=False
p.disk_mesh_folder_path=p.disk_animation_folder_path=p.disk_groom_folder_path=str(out)
p.extensions.combine_assets.combine='child_meshes'
bpy.context.scene.ue_unique_names.texture_export_dir=str(out)
report={}
try:
    report['result']=list(bpy.ops.wm.send2ue('EXEC_DEFAULT'))
    report['assets']=dict(bpy.context.window_manager.send2ue.asset_data)
    report['sidecars']={}
    for asset in report['assets'].values():
        path=Path(asset['_material_pipeline_json_path'])
        payload=path.read_bytes();data=json.loads(payload)
        report['sidecars'][path.name]={'prototype_handoff':data.get('speedtree_prototype_handoff'),'stored_hash':asset['_material_pipeline_json_sha256'],'current_hash':hashlib.sha256(payload).hexdigest()}
    report['pass']=report['result']==['FINISHED'] and len(report['assets'])==2 and all(v['stored_hash']==v['current_hash'] for v in report['sidecars'].values()) and any(v['prototype_handoff'] for v in report['sidecars'].values())
    assert report['pass']
    first_handoff=report['sidecars']['proto_parent.json']['prototype_handoff']
    assert report['sidecars']['proto_child.json']['prototype_handoff'] is None
    bpy.data.objects['frame_a'].data.vertices[0].co.x+=0.25
    report['repeat_result']=list(bpy.ops.wm.send2ue('EXEC_DEFAULT'))
    report['repeat_sidecars']={}
    for asset in dict(bpy.context.window_manager.send2ue.asset_data).values():
        path=Path(asset['_material_pipeline_json_path'])
        payload=path.read_bytes();data=json.loads(payload)
        report['repeat_sidecars'][path.name]={'prototype_handoff':data.get('speedtree_prototype_handoff'),'stored_hash':asset['_material_pipeline_json_sha256'],'current_hash':hashlib.sha256(payload).hexdigest()}
    second_handoff=report['repeat_sidecars']['proto_parent.json']['prototype_handoff']
    report['repeat_fresh_payload']=first_handoff['output_content']!=second_handoff['output_content'] and first_handoff['blender_geometry_content']!=second_handoff['blender_geometry_content']
    report['pass']=report['pass'] and report['repeat_result']==['FINISHED'] and report['repeat_fresh_payload'] and report['repeat_sidecars']['proto_child.json']['prototype_handoff'] is None and all(v['stored_hash']==v['current_hash'] for v in report['repeat_sidecars'].values())
except Exception:
    report['error']=traceback.format_exc();report['pass']=False
(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('PROTOTYPE_NATIVE='+json.dumps(report),flush=True)

assert report['pass'], report.get('error', report)
temporary_output.cleanup()
