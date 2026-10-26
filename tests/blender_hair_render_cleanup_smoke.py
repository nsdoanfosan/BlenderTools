"""Disposable Blender geometry QA; never save production files or preferences."""
import sys
from pathlib import Path
import bpy, addon_utils
assert bpy.app.background
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/addons'))
assert addon_utils.enable('send2ue', default_set=False)
from send2ue.core import hair_render_cleanup as cleanup

mesh=bpy.data.meshes.new('ExactAndTargetCollapsedTips')
mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(0,1,0),
                 (-1.4022470712661743,-9.334400177001953,.993929386138916),
                 (-1.357017159461975,-9.603636741638184,.674537181854248),
                 (-1.3570172786712646,-9.603636741638184,.674537181854248),
                 (2,2,0),(3,2,0),(2,3,0)],[],[(0,1,2,3),(4,5,6),(7,8,9)])
mesh.update()
ids=mesh.attributes.new('source_identity','INT','POINT')
for i,v in enumerate(ids.data):v.value=i
weights=mesh.attributes.new('ChaosWeight','FLOAT_COLOR','POINT')
for i,v in enumerate(weights.data):v.color=(.2,float(i)/10,.4,1)
uv=mesh.attributes.new('UVMapGN','FLOAT2','CORNER')
for i,v in enumerate(uv.data):v.vector=(float(i)/20,float(i%3)/4)
owners=mesh.attributes.new('generator_owner','INT','FACE')
for i,v in enumerate(owners.data):v.value=i+11
baseline=mesh.copy()
exact=mesh.copy()
receipt=cleanup.clean(exact)
assert receipt['removed_exact_zero_area_triangles']==1 and receipt['removed_target_collapsed_triangles']==0,receipt
assert receipt['export_triangles']==3 and len(exact.vertices)==9
target=mesh.copy()
receipt=cleanup.clean(target,100.)
assert receipt['removed_exact_zero_area_triangles']==1 and receipt['removed_target_collapsed_triangles']==1,receipt
assert receipt['export_triangles']==2 and len(target.vertices)==6
assert receipt['retained_surface_attributes_and_shading_identical']
assert [v.value for v in target.attributes['source_identity'].data]==[0,1,2,7,8,9]
custom=mesh.copy()
custom.normals_split_custom_set([(0,0,1)]*len(custom.loops))
try:cleanup.clean(custom,100.)
except ValueError as error:assert 'custom split normals' in str(error)
else:raise AssertionError('Custom normals must remain unchanged and fail closed')
assert len(custom.vertices)==len(mesh.vertices) and len(custom.polygons)==len(mesh.polygons)
assert not any(a.name.startswith('__send2ue_cleanup') for a in target.attributes)
assert [tuple(v.co) for v in mesh.vertices]==[tuple(v.co) for v in baseline.vertices]
assert len(mesh.polygons)==len(baseline.polygons)==3
for scale in (True,0,-1,float('nan')):
    try:cleanup.clean(mesh,scale)
    except ValueError:pass
    else:raise AssertionError(scale)
empty=bpy.data.meshes.new('EntirelyCollapsed')
empty.from_pydata([(0,0,0),(0,0,0),(0,0,0)],[],[(0,1,2)])
try:cleanup.clean(empty)
except ValueError:pass
else:raise AssertionError('An entirely removed render must fail closed')
assert len(empty.vertices)==3 and len(empty.polygons)==1
print('HAIR_RENDER_CLEANUP_SMOKE PASSED: exact/target precision, retained UV/color/owner, original unchanged, custom normals and invalid inputs rejected')
