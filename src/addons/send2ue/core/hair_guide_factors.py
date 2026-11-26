"""Carry a generating guide's spline parameter through the child's native construction."""
import math

from . import hair_guide_ribbon as ribbon

RENDER_ATTRIBUTE = 'send2ue_render_guide_factor'
# Positive encoding distinguishes a real root from a missing field on joined meshes.
CARRIED_ATTRIBUTE = 'send2ue_sampled_guide_factor'


class MissingGeneratorParameter(ValueError):
    """A recognized generator lost the source-coordinate contract."""


def _source(socket):
    if socket is None or len(socket.links) != 1:
        return None
    source = socket.links[0].from_socket
    while source.node.bl_idname == 'NodeReroute':
        source = source.node.inputs[0].links[0].from_socket
    return source


def _copy_sample(group, original, value):
    sample = group.nodes.new(original.bl_idname)
    sample.data_type = 'FLOAT'
    for name in ('domain', 'clamp', 'mode', 'use_all_curves'):
        if hasattr(original, name):
            setattr(sample, name, getattr(original, name))
    for src in original.inputs:
        if src.name == 'Value' or not src.enabled:
            continue
        dest = sample.inputs.get(src.name)
        if dest is None:
            continue
        if src.is_linked:
            group.links.new(src.links[0].from_socket, dest)
        elif hasattr(src, 'default_value'):
            dest.default_value = src.default_value
    group.links.new(value, sample.inputs['Value'])
    return sample.outputs['Value']


def _prism_position_chain(group):
    # The generator builds each row by sampling original rail positions at its
    # own indices, then interpolating on its UV layer for that row. Reuse both
    # lookups, including native face-group filters and the sample position.
    matches = []
    for move in group.nodes:
        if move.bl_idname != 'GeometryNodeSetPosition':
            continue
        surface = _source(move.inputs['Position'])
        if surface is None or surface.node.bl_idname != 'GeometryNodeSampleNearestSurface':
            continue
        index = _source(surface.node.inputs['Value'])
        if index is None or index.node.bl_idname != 'GeometryNodeSampleIndex':
            continue
        position = _source(index.node.inputs['Value'])
        if position is not None and position.node.bl_idname == 'GeometryNodeInputPosition':
            matches.append((move, surface.node, index.node))
    if len(matches) != 1:
        raise ValueError('Cannot identify the native Prism rail-position sampler')
    return matches[0]


def _encoded_guide_field(group):
    field = group.nodes.new('GeometryNodeInputNamedAttribute')
    field.data_type = 'FLOAT'
    field.inputs['Name'].default_value = ribbon.FACTOR_ATTRIBUTE
    encoded = group.nodes.new('ShaderNodeMath')
    encoded.operation = 'ADD'
    group.links.new(field.outputs['Attribute'], encoded.inputs[0])
    group.links.new(field.outputs['Exists'], encoded.inputs[1])
    return encoded.outputs[0]


def _stamp_prism(group):
    move, surface, index = _prism_position_chain(group)
    indexed = _copy_sample(group, index, _encoded_guide_field(group))
    sampled = _copy_sample(group, surface, indexed)
    ribbon._stamp(group, move.inputs['Geometry'], CARRIED_ATTRIBUTE, 'FLOAT', 'POINT', sampled)


def _named_float(group, name):
    node = group.nodes.new('GeometryNodeInputNamedAttribute')
    node.data_type = 'FLOAT'
    node.inputs['Name'].default_value = name
    return node.outputs['Attribute']


def _copied_group(node, copies):
    group = node.node_tree.copy()
    copies.append(group)
    node.node_tree = group
    return group


def _grid_position_sample(group):
    matches = []
    for move in group.nodes:
        if move.bl_idname == 'GeometryNodeSetPosition':
            position = _source(move.inputs['Position'])
            if position and position.node.bl_idname == 'GeometryNodeSampleCurve' and position.name == 'Position':
                matches.append((move, position.node))
    if len(matches) != 1:
        raise ValueError('Cannot identify the native Grid perpendicular position sampler')
    return matches[0]


