"""Export-only open ribbons from the verified Hair Tool circle profile schema.

The render generator keeps its original guide. Curve/sample identity is carried
through copied node groups; native mesh-island identity is never approximated by
vertex order, averaging, or nearest geometry. Unsupported layouts use the
original simulation mesh and leave a reason in the export provenance.
"""
from collections import Counter, defaultdict
import hashlib
import math
import re
import struct


VERSION = 'send2ue.guide_ribbon.v1'
SPLINE_ATTRIBUTE = 'send2ue_ribbon_spline_id'
SAMPLE_ATTRIBUTE = 'send2ue_ribbon_sample_id'
CENTER_ATTRIBUTE = 'send2ue_ribbon_sample_center'


def _base_name(name):
    return re.sub(r'\.\d{3,}$', '', str(name))


def _counts(mesh):
    mesh.calc_loop_triangles()
    return {'vertices': len(mesh.vertices), 'triangles': len(mesh.loop_triangles)}


def geometry_signature(mesh):
    """Hash the exact positions and face order; added identity fields are ignored."""
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(struct.pack('<3d', *vertex.co))
    for face in mesh.polygons:
        digest.update(struct.pack('<I', len(face.vertices)))
        digest.update(struct.pack('<' + 'I' * len(face.vertices), *face.vertices))
    return digest.hexdigest()


def _point_attribute(mesh, name, data_type):
    attr = mesh.attributes.get(name)
    if attr is None or attr.domain != 'POINT' or attr.data_type != data_type or len(attr.data) != len(mesh.vertices):
        raise ValueError('Missing or unsupported ribbon identity attribute: ' + name)
    if data_type == 'FLOAT_VECTOR':
        return [tuple(float(v) for v in item.vector) for item in attr.data]
    values = [item.value for item in attr.data]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Invalid ribbon curve/sample ID: ' + name)
    return values


def _components(mesh):
    parent = list(range(len(mesh.vertices)))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    for edge in mesh.edges:
        parent[root(edge.vertices[0])] = root(edge.vertices[1])
    return [root(i) for i in range(len(parent))]


def _validate_connected(mesh, owners):
    component_owners, owner_components = defaultdict(set), defaultdict(set)
    for component, owner in zip(_components(mesh), owners):
        component_owners[component].add(owner)
        owner_components[owner].add(component)
    if any(len(values) != 1 for values in component_owners.values()) or any(
            len(values) != 1 for values in owner_components.values()):
        raise ValueError('Ribbon guide identity is not one-to-one with connected components')
    return len(component_owners)


