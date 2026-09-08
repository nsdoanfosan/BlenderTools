"""Optional Hair Tool generator-owned cloth capture; authoring data is read-only.

All stamps, color packing and triangulation live on disposable export copies.
Failure to prove ownership defers cloth creation and leaves normal export intact.
"""
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix

STATE_KEY = 'send2ue_hair_guide_cloth_state'
EXTENSION_NAME = 'zzzz_hair_guide_cloth'
ROLE_PROPERTY = '_send2ue_guide_cloth_role'
PACKAGE_PROPERTY = '_send2ue_guide_cloth_package'
GUID_ATTRIBUTE = 'send2ue_parent_guide_id'
STAMP_ATTRIBUTE = 'send2ue_source_guide_island'
RENDER_ONLY_GUID = -1


def settings(properties=None):
    properties = properties or getattr(bpy.context.scene, 'send2ue', None)
    return getattr(getattr(properties, 'extensions', None), EXTENSION_NAME, None)


def enabled(properties=None):
    return bool(getattr(settings(properties), 'enabled', False))


def begin():
    bpy.app.driver_namespace[STATE_KEY] = {'packages': {}, 'diagnostics': [], 'next_gid': 1}


def state():
    return bpy.app.driver_namespace.setdefault(STATE_KEY, {'packages': {}, 'diagnostics': [], 'next_gid': 1})


def diagnostic(source, reason):
    row = {'source': str(source), 'status': 'cloth_deferred', 'reason': str(reason)}
    state()['diagnostics'].append(row)
    print('[send2ue][hair_guide_cloth] ' + json.dumps(row, ensure_ascii=False))
    return row


def resolve_export_armature(source):
    """Use the existing rig relationship even while that exported rig is hidden.

    armature_modifier_fix makes those explicit rigs visible later in ordinary
    pre-operation. Capture runs earlier, so visibility is not an identity test.
    """
    from . import hair_tool_export as hair, armature_modifier_fix
    rig = hair._get_armature(source)
    if rig:
        return rig
    return armature_modifier_fix.get_top_parent_rig_object(
        source, armature_modifier_fix._export_armature_objects())


def _object_inputs(obj):
    from .hair_tool_export import _modifier_input_get
    for modifier in obj.modifiers:
        group = getattr(modifier, 'node_group', None)
        if modifier.type != 'NODES' or not group or not modifier.show_viewport:
            continue
        for socket in group.interface.items_tree:
            if getattr(socket, 'in_out', '') != 'INPUT' or getattr(socket, 'socket_type', '') != 'NodeSocketObject':
                continue
            value = _modifier_input_get(modifier, socket.identifier)
            yield modifier, socket, value


def find_guide(render):
    """Trace the immediate generator input; never infer a fixed hierarchy depth.

    Registry and hierarchy are searched even when input lookup fails, so a
    plausible but unresolved guide cannot silently become a missing guide.
    """
    inputs, authoritative, unresolved = [], [], []
    for modifier, socket, value in _object_inputs(render):
        label = socket.name.replace('_', ' ').strip().casefold()
        row = {'modifier': modifier.name, 'group': modifier.node_group.name,
               'socket': socket.name, 'identifier': socket.identifier,
               'object': getattr(value, 'name', None)}
        inputs.append(row)
        if value is None or value == render:
            continue
        if label == 'source surface' and modifier.node_group.name.startswith('Hair_System_Setup'):
            authoritative.append((value, modifier.name, socket.identifier))
        elif label in {'source surface', 'curve guide', 'guide', 'source mesh'}:
            unresolved.append(value)
    props = getattr(render, 'ht_props', None)
    nodes = getattr(props, 'hair_nodes', None)
    base_name = str(getattr(nodes, 'hair_base_mesh', '') or '')
    registry = {'available': props is not None, 'hair_base_mesh': base_name,
                'systems': [{'name': row.name, 'parent_tag': str(getattr(row, 'parent_tag', ''))}
                            for row in getattr(nodes, 'hair_systems', [])]}
    registry_object = bpy.data.objects.get(base_name) if base_name else None
    if registry_object and registry_object != render:
        unresolved.append(registry_object)
    relatives = ([render.parent] if render.parent else []) + list(render.children)
    hierarchy = [{'name': obj.name, 'type': obj.type} for obj in relatives]
    # A mesh/curve parent can be an unresolved generating source. Children can
    # be outputs, but a guide-labelled child is evidence requiring inspection.
    for obj in relatives:
        if obj.type in {'MESH', 'CURVES', 'CURVE'} and (obj == render.parent or 'guide' in obj.name.casefold()):
            unresolved.append(obj)
    receipt = {'render': render.name, 'active_generator_inputs': inputs,
               'hair_tool_registry': registry, 'hierarchy': hierarchy,
               'search_stages': ['active_generator_inputs', 'hair_tool_registry', 'hierarchy']}
    sources = {item[0] for item in authoritative}
    if len(sources) == 1:
        guide = next(iter(sources))
        receipt.update(status='resolved', guide=guide.name, basis='immediate_generator_source_surface')
        return guide, [(m, s) for o, m, s in authoritative if o == guide], receipt
    if sources or unresolved:
        receipt.update(status='unresolved', candidates=sorted({o.name for o in sources | set(unresolved)}))
        return None, [], receipt
    receipt.update(status='no_guide_after_search', guide=None,
                   reason='No generating source input, registry source or plausible hierarchy guide exists.')
    return None, [], receipt