def _stamp_grid(group, copies):
    # Grid forms perpendicular rails twice, sampling a curve position in each
    # pass. Then it mixes uniform and non-uniform positions on the output
    # rails. Finally its optional root hook restores the original first row.
    source = group.nodes['Capture Attribute.001'].inputs['Geometry']
    if _source(source).node.bl_idname != 'NodeGroupInput':
        raise ValueError('Unknown Grid source surface')
    ribbon._stamp(group, source, CARRIED_ATTRIBUTE, 'FLOAT', 'POINT', _encoded_guide_field(group))
    rails = [n for n in group.nodes if getattr(n, 'node_tree', None)
             and ribbon._base_name(n.node_tree.name) == 'HT_Interpol_Curves_Perp']
    if len(rails) != 2:
        raise ValueError('Grid needs its two native perpendicular interpolation passes')
    for node in rails:
        rail = _copied_group(node, copies)
        move, sample = _grid_position_sample(rail)
        t = _copy_sample(rail, sample, _named_float(rail, CARRIED_ATTRIBUTE))
        ribbon._stamp(rail, move.inputs['Geometry'], CARRIED_ATTRIBUTE, 'FLOAT', 'POINT', t)
    move = group.nodes['Set Position.001']
    mix = _source(move.inputs['Position']).node
    active = {s.name: s for s in mix.inputs if s.enabled}
    sample = _source(active['A'])
    if (mix.bl_idname != 'ShaderNodeMix' or sample.node.bl_idname != 'GeometryNodeSampleCurve'
            or sample.name != 'Position' or _source(active['B']).node.bl_idname != 'GeometryNodeInputPosition'):
        raise ValueError('Unknown Grid longitudinal interpolation')
    t = _named_float(group, CARRIED_ATTRIBUTE)
    interpolated = group.nodes.new('ShaderNodeMix')
    interpolated.data_type = 'FLOAT'
    interpolated.clamp_factor = mix.clamp_factor
    inputs = {s.name: s for s in interpolated.inputs if s.enabled}
    group.links.new(_source(active['Factor']), inputs['Factor'])
    group.links.new(_copy_sample(group, sample.node, t), inputs['A'])
    group.links.new(t, inputs['B'])
    ribbon._stamp(group, move.inputs['Geometry'], CARRIED_ATTRIBUTE, 'FLOAT', 'POINT',
                  next(s for s in interpolated.outputs if s.enabled))
    root_capture = group.nodes['Capture Attribute.003']
    root_socket = _source(root_capture.inputs['Root Co (before del)'])
    root_move = group.nodes['Set Position.003']
    if (root_socket.name != 'Root Co' or ribbon._base_name(root_socket.node.node_tree.name) != 'RootTipCo' or
            _source(root_move.inputs['Position']).node != root_capture):
        raise ValueError('Unknown Grid root hook')
    root_t = group.nodes.new('GeometryNodeFieldAtIndex')
    root_t.data_type, root_t.domain = 'FLOAT', 'POINT'
    group.links.new(root_socket.node.outputs['Root ID'], root_t.inputs['Index'])
    group.links.new(t, root_t.inputs['Value'])
    root_field = 'send2ue_grid_hook_factor'
    ribbon._stamp(group, root_capture.inputs['Geometry'], root_field, 'FLOAT', 'POINT', root_t.outputs['Value'])
    hook = group.nodes.new('GeometryNodeSwitch')
    hook.input_type = 'FLOAT'
    group.links.new(_source(root_move.inputs['Selection']), hook.inputs['Switch'])
    group.links.new(t, hook.inputs['False'])
    group.links.new(_named_float(group, root_field), hook.inputs['True'])
    ribbon._stamp(group, root_move.inputs['Geometry'], CARRIED_ATTRIBUTE, 'FLOAT', 'POINT', hook.outputs['Output'])