def validate_pair(original, ribbon, native_islands, own_weights):
    """Prove source lineage, sample/G preservation and valid open-strip topology."""
    source_ids = _point_attribute(original, SPLINE_ATTRIBUTE, 'INT')
    ribbon_ids = _point_attribute(ribbon, SPLINE_ATTRIBUTE, 'INT')
    if len(native_islands) != len(source_ids):
        raise ValueError('Native guide-island stamp length mismatch')
    native_to_spline, spline_to_native = defaultdict(set), defaultdict(set)
    for native, spline in zip(native_islands, source_ids):
        native_to_spline[native].add(spline)
        spline_to_native[spline].add(native)
    if any(len(values) != 1 for values in native_to_spline.values()) or any(
            len(values) != 1 for values in spline_to_native.values()):
        raise ValueError('Native mesh islands and curve identities are not one-to-one')
    crosswalk = {spline: next(iter(values)) for spline, values in spline_to_native.items()}
    if set(ribbon_ids) != set(source_ids):
        raise ValueError('Ribbon generation changed the source curve set')
    local_owners = [crosswalk[spline] for spline in ribbon_ids]
    original_components = _validate_connected(original, source_ids)
    ribbon_components = _validate_connected(ribbon, ribbon_ids)
    original_weights, _ = own_weights(original)
    ribbon_weights, _ = own_weights(ribbon)
    samples, centers = [], []
    for mesh, ids in ((original, source_ids), (ribbon, ribbon_ids)):
        sample_ids = _point_attribute(mesh, SAMPLE_ATTRIBUTE, 'INT')
        values = _point_attribute(mesh, CENTER_ATTRIBUTE, 'FLOAT_VECTOR')
        if any(any(not math.isfinite(v) for v in point) for point in values):
            raise ValueError('Ribbon sample centers contain non-finite values')
        buckets = defaultdict(list)
        for index, key in enumerate(zip(ids, sample_ids)):
            buckets[key].append(index)
        samples.append(buckets)
        centers.append(values)
    if set(samples[0]) != set(samples[1]):
        raise ValueError('Ribbon generation changed curve sample identities')
    if any(len(indices) != 2 for indices in samples[1].values()):
        raise ValueError('Ribbon must contain exactly two vertices at every curve sample')
    max_center_error = max_weight_error = 0.0
    for key, source_indices in samples[0].items():
        anchor = source_indices[0]
        for index in source_indices:
            if abs(original_weights[index] - original_weights[anchor]) > 1e-6:
                raise ValueError('Source cross-section has different ChaosWeight values')
            if math.dist(centers[0][index], centers[0][anchor]) > 1e-7:
                raise ValueError('Source cross-section has different sample centers')
        for index in samples[1][key]:
            max_center_error = max(max_center_error, math.dist(centers[0][anchor], centers[1][index]))
            max_weight_error = max(max_weight_error, abs(original_weights[anchor] - ribbon_weights[index]))
    if max_center_error > 1e-7 or max_weight_error > 1e-6:
        raise ValueError('Ribbon generation changed a curve sample center or ChaosWeight')
    if any(any(not math.isfinite(float(v)) for v in vertex.co) for vertex in ribbon.vertices):
        raise ValueError('Ribbon contains non-finite geometry')
    ribbon.calc_loop_triangles()
    areas, edge_use = [], Counter()
    for triangle in ribbon.loop_triangles:
        ids = triangle.vertices
        if len({ribbon_ids[i] for i in ids}) != 1:
            raise ValueError('Ribbon triangle crosses generating curves')
        a, b, c = (ribbon.vertices[i].co for i in ids)
        ab, ac = [float(b[i] - a[i]) for i in range(3)], [float(c[i] - a[i]) for i in range(3)]
        cross = [ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]]
        area = math.sqrt(sum(v*v for v in cross)) * 0.5
        if not math.isfinite(area) or area <= 1e-14:
            raise ValueError('Ribbon contains a degenerate triangle')
        areas.append(area)
        for a, b in zip(ids, (ids[1], ids[2], ids[0])):
            edge_use[tuple(sorted((a, b)))] += 1
    if not areas or any(count > 2 for count in edge_use.values()):
        raise ValueError('Ribbon has missing faces or non-manifold edges')
    if len(ribbon.loop_triangles) != 2 * (len(samples[1]) - ribbon_components):
        raise ValueError('Ribbon does not have the expected open-strip connectivity')
    return local_owners, {'guide_count': ribbon_components, 'source_component_count': original_components,
        'sample_count': len(samples[1]), 'max_center_error_m': max_center_error,
        'max_weight_error': max_weight_error, 'min_triangle_area_m2': min(areas),
        'native_island_to_spline': {str(native): next(iter(values)) for native, values in native_to_spline.items()},
        'identity_basis': 'native_mesh_island_to_stamped_curve_and_sample',
        'source_counts': _counts(original), 'simulation_counts': _counts(ribbon)}


def _connect(group, output, target):
    for link in list(target.links):
        group.links.remove(link)
    group.links.new(output, target)


def _stamp(group, target, name, data_type, domain, field=None):
    if len(target.links) != 1:
        raise ValueError('Profile input has no unique geometry source: ' + target.name)
    incoming = target.links[0].from_socket
    store = group.nodes.new('GeometryNodeStoreNamedAttribute')
    store.data_type, store.domain = data_type, domain
    store.inputs['Name'].default_value = name
    if field is None:
        field = group.nodes.new('GeometryNodeInputIndex').outputs['Index']
    group.links.new(field, store.inputs['Value'])
    group.links.new(incoming, store.inputs['Geometry'])
    _connect(group, store.outputs['Geometry'], target)