def components(mesh):
    parents = list(range(len(mesh.vertices)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for edge in mesh.edges:
        parents[root(edge.vertices[1])] = root(edge.vertices[0])
    seen = {}
    return [seen.setdefault(root(i), len(seen)) for i in range(len(parents))]


def _copy_mesh(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    geometry_fn = getattr(evaluated, 'evaluated_geometry', None)
    geometry = geometry_fn() if callable(geometry_fn) else geometry_fn
    instance_points_fn = getattr(geometry, 'instances_pointcloud', None)
    instance_points = instance_points_fn() if callable(instance_points_fn) else instance_points_fn
    if (instance_points is not None and len(instance_points.points)) or (geometry is None and any(
            instance.is_instance and instance.parent and instance.parent.original == obj
            for instance in depsgraph.object_instances)):
        # The ordinary exporter also collects GN instances. Taking only mesh
        # here would silently replace its complete render with a partial one.
        # Use GeometrySet first: CURVES mesh output can appear as a depsgraph
        # proxy instance despite having no actual instance component.
        raise ValueError(f'{obj.name}: evaluated output contains instances; keep ordinary export and defer optional cloth.')
    source = getattr(geometry, 'mesh', None)
    if source is not None:
        mesh = source.copy()
    elif evaluated.type == 'MESH':
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True,
                                             depsgraph=bpy.context.evaluated_depsgraph_get())
    else:
        raise ValueError(f'{obj.name}: evaluated output has no simulation mesh; the original curve is unchanged.')
    if not mesh.vertices or not mesh.polygons:
        bpy.data.meshes.remove(mesh)
        raise ValueError(f'{obj.name}: evaluated guide has no usable faces.')
    mesh.transform(evaluated.matrix_world)
    if evaluated.matrix_world.determinant() < 0:
        mesh.flip_normals()
    mesh.update()
    return mesh


def _stamp_group():
    group = bpy.data.node_groups.new('Send2UE_GuideOwnership_ExportOnly', 'GeometryNodeTree')
    for direction in ('INPUT', 'OUTPUT'):
        group.interface.new_socket(name='Geometry', in_out=direction, socket_type='NodeSocketGeometry')
    source = group.nodes.new('NodeGroupInput')
    output = group.nodes.new('NodeGroupOutput')
    island = group.nodes.new('GeometryNodeInputMeshIsland')
    store = group.nodes.new('GeometryNodeStoreNamedAttribute')
    store.domain, store.data_type = 'FACE', 'INT'
    store.inputs['Name'].default_value = STAMP_ATTRIBUTE
    group.links.new(source.outputs['Geometry'], store.inputs['Geometry'])
    group.links.new(island.outputs['Island Index'], store.inputs['Value'])
    group.links.new(store.outputs['Geometry'], output.inputs['Geometry'])
    return group


def _has_grid_generator(obj):
    seen = set()
    def visit(group):
        if group in seen:
            return False
        seen.add(group)
        if group.name.startswith('HS_Grid_Guide'):
            return True
        return any(visit(node.node_tree) for node in group.nodes
                   if getattr(node, 'node_tree', None) and not node.mute)
    return any(visit(mod.node_group) for mod in obj.modifiers
               if mod.type == 'NODES' and mod.show_viewport and mod.node_group)


def _int_attribute(mesh, name, values):
    attribute = mesh.attributes.get(name)
    if attribute:
        mesh.attributes.remove(attribute)
    attribute = mesh.attributes.new(name, 'INT', 'POINT')
    for datum, value in zip(attribute.data, values):
        datum.value = value


def own_weights(mesh):
    attr = mesh.attributes.get('ChaosWeight')
    if attr is None:
        return [0.0] * len(mesh.vertices), {'authored_weight_present': False,
                  'weight_source': 'absent_intentionally_static', 'clamped_values': 0}
    if attr.domain != 'POINT' or attr.data_type not in {'FLOAT_COLOR', 'BYTE_COLOR', 'FLOAT'}:
        raise ValueError(f'Unsupported guide ChaosWeight domain/type: {attr.domain}/{attr.data_type}')
    raw = [float(item.color[1] if hasattr(item, 'color') else item.value) for item in attr.data]
    if len(raw) != len(mesh.vertices) or not all(math.isfinite(w) for w in raw):
        raise ValueError('Guide ChaosWeight contains incomplete or non-finite values.')
    return [max(0., min(1., w)) for w in raw], {'authored_weight_present': True,
               'weight_source': 'simulation_source_own_g',
               'raw_min': min(raw), 'raw_max': max(raw),
               'clamped_values': sum(w < 0 or w > 1 for w in raw)}


def _capture_render_only(source, export_state, search, include_system_ao, ao_settings):
    """Keep the ordinary complete render output without inventing a sim mesh."""
    from . import hair_tool_export as hair
    objects = hair._evaluated_mesh_objects(source, export_state,
        include_system_ao=include_system_ao, ao_settings=ao_settings)
    for obj in objects:
        _int_attribute(obj.data, GUID_ATTRIBUTE, [RENDER_ONLY_GUID] * len(obj.data.vertices))
    return objects, {'vertices': [], 'triangles': [], 'guide_ids': [],
        'weights': [], 'authored_weights': [], 'parts': [],
        'render_vertex_count': sum(len(obj.data.vertices) for obj in objects),
        'source': {**search, 'simulation_enabled': False}, 'simulation_enabled': False}


def capture_source(source, export_state, include_system_ao=False, ao_settings=None):
    """Return (temporary render parts, physical source packet), or defer safely."""
    from . import hair_tool_export as hair
    copies, group, meshes = [], None, []
    try:
        guide, sockets, search = find_guide(source)
        if search['status'] == 'unresolved':
            diagnostic(source.name, search)
            return None
        if search['status'] == 'no_guide_after_search':
            return _capture_render_only(source, export_state, search, include_system_ao, ao_settings)
        render_copy = source.copy()
        render_copy.name = source.name + '__GuideCapture'
        bpy.context.scene.collection.objects.link(render_copy)
        render_copy.hide_set(False)
        render_copy.hide_viewport = False
        copies.append(render_copy)
        if guide:
            guide_copy = guide.copy()
            guide_copy.name = guide.name + '__GuideCapture'
            bpy.context.scene.collection.objects.link(guide_copy)
            guide_copy.hide_set(False)
            guide_copy.hide_viewport = False
            copies.append(guide_copy)
            group = _stamp_group()
            stamp = guide_copy.modifiers.new('Send2UE Export Ownership', 'NODES')
            stamp.node_group = group
            for name, identifier in sockets:
                hair._modifier_input_set(render_copy.modifiers[name], identifier, guide_copy)
        # AO changes affect disposable copies only.
        if include_system_ao:
            for modifier in render_copy.modifiers:
                if modifier.type == 'NODES' and modifier.node_group and modifier.node_group.name.startswith('HT_Mesh_AO'):
                    hair._apply_ao_modifier_settings(modifier, ao_settings)
                    modifier.show_viewport = True
        bpy.context.view_layer.update()
        gm = _copy_mesh(guide_copy)
        meshes.append(gm)
        weights, weight_receipt = own_weights(gm)
        # Decide once for the entire evaluated generating source, before
        # requiring render ownership. Zero roots/islands of an active source
        # remain part of its cloth topology; render weights never decide this.
        if not any(weight > 0.0 for weight in weights):
            static_source = {**search, **weight_receipt,
                'status': 'guide_weights_all_zero',
                'guide_search_status': search['status'],
                'excluded_sim_vertices': len(gm.vertices)}
            return _capture_render_only(source, export_state, static_source,
                                        include_system_ao, ao_settings)
        rm = _copy_mesh(render_copy)
        meshes.append(rm)
        if include_system_ao and rm.attributes.get('AO') is None:
            hair._set_neutral_ao(rm)
        guide_islands = components(gm)
        if guide:
            stamped = gm.attributes.get(STAMP_ATTRIBUTE)
            if stamped is None:
                raise ValueError('Evaluated guide ownership stamp is missing.')
            face_ids = [item.value for item in stamped.data]
            guide_islands = [None] * len(gm.vertices)
            for face, owner in zip(gm.polygons, face_ids):
                for vertex in face.vertices:
                    if guide_islands[vertex] not in (None, owner):
                        raise ValueError('Guide point crosses source islands.')
                    guide_islands[vertex] = owner
            if any(g is None for g in guide_islands):
                raise ValueError('Guide has loose points without a surface owner.')
            attr = rm.attributes.get(STAMP_ATTRIBUTE)
            provenance = 'generator_carried_island'
            if attr is None and _has_grid_generator(render_copy):
                attr = rm.attributes.get('src_island_index')
                provenance = 'hair_tool_grid_src_island_index'
            if attr is None or attr.domain != 'POINT' or attr.data_type != 'INT':
                raise ValueError('This generator does not preserve an exact source guide island attribute.')
            render_islands = [v.value for v in attr.data]
        if not set(render_islands).issubset(set(guide_islands)):
            raise ValueError('Generated strands refer to an absent guide island.')
        strand_owners = {}
        for strand, owner in zip(components(rm), render_islands):
            strand_owners.setdefault(strand, set()).add(owner)
        if any(len(owners) != 1 for owners in strand_owners.values()):
            raise ValueError('A connected strand has multiple generating guide IDs.')
        ids = {}
        for local in sorted(set(guide_islands)):
            ids[local] = state()['next_gid']
            state()['next_gid'] += 1
        gids = [ids[i] for i in guide_islands]
        render_gids = [ids[i] for i in render_islands]
        _int_attribute(rm, GUID_ATTRIBUTE, render_gids)
        longitudinal = rm.attributes.get('UVHelperGN')
        if longitudinal and longitudinal.domain == 'CORNER' and longitudinal.data_type == 'FLOAT_VECTOR':
            parameters = [[] for _ in rm.vertices]
            for loop, datum in zip(rm.loops, longitudinal.data):
                parameters[loop.vertex_index].append(float(datum.vector[1]))
            _int_attribute(rm, 'send2ue_strand_root', [int(bool(v) and max(abs(t) for t in v) <= 1e-6) for v in parameters])
        gm.calc_loop_triangles()
        packet = {'vertices': [list(v.co) for v in gm.vertices],
                  'triangles': [list(t.vertices) for t in gm.loop_triangles],
                  'guide_ids': gids, 'weights': weights, 'parts': [], 'source': search,
                  'simulation_enabled': True,
                  'authored_weights': [float(d.color[1] if hasattr(d, 'color') else d.value)
                      for d in gm.attributes['ChaosWeight'].data] if weight_receipt['authored_weight_present'] else [None] * len(weights)}
        for local, gid in ids.items():
            vertices = [i for i, g in enumerate(gids) if g == gid]
            packet['parts'].append({'gid': gid, 'source_guide': guide.name,
                'source_render': source.name, 'source_vertex_indices': vertices,
                'local_sim_vertex_indices': vertices, 'provenance': provenance,
                'render_vertices': render_gids.count(gid), **weight_receipt})
        obj = bpy.data.objects.new(source.name + '__GuideRender', rm)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = Matrix.Identity(4)
        export_state['temporary_object_names'].add(obj.name)
        meshes.remove(rm)
        return [obj], packet
    except Exception as error:
        diagnostic(source.name, str(error))
        return None
    finally:
        for obj in reversed(copies):
            bpy.data.objects.remove(obj, do_unlink=True)
        if group and group.users == 0:
            bpy.data.node_groups.remove(group)
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.context.view_layer.update()


def _triangulate(mesh):
    import bmesh
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method='BEAUTY', ngon_method='BEAUTY')
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()


def finish_asset(render, captures, asset_name, armature, export_collection, export_state):
    """Commit optional export copies only after a complete packet was produced."""
    original = render.data
    original_objects = set(export_state['temporary_object_names'])
    original_packages = set(state()['packages'])
    render.data = original.copy()
    try:
        _finish_asset(render, captures, asset_name, armature, export_collection, export_state)
        if not render.get(PACKAGE_PROPERTY):
            disposable = render.data
            render.data = original
            bpy.data.meshes.remove(disposable)
        elif original.users == 0:
            bpy.data.meshes.remove(original)
    except Exception:
        disposable = render.data
        render.data = original
        if disposable.users == 0:
            bpy.data.meshes.remove(disposable)
        for name in set(export_state['temporary_object_names']) - original_objects:
            obj = bpy.data.objects.get(name)
            if obj:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            export_state['temporary_object_names'].discard(name)
        for key in set(state()['packages']) - original_packages:
            del state()['packages'][key]
        raise


def _finish_asset(render, captures, asset_name, armature, export_collection, export_state):
    """Build one unparented simulation export beside a normal prepared render."""
    from . import hair_tool_export as hair
    positions, triangles, gids, weights, authored_weights, parts = [], [], [], [], [], []
    for capture in captures:
        base = len(positions)
        positions.extend(capture['vertices'])
        triangles.extend([[i + base for i in triangle] for triangle in capture['triangles']])
        gids.extend(capture['guide_ids'])
        weights.extend(capture['weights'])
        authored_weights.extend(capture['authored_weights'])
        for row in capture['parts']:
            row = dict(row)
            row['sim_vertex_indices'] = [base + i for i in row.pop('local_sim_vertex_indices')]
            parts.append(row)
    if positions and not armature:
        diagnostic(asset_name, 'Paired cloth requires nonempty meshes and the normal shared skeletal armature.')
        return
    attr = render.data.attributes.get(GUID_ATTRIBUTE)
    if attr is None or attr.domain != 'POINT':
        diagnostic(asset_name, 'Final export processing did not retain source guide ownership.')
        return
    # Triangulate disposable exports so the packet describes actual FBX topology.
    _triangulate(render.data)
    render_gids = [v.value for v in render.data.attributes[GUID_ATTRIBUTE].data]
    render_only = [i for i, gid in enumerate(render_gids) if gid == RENDER_ONLY_GUID]
    if set(render_gids) - {RENDER_ONLY_GUID} - set(gids):
        diagnostic(asset_name, 'Final export processing changed source guide ownership.')
        return
    # Fully filtered sources are ordinary empty output. They have no vertices
    # to skin and must not invalidate the otherwise guided group's provenance.
    render_only_sources = [c['source'] for c in captures
                           if c.get('simulation_enabled') is False and c['render_vertex_count']]
    if render_only and not render_only_sources:
        diagnostic(asset_name, 'Render-only vertices require a completed no-guide search receipt.')
        return
    simulation_enabled = bool(positions)
    quantized = [0.] * len(weights)
    targets = [('render', render)]
    if simulation_enabled:
        mesh = bpy.data.meshes.new(asset_name + '__GuideSim')
        mesh.from_pydata(positions, [], triangles)
        mesh.update()
        obj = bpy.data.objects.new(asset_name + '_GuideSim', mesh)
        export_collection.objects.link(obj)
        export_state['temporary_object_names'].add(obj.name)
        obj[hair.SOURCE_NAME_PROPERTY] = asset_name + '_GuideSim'
        obj[hair.TEMP_PROPERTY] = True
        # No Empty/armature parent: Combine Assets must keep this as its own FBX.
        group = obj.vertex_groups.new(name=hair._get_head_bone_name(armature))
        group.add(range(len(mesh.vertices)), 1., 'REPLACE')
        obj.modifiers.new('Armature', 'ARMATURE').object = armature
        _int_attribute(mesh, GUID_ATTRIBUTE, gids)
        colors = mesh.color_attributes.new(name='RFAOS', type='BYTE_COLOR', domain='CORNER')
        for loop, color in zip(mesh.loops, colors.data):
            color.color_srgb = (1., weights[loop.vertex_index], 1., 1.)
            quantized[loop.vertex_index] = float(color.color_srgb[1])
        mesh.color_attributes.active_color = colors
        mesh.color_attributes.render_color_index = list(mesh.color_attributes).index(colors)
        # Keep an existing material; sim appearance does not require new material assets.
        if render.data.materials:
            mesh.materials.append(render.data.materials[0])
        targets.insert(0, ('sim', obj))
    rgba = render.data.color_attributes.get('RFAOS')
    packet = {'version': 2, 'recipe': 'send2ue.generator_owned_cloth.v2', 'remesh': False,
        'simulation_enabled': simulation_enabled,
        'meshes': {'sim': {'vertices': positions, 'triangles': triangles, 'guide_ids': gids, 'weights': quantized,
                           'authored_weights': authored_weights},
                   'render': {'vertices': [list(render.matrix_world @ v.co) for v in render.data.vertices],
                              'triangles': [list(p.vertices) for p in render.data.polygons],
                              'guide_ids': render_gids,
                              'render_only_vertex_indices': render_only,
                              'root_indices': [i for i, d in enumerate(render.data.attributes['send2ue_strand_root'].data) if d.value]
                                  if render.data.attributes.get('send2ue_strand_root') else [],
                              'root_basis': 'UVHelperGN.y longitudinal zero; never material Factor or render G',
                              'rgba_srgb': [list(d.color_srgb) for d in rgba.data] if rgba else [],
                              'loop_vertices': [loop.vertex_index for loop in render.data.loops],
                              'uv_layers': {uv.name: [list(d.uv) for d in uv.data] for uv in render.data.uv_layers}}},
        'parts': parts, 'sources': [c['source'] for c in captures],
        'render_only_sources': render_only_sources,
        'armature': {'name': armature.name, 'bones': [b.name for b in armature.data.bones],
                     'matrix_world': [list(r) for r in armature.matrix_world],
                     'rest_bones': [{'name': b.name, 'parent': b.parent.name if b.parent else None,
                                     'matrix_local': [list(r) for r in b.matrix_local]} for b in armature.data.bones],
                     'export_skin_rule': {'head_bone': hair._get_head_bone_name(armature), 'weight': 1.0,
                                          'basis': 'existing_send2ue_hair_tool_export_rule'}} if armature else None,
        'weight_policy': 'Own simulation ChaosWeight; whole all-zero or absent-weight sources render-only; retain all vertices of any positive-weight source; never child render G.',
        'missing_guide_policy': 'Render-only after complete guide search; no simulation geometry or proxy binding.'}
    packet['content_id'] = hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    package = {'packet': packet, 'exports': {}, 'imported': set(), 'asset_name': asset_name}
    state()['packages'][packet['content_id']] = package
    for role, target in targets:
        target[ROLE_PROPERTY] = role
        target[PACKAGE_PROPERTY] = packet['content_id']


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record_export(asset_data):
    obj = bpy.data.objects.get(asset_data.get('_mesh_object_name', ''))
    if not obj or not obj.get(PACKAGE_PROPERTY):
        return
    key, role = obj[PACKAGE_PROPERTY], obj[ROLE_PROPERTY]
    package = state()['packages'][key]
    path = Path(asset_data['file_path']).resolve()
    package['exports'][role] = {'asset_path': asset_data['asset_path'],
                                'fbx': {'path': str(path), 'sha256': sha256(path)}}
    asset_data['_hair_guide_cloth'] = {'package': key, 'role': role}
    expected_roles = {'sim', 'render'} if package['packet']['simulation_enabled'] else {'render'}
    if set(package['exports']) != expected_roles:
        return
    filename = path.parent / (package['asset_name'] + '.hair_guide_cloth.json')
    filename.write_text(json.dumps(package['packet'], separators=(',', ':'), allow_nan=False), encoding='utf8')
    package['manifest_path'], package['manifest_sha256'] = str(filename), sha256(filename)
    # The final exported member is also the final import queue member. Embed a
    # complete portable record there so deferred ingest does not need Blender's
    # driver namespace or disposable objects after post-operation cleanup.
    asset_data['_hair_guide_cloth_record'] = _package_record(package, key, settings())


def _package_record(package, key, cfg):
    simulation_enabled = package['packet']['simulation_enabled']
    record = {'version': 2, 'content_id': key, 'simulation_enabled': simulation_enabled,
              'manifest_path': package['manifest_path'], 'manifest_sha256': package['manifest_sha256']}
    for role in (('sim', 'render') if simulation_enabled else ('render',)):
        record[role + '_asset_path'] = package['exports'][role]['asset_path']
        record[role + '_fbx'] = package['exports'][role]['fbx']
    for name in ('cloth_template_asset_path', 'body_mesh_asset_path', 'physics_asset_path'):
        record[name] = str(getattr(cfg, name, ''))
    return record


def import_record(asset_data, properties):
    if asset_data.get('skip'):
        return None
    return asset_data.get('_hair_guide_cloth_record')
