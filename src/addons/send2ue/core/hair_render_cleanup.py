"""Opt-in cleanup of unrenderable triangles on disposable cloth render copies."""
from collections import Counter
import math
import struct


def export_point(point, position_scale):
    """Match an explicitly declared float32 export-position precision."""
    return tuple(struct.unpack('f', struct.pack('f', float(x) * position_scale))[0] for x in point)


def _value(item):
    for field in ('color', 'vector'):
        if hasattr(item, field):
            return tuple(getattr(item, field))
    value = item.value
    return value if isinstance(value, (bool, int, float, str)) else tuple(value)


def _collapsed(mesh, triangle, scale):
    return triangle.area == 0.0 or (scale is not None and
        len({export_point(mesh.vertices[i].co, scale) for i in triangle.vertices}) < 3)


def _surface(mesh, scale):
    """Compare winding, retained positions, every corner/face attribute and shading."""
    mesh.calc_loop_triangles()
    corner = sorted((a.name, a) for a in mesh.attributes if a.domain == 'CORNER' and not a.name.startswith('.'))
    # Blender may omit an all-default built-in layer after BMesh conversion.
    # Compare its actual material/smooth values below, independent of storage.
    face = sorted((a.name, a) for a in mesh.attributes if a.domain == 'FACE' and
                  not a.name.startswith('.') and a.name not in {'material_index', 'sharp_face'})
    rows = []
    for triangle in mesh.loop_triangles:
        if _collapsed(mesh, triangle, scale):
            continue
        corners = [(tuple(mesh.vertices[mesh.loops[i].vertex_index].co),
                    tuple((name, _value(a.data[i])) for name, a in corner),
                    tuple(mesh.corner_normals[i].vector) if mesh.has_custom_normals else None)
                   for i in triangle.loops]
        winding = min(tuple(corners[i:] + corners[:i]) for i in range(3))
        polygon = mesh.polygons[triangle.polygon_index]
        rows.append((winding, polygon.material_index, polygon.use_smooth,
                     tuple((name, _value(a.data[triangle.polygon_index])) for name, a in face)))
    return Counter(rows)


def clean(mesh, position_scale=None):
    """Remove exact-zero faces, or duplicate corners at declared target precision.

    No distance threshold, welding, resampling, position, UV or weight edits occur.
    Newly orphaned vertices belonging to the removed triangles are also removed.
    A missing scale only enables exact-zero cleanup. The caller supplies the real
    export conversion; an arbitrary unit conversion is never inferred here.
    """
    if position_scale is not None and (isinstance(position_scale, bool) or
            not isinstance(position_scale, (float, int)) or not math.isfinite(position_scale) or position_scale <= 0):
        raise ValueError('Render cleanup position scale must be a finite positive number')
    mesh.calc_loop_triangles()
    bad = [t for t in mesh.loop_triangles if _collapsed(mesh, t, position_scale)]
    receipt = {'policy': 'remove_zero_or_declared_float32_duplicate_corner_triangles',
               'export_position_scale': position_scale,
               'source_vertices': len(mesh.vertices), 'source_triangles': len(mesh.loop_triangles),
               'removed_exact_zero_area_triangles': sum(t.area == 0.0 for t in bad),
               'removed_target_collapsed_triangles': sum(t.area > 0.0 for t in bad),
               'target_collapsed_source_area': sum(t.area for t in bad),
               'removed_orphan_vertices': 0}
    if not bad:
        receipt.update(export_vertices=len(mesh.vertices), export_triangles=len(mesh.loop_triangles),
                       retained_surface_attributes_and_shading_identical=True)
        return receipt
    if mesh.has_custom_normals:
        # BMesh re-encodes custom split normals in changed local corner bases.
        # Preserve artist data by rejecting this unsupported conversion.
        raise ValueError('Render cleanup does not support custom split normals')
    before = _surface(mesh, position_scale)
    if not before:
        raise ValueError('Render cleanup would remove the entire rendered surface')
    attrs = {a.name: [_value(v) for v in a.data] for a in mesh.attributes
             if a.domain == 'POINT' and not a.name.startswith('.')}
    point_id = '__send2ue_cleanup_point_id'
    if mesh.attributes.get(point_id):
        raise ValueError('Render cleanup temporary identity collides with an existing attribute')
    attr = mesh.attributes.new(point_id, 'INT', 'POINT')
    for i, datum in enumerate(attr.data):
        datum.value = i
    import bmesh
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method='FIXED', ngon_method='BEAUTY')
        faces = [f for f in bm.faces if f.calc_area() == 0.0 or (position_scale is not None and
                 len({export_point(v.co, position_scale) for v in f.verts}) < 3)]
        candidates = {v for f in faces for v in f.verts}
        bmesh.ops.delete(bm, geom=faces, context='FACES_ONLY')
        loose = [v for v in candidates if v.is_valid and not v.link_faces]
        receipt['removed_orphan_vertices'] = len(loose)
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    indices = [v.value for v in mesh.attributes[point_id].data]
    mesh.attributes.remove(mesh.attributes[point_id])
    if not all(all(_value(mesh.attributes[name].data[i]) == values[j] for i, j in enumerate(indices))
               for name, values in attrs.items()):
        raise ValueError('Render cleanup changed a retained point attribute')
    if _surface(mesh, position_scale) != before:
        raise ValueError('Render cleanup changed retained surface attributes or shading')
    if any(_collapsed(mesh, t, position_scale) for t in mesh.loop_triangles):
        raise ValueError('Render cleanup retained an unrenderable triangle')
    receipt.update(export_vertices=len(mesh.vertices), export_triangles=len(mesh.loop_triangles),
                   retained_surface_attributes_and_shading_identical=True)
    return receipt