def _schema(inner):
    required = {'Capture Attribute.003': 'GeometryNodeCaptureAttribute',
                'Capture Attribute.001': 'GeometryNodeCaptureAttribute',
                'Curve to Mesh': 'GeometryNodeCurveToMesh',
                'Set Position.002': 'GeometryNodeSetPosition', 'Group Output': 'NodeGroupOutput'}
    nodes = {}
    for name, kind in required.items():
        node = inner.nodes.get(name)
        if node is None or node.bl_idname != kind or getattr(node, 'mute', False):
            raise ValueError('Unsupported Circle_Profile_UV schema at ' + name)
        nodes[name] = node
    for name, socket in (('Capture Attribute.003', 'Geometry'), ('Capture Attribute.001', 'Geometry'),
                         ('Curve to Mesh', 'Curve'), ('Curve to Mesh', 'Profile Curve'),
                         ('Set Position.002', 'Geometry')):
        if len(nodes[name].inputs[socket].links) != 1:
            raise ValueError('Unsupported circle profile geometry link at ' + name + '/' + socket)
    if nodes['Group Output'].inputs.get('Output_1') is None:
        raise ValueError('Unsupported circle profile output socket')
    if not any(node.bl_idname == 'GeometryNodeCurvePrimitiveCircle' for node in inner.nodes):
        raise ValueError('Circle profile does not contain a circle primitive')
    return nodes


def _output_linked(group, profile):
    # Native Hair Tool wraps the profile with Set Material and DrawDebugUV.
    # Follow geometry flow through those native wrappers, not attribute outputs
    # or an orphaned profile node. Final geometry/sample validation rejects
    # topology-changing wrappers rather than guessing how they remap ownership.
    pending, visited = [profile], set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        for link in group.links:
            if link.from_node != node or getattr(link.from_socket, 'type', '') != 'GEOMETRY':
                continue
            if link.to_node.bl_idname == 'NodeGroupOutput' and getattr(link.to_node, 'is_active_output', True):
                return True
            pending.append(link.to_node)
    return False


def _attribute_only(group, seen=None):
    seen = set() if seen is None else seen
    if group in seen:
        return False
    seen.add(group)
    allowed = {'NodeGroupInput', 'NodeGroupOutput', 'NodeReroute',
               'GeometryNodeStoreNamedAttribute', 'GeometryNodeRemoveAttribute',
               'GeometryNodeSetMaterial', 'GeometryNodeSetShadeSmooth'}
    for node in group.nodes:
        if getattr(node, 'mute', False):
            continue
        if getattr(node, 'node_tree', None):
            if not _attribute_only(node.node_tree, set(seen)):
                return False
        elif any(getattr(socket, 'type', '') == 'GEOMETRY' for socket in list(node.inputs) + list(node.outputs)):
            if node.bl_idname not in allowed:
                return False
    return True


def _remove_groups(groups):
    if not groups:
        return
    import bpy
    # Parent copies reference inner copies; remove parents first, then retry.
    for _ in range(len(groups) + 1):
        for group in list(groups):
            if group.users == 0:
                bpy.data.node_groups.remove(group)
                groups.remove(group)


class RibbonPlan:
    def __init__(self, guide, requested_mode):
        self.guide = guide
        self.modifier = self.original_group = self.profile_name = None
        self.groups = []
        self.prepared = False
        self.receipt = {'version': VERSION, 'requested_mode': requested_mode, 'effective_mode': 'ORIGINAL',
                        'profile': 'UNKNOWN', 'status': 'original', 'fallback_reason': None}

    def fallback(self, reason):
        self.receipt.update(effective_mode='ORIGINAL', status='fallback', fallback_reason=str(reason))
        self.prepared = False
        if self.modifier is not None and self.original_group is not None:
            try:
                self.modifier.node_group = self.original_group
            except ReferenceError:
                pass

    def close(self):
        if self.modifier is not None and self.original_group is not None:
            try:
                self.modifier.node_group = self.original_group
            except ReferenceError:
                pass
        _remove_groups(self.groups)

    def convert(self, original_mesh, native_islands, copy_mesh, own_weights):
        self.receipt['source_counts'] = _counts(original_mesh)
        self.receipt['simulation_counts'] = dict(self.receipt['source_counts'])
        if not self.prepared:
            return original_mesh, native_islands, self.receipt
        import bpy
        obj, mesh, groups = None, None, []
        try:
            obj = self.guide.copy()
            obj.name = self.guide.name + '__RibbonSimulationOnly'
            bpy.context.scene.collection.objects.link(obj)
            obj.hide_viewport = False
            obj.hide_set(False)
            modifier = obj.modifiers[self.modifier.name]
            top = modifier.node_group.copy()
            groups.append(top)
            modifier.node_group = top
            profile = top.nodes[self.profile_name]
            inner = profile.node_tree.copy()
            groups.append(inner)
            profile.node_tree = inner
            nodes = _schema(inner)
            line = inner.nodes.new('GeometryNodeCurvePrimitiveLine')
            line.inputs['Start'].default_value = (-1.0, 0.0, 0.0)
            line.inputs['End'].default_value = (1.0, 0.0, 0.0)
            _connect(inner, line.outputs['Curve'], nodes['Capture Attribute.001'].inputs['Geometry'])
            sweep = nodes['Curve to Mesh']
            for link in list(sweep.inputs['Fill Caps'].links):
                inner.links.remove(link)
            sweep.inputs['Fill Caps'].default_value = False
            position = nodes['Set Position.002']
            _connect(inner, sweep.outputs['Mesh'], position.inputs['Geometry'])
            _connect(inner, position.outputs['Geometry'], nodes['Group Output'].inputs['Output_1'])
            bpy.context.view_layer.update()
            mesh = copy_mesh(obj)
            owners, validation = validate_pair(original_mesh, mesh, native_islands, own_weights)
            self.receipt.update(validation, effective_mode='RIBBON', status='converted', fallback_reason=None)
            result, mesh = mesh, None
            return result, owners, self.receipt
        except Exception as error:
            # Geometry already frozen by the caller remains the original guide.
            # No failed candidate is allowed to replace it or the render mesh.
            self.fallback(error)
            return original_mesh, native_islands, self.receipt
        finally:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            _remove_groups(groups)