def stamp_generators(render_copy, copies):
    kinds = set()
    for modifier in render_copy.modifiers:
        top = getattr(modifier, 'node_group', None)
        if modifier.type != 'NODES' or not modifier.show_viewport or not top:
            continue
        generators = [node.name for node in top.nodes if getattr(node, 'node_tree', None) and
                      ribbon._base_name(node.node_tree.name) in ('HS_PrismMeshGuide', 'HS_Grid_Guide') and
                      not node.mute and ribbon._output_linked(top, node)]
        if not generators:
            continue
        top = top.copy()
        copies.append(top)
        modifier.node_group = top
        for name in generators:
            node = top.nodes[name]
            kind = ribbon._base_name(node.node_tree.name)
            group = _copied_group(node, copies)
            try:
                if kind == 'HS_PrismMeshGuide':
                    _stamp_prism(group)
                else:
                    _stamp_grid(group, copies)
            except (AttributeError, KeyError, ValueError, TypeError) as error:
                raise MissingGeneratorParameter(f'{kind} source position sampler: {error}') from error
            kinds.add(kind)
    return sorted(kinds)


def source_factors(sim, render, sim_ids, render_ids, strands, self_guide):
    sim_attr = sim.attributes.get(ribbon.FACTOR_ATTRIBUTE)
    attr_name = ribbon.FACTOR_ATTRIBUTE if self_guide else CARRIED_ATTRIBUTE
    render_attr = render.attributes.get(attr_name)
    if sim_attr is None or render_attr is None:
        return [-1.] * len(sim_ids), [-1.] * len(render_ids)
    try:
        sim_values = ribbon._point_attribute(sim, ribbon.FACTOR_ATTRIBUTE, 'FLOAT')
        render_values = ribbon._point_attribute(render, attr_name, 'FLOAT')
    except ValueError as error:
        raise MissingGeneratorParameter(str(error)) from error
    if not self_guide:
        render_values = [v - 1. for v in render_values]
    unsupported = set()
    intervals = {}
    for gid, strand, value in zip(render_ids, strands, render_values):
        if value == -1.:
            unsupported.add(gid)
        elif not math.isfinite(value) or not -1.e-5 <= value <= 1.00001:
            raise MissingGeneratorParameter('Generator did not preserve a complete parent curve parameter')
        else:
            lo, hi = intervals.get(strand, (value, value))
            intervals[strand] = (min(lo, value), max(hi, value))
    constant = {strand for strand, (lo, hi) in intervals.items() if hi == lo}
    child = render.attributes.get('UVHelperGN')
    if constant and child and child.domain == 'CORNER' and child.data_type == 'FLOAT_VECTOR':
        first = {}
        for loop, datum in zip(render.loops, child.data):
            strand = strands[loop.vertex_index]
            if strand in constant:
                t = float(datum.vector[1])
                if abs(t - first.setdefault(strand, t)) > 1.e-5:
                    raise MissingGeneratorParameter('A longitudinal child strand retained only one parent sample')
    if any(not math.isfinite(v) or not 0. <= v <= 1.00001 for v in sim_values):
        raise MissingGeneratorParameter('Invalid generator guide parameter')
    return ([min(1., v) if g not in unsupported else -1. for v, g in zip(sim_values, sim_ids)],
            [min(1., max(0., v)) if g not in unsupported else -1. for v, g in zip(render_values, render_ids)])


def store_render(mesh, values):
    attr = mesh.attributes.get(RENDER_ATTRIBUTE) or mesh.attributes.new(RENDER_ATTRIBUTE, 'FLOAT', 'POINT')
    if attr.domain != 'POINT' or attr.data_type != 'FLOAT' or len(attr.data) != len(values):
        raise ValueError('Invalid render curve-parameter attribute')
    attr.data.foreach_set('value', values)
