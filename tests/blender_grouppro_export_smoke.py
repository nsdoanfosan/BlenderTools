"""Native geometry equivalence, rollback and FBX roundtrip for GroupPro Empties."""
import addon_utils
import bpy
import hashlib
import json
from pathlib import Path
import sys
import traceback
import numpy as np

# Run in disposable Blender memory, with a real GroupPro source fixture:
# blender --factory-startup --background --python <this file> --
#   <source.blend> <output-directory> <GroupPro-parent-directory>
# User preferences and source blend files are never saved.
args=sys.argv[sys.argv.index('--')+1:]
SOURCE=Path(args[0]).resolve()
OUT=Path(args[1]).resolve()
OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(args[2]).resolve()))
report = {}
def signature(mesh):
    coords=np.empty(len(mesh.vertices)*3,dtype=np.float32)
    mesh.vertices.foreach_get('co',coords)
    return {'vertices':len(mesh.vertices),'polygons':len(mesh.polygons),
            'custom_normals':mesh.has_custom_normals,
            'coords':hashlib.sha256(coords.tobytes()).hexdigest(),
            'uvs':[u.name for u in mesh.uv_layers],
            'colors':[a.name for a in mesh.color_attributes],
            'materials':[m.name if m else None for m in mesh.materials]}
def state():
    return {o.name:{'type':o.type,'parent':o.parent.name if o.parent else None,
        'matrix':[list(r) for r in o.matrix_world], 'collections':sorted(c.name for c in o.users_collection),
        'hidden':o.hide_get() if o.name in bpy.context.view_layer.objects else None,
        'modifiers':[(m.name,m.type,m.show_viewport,m.show_render) for m in o.modifiers]}
        for o in bpy.data.objects}
def geometry(objects):
    dg=bpy.context.evaluated_depsgraph_get()
    dg.update()
    rows={o.name:[] for o in objects}
    for o in objects:
        ev=o.evaluated_get(dg)
        if ev.type=='MESH' and len(ev.data.vertices): rows[o.name].append((ev.data,ev.matrix_world.copy()))
    for i in dg.object_instances:
        if i.is_instance and i.parent and i.parent.original in objects and i.object.type=='MESH':
            rows[i.parent.original.name].append((i.object.data,i.matrix_world.copy()))
    result={}
    for name, parts in rows.items():
        points=[]
        for mesh,matrix in parts:
            coords=np.empty(len(mesh.vertices)*3,dtype=np.float64)
            mesh.vertices.foreach_get('co',coords)
            mat=np.array(matrix)
            points.append(coords.reshape(-1,3) @ mat[:3,:3].T+mat[:3,3])
        result[name]={'vertices':sum(len(m.vertices) for m,t in parts),
          'polygons':sum(len(m.polygons) for m,t in parts),
          'points':np.sort(np.concatenate(points),axis=0) if points else np.empty((0,3)),
          'materials':sorted({m.name for mesh,t in parts for m in mesh.materials if m})}
    return result