def prepare(guide_copy, requested_mode='RIBBON'):
    """Prepare stamps on a disposable guide; keep the original profile shape."""
    plan = RibbonPlan(guide_copy, requested_mode)
    if requested_mode == 'ORIGINAL':
        plan.receipt['status'] = 'original_requested'
        return plan
    if requested_mode != 'RIBBON':
        plan.fallback('Unsupported simulation mesh mode: ' + str(requested_mode))
        return plan
    try:
        candidates, flats = [], []
        modifiers = list(guide_copy.modifiers)
        for index, modifier in enumerate(modifiers):
            top = getattr(modifier, 'node_group', None)
            if modifier.type != 'NODES' or not modifier.show_viewport or top is None:
                continue
            for node in top.nodes:
                inner = getattr(node, 'node_tree', None)
                if inner is None or getattr(node, 'mute', False) or not _output_linked(top, node):
                    continue
                if _base_name(inner.name) == 'Circle_Profile_UV':
                    candidates.append((index, modifier, node))
                elif _base_name(inner.name) == 'Flat_Profile_UV':
                    flats.append(node)
        if not candidates and flats:
            plan.receipt.update(profile='FLAT', status='flat_preserved')
            return plan
        if len(candidates) != 1 or flats:
            raise ValueError('No unique supported circle profile on the active guide output')
        index, modifier, profile = candidates[0]
        plan.receipt.update(profile='CIRCLE', profile_group=profile.node_tree.name, profile_modifier=modifier.name)
        for later in modifiers[index + 1:]:
            if not later.show_viewport:
                continue
            if later.type != 'NODES' or not getattr(later, 'node_group', None) or not _attribute_only(later.node_group):
                raise ValueError('Unsupported active post-profile modifier: ' + later.name + ' (' + later.type + ')')
        _schema(profile.node_tree)
        if len(profile.inputs['Curves'].links) != 1:
            raise ValueError('Circle profile has no unique source curves')
        plan.modifier, plan.original_group, plan.profile_name = modifier, modifier.node_group, profile.name
        top = modifier.node_group.copy()
        plan.groups.append(top)
        modifier.node_group = top
        copied_profile = top.nodes[profile.name]
        inner = copied_profile.node_tree.copy()
        plan.groups.append(inner)
        copied_profile.node_tree = inner
        _stamp(top, copied_profile.inputs['Curves'], SPLINE_ATTRIBUTE, 'INT', 'CURVE')
        capture = _schema(inner)['Capture Attribute.003']
        _stamp(inner, capture.inputs['Geometry'], SAMPLE_ATTRIBUTE, 'INT', 'POINT')
        position = inner.nodes.new('GeometryNodeInputPosition')
        _stamp(inner, capture.inputs['Geometry'], CENTER_ATTRIBUTE, 'FLOAT_VECTOR', 'POINT', position.outputs['Position'])
        plan.prepared = True
        plan.receipt['status'] = 'prepared_original_shape'
    except Exception as error:
        plan.fallback(error)
        _remove_groups(plan.groups)
    return plan
