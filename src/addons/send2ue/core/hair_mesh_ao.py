"""Bake whole-mesh AO on disposable Hair Tool export meshes."""

from math import cos, pi, sin, sqrt

from mathutils.bvhtree import BVHTree


ATTRIBUTE = 'HairWholeMeshAO'
DEFAULT_DISTANCE = 0.011
SAMPLE_PAIRS = 12
HEAD_MATERIAL_PREFIX = 'std_skin_head'


def _geometry(objects, growth_surface_mirror):
    """Build the ray target in world space from the evaluated surfaces."""
    positions, triangles = [], []
    for obj, mesh, material_indices in objects:
        matrix = obj.matrix_world
        transforms = (matrix,)
        if growth_surface_mirror is not None:
            transforms += (growth_surface_mirror @ matrix,)
        mesh.calc_loop_triangles()
        for transform in transforms:
            offset = len(positions)
            positions.extend(transform @ vertex.co for vertex in mesh.vertices)
            triangles.extend(
                tuple(offset + index for index in triangle.vertices)
                for triangle in mesh.loop_triangles
                if material_indices is None or triangle.material_index in material_indices
            )
    return BVHTree.FromPolygons(positions, triangles, all_triangles=True) if triangles else None


def _directions():
    # A deterministic cosine-weighted disk, evaluated on both sides of a card.
    golden_angle = pi * (3 - sqrt(5))
    for index in range(SAMPLE_PAIRS):
        radius = sqrt((index + 0.5) / SAMPLE_PAIRS)
        angle = index * golden_angle
        yield radius * cos(angle), radius * sin(angle), sqrt(1 - radius * radius)


def bake_group(targets, cards, surfaces, depsgraph, *, growth_surface_only=False,
               growth_surface_mirror=None):
    """Write POINT AO to the final cards only. References are read-only."""
    meshes = [(obj, obj.data, None) for obj in cards]
    reference_meshes = []
    try:
        for obj in surfaces:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            if mesh is None:
                if growth_surface_only:
                    raise RuntimeError(f'Hair Tool growth surface "{obj.name}" has no evaluated mesh for whole-mesh AO.')
                continue
            reference_meshes.append(evaluated)
            if growth_surface_only:
                if not mesh.polygons:
                    raise RuntimeError(f'Hair Tool growth surface "{obj.name}" has no faces for whole-mesh AO.')
                meshes.append((evaluated, mesh, None))
                continue
            head_slots = {
                index for index, material in enumerate(mesh.materials)
                if material and material.name.casefold().startswith(HEAD_MATERIAL_PREFIX)
            }
            meshes.append((evaluated, mesh, head_slots or None))
        tree = _geometry(meshes, growth_surface_mirror)
    finally:
        for obj in reference_meshes:
            obj.to_mesh_clear()
    if growth_surface_only and tree is None:
        raise RuntimeError('The Hair Tool growth surface has no evaluated triangles for whole-mesh AO.')

    samples = tuple(_directions())
    baked = set()
    for obj, distance in targets:
        matrix = obj.matrix_world
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        bias = min(0.00005, distance * 0.01)
        values = []
        for vertex in obj.data.vertices:
            normal = (normal_matrix @ vertex.normal).normalized()
            if tree is None or not normal.length_squared:
                values.append(1.0)
                continue
            position = matrix @ vertex.co
            tangent = normal.orthogonal().normalized()
            bitangent = normal.cross(tangent)
            front_origin = position + bias * normal
            back_origin = position - bias * normal
            blocked = 0.0
            for x, y, z in samples:
                lateral = tangent * x + bitangent * y
                for origin, direction in (
                    (front_origin, lateral + normal * z),
                    (back_origin, lateral - normal * z),
                ):
                    hit_distance = tree.ray_cast(origin, direction, distance)[3]
                    if hit_distance is not None:
                        blocked += 1.0 - hit_distance / distance
            values.append(1.0 - blocked / (2 * SAMPLE_PAIRS))

        if not values:
            continue
        existing = obj.data.attributes.get(ATTRIBUTE)
        if existing:
            obj.data.attributes.remove(existing)
        attribute = obj.data.attributes.new(ATTRIBUTE, 'FLOAT', 'POINT')
        attribute.data.foreach_set('value', values)
        baked.add(obj)
        print(f'[send2ue] Whole Mesh AO B {obj.name}: '
              f'{min(values):.4f}..{max(values):.4f}, mean {sum(values) / len(values):.4f}')
    return baked