try:
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE))
    for name in ['GroupPro','ue_unique_export_names_addon','send2ue','linked_opening_assembly']:
        assert addon_utils.enable(name,default_set=False),name
    from send2ue.core import grouppro_export as gp_export, preview_modifier_guard
    props=bpy.context.scene.send2ue
    props.path_mode='send_to_disk'
    props.disk_mesh_folder_path=str(OUT)
    props.disk_animation_folder_path=str(OUT)
    props.disk_groom_folder_path=str(OUT)
    bpy.context.scene.ue_unique_names.texture_export_dir=str(OUT/'textures')
    before=state()
    # Open/nested GroupPro edit sessions have their own native regression in
    # blender_grouppro_open_export.py, including failure and Edit Mode restore.
    preview_modifier_guard.prepare()
    dg=bpy.context.evaluated_depsgraph_get()
    dg.update()
    sources={o for o in bpy.data.collections['Export'].all_objects if o.type=='EMPTY' and o.instance_collection and o.modifiers.get('GPro_RealizeAndProxy') and o.visible_get()}
    expected=geometry(sources)
    gp_export.prepare(props)
    dg=bpy.context.evaluated_depsgraph_get()
    proxy_geom=geometry({row['proxy'] for row in gp_export._prepared})
    actual={row['name']:proxy_geom[row['proxy'].name] for row in gp_export._prepared}
    report['expected']={k:{a:b for a,b in v.items() if a!='points'} for k,v in expected.items()}
    report['prepared']={k:{a:b for a,b in v.items() if a!='points'} for k,v in actual.items()}
    report['geometry_equal']={name:expected[name]['vertices']==value['vertices'] and expected[name]['polygons']==value['polygons'] and expected[name]['materials']==value['materials'] and bool(np.allclose(expected[name]['points'],value['points'],atol=1e-4,rtol=0)) for name,value in actual.items()}
    report['prepared_count']=len(actual)
    gp_export.cleanup()
    preview_modifier_guard.cleanup()
    report['cleanup_restores_scene']=state()==before
    assert report['cleanup_restores_scene']
    assert report['prepared_count']>0
    assert all(report['geometry_equal'].values()),report['geometry_equal']
    provider=gp_export._provider()
    copy_modifiers=provider.copy_modifiers
    calls=[0]
    def fail_copy(*args,**kwargs):
        calls[0]+=1
        if calls[0]==2:
            raise RuntimeError('injected GroupPro preparation failure')
        return copy_modifiers(*args,**kwargs)
    if len(sources)>1:
        provider.copy_modifiers=fail_copy
        try:
            try:
                gp_export.prepare(props)
                raise AssertionError('Expected preparation failure')
            except RuntimeError as error:
                assert 'injected GroupPro' in str(error)
        finally:
            provider.copy_modifiers=copy_modifiers
        assert state()==before and not gp_export._prepared
        report['partial_failure_restores_scene']=True
    print('GROUP_PREP='+json.dumps({k:report[k] for k in ['prepared_count','geometry_equal','cleanup_restores_scene']}),flush=True)
    # Exercise the ordinary Send2UE operator, including validation, material
    # discovery and final FBX selection, then independently read the resulting FBX.
    result=bpy.ops.wm.send2ue('EXEC_DEFAULT')
    report['operator']=sorted(result)
    report['operator_restores_scene']=state()==before
    assert result=={'FINISHED'}
    assert report['operator_restores_scene']
    report['asset_names']=[a.get('asset_path') for a in bpy.context.window_manager.send2ue.asset_data.values()]
    files=list(OUT.glob('*.fbx'))
    assert len(files)==1,files
    path=files[0]
    assert path.is_file()
    report['fbx_bytes']=path.stat().st_size
    for o in list(bpy.data.objects): bpy.data.objects.remove(o,do_unlink=True)
    bpy.ops.import_scene.fbx(filepath=str(path))
    report['fbx_meshes']={o.name:signature(o.data) for o in bpy.context.scene.objects if o.type=='MESH'}
    for name in actual:
        assert name.replace('.','_') in report['fbx_meshes'],name
    if 'Roof_Main_01' in report['fbx_meshes']:
        assert report['fbx_meshes']['Roof_Main_01']['custom_normals']
    if any('house_Cliff_ornament_01_low' == o.name for o in bpy.context.scene.objects):
        assert report['fbx_meshes']['house_Cliff_ornament_01_low']['vertices']>0
    report['status']='passed'
except BaseException:
    report['status']='failed'
    report['error']=traceback.format_exc()
finally:
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('HOUSE_QA='+json.dumps({k:v for k,v in report.items() if k not in ['expected','prepared','fbx_meshes']}),flush=True)

if report.get('status')!='passed':
    raise RuntimeError(report.get('error','GroupPro native regression failed'))
