"""Explicit, reviewable G/kinematic-map routes for a Chaos Cloth Dataflow.

This module never evaluates or saves an asset. Callers own admission, recovery,
native collection comparison and saving. ``derived_maps`` is an explicit list of
confirmed generated roles, not a request to reinterpret arbitrary artist paint.
The planner retains original arrays/properties; the installer checks the graph
again, verifies its exact pin edits, and requires a native verification callback.
"""
import copy
import hashlib
import json
import math
import re


GREEN = 'FCodexClothVertexColorWeightMapNode'
MASK = 'FChaosClothAssetSelectionToWeightMapNode'
PAINT = 'FChaosClothAssetWeightMapNode'
MAXIMUM = 'FChaosClothAssetSimulationMaxDistanceConfigNode'
SKINNING = 'FChaosClothAssetSkinningBlendNode'
EDGE_FIELDS = ('fromNode', 'fromPin', 'toNode', 'toPin')


class WeightRouteError(RuntimeError):
    """A failed edit; rollback_complete describes in-memory recovery only."""
    def __init__(self, message, *, plan, rollback_complete):
        super().__init__(message)
        self.plan = plan
        self.rollback_complete = rollback_complete


def _edge(a, out, b, pin):
    return dict(zip(EDGE_FIELDS, (a, out, b, pin)))


def _edges(rows):
    result = []
    for row in rows:
        if any(not isinstance(row.get(key), str) or not row[key] for key in EDGE_FIELDS):
            raise ValueError('Unresolved Dataflow connection')
        item = {key: row[key] for key in EDGE_FIELDS}
        if item in result:
            raise ValueError('Duplicate Dataflow connection')
        result.append(item)
    return sorted(result, key=lambda row: tuple(row[key] for key in EDGE_FIELDS))


def _pins(info, direction):
    return {pin['name'] for pin in info[direction + 'Pins']}


def _props(info):
    return info.get('properties', info.get('props', {}))


def _name(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\0\r\n"\\'):
        raise ValueError('Weight-map name must be an explicit nonempty string')
    return value


def _object_path(value):
    text = str(value).strip()
    typed = re.fullmatch(r"[A-Za-z0-9_./]+'([^']+)'", text)
    return typed.group(1) if typed else text


def _flat_struct(value):
    if not isinstance(value, str) or not value.startswith('(') or not value.endswith(')'):
        raise ValueError('Expected flat native struct text')
    result = {}
    for field in value[1:-1].split(',') if value != '()' else []:
        key, separator, item = field.partition('=')
        if not separator or key in result or any(char in item for char in '()\\'):
            raise ValueError('Unsupported native struct field')
        if item.lower() in ('true', 'false'):
            result[key] = item.lower() == 'true'
        elif item.startswith('"') and item.endswith('"'):
            result[key] = item[1:-1]
        else:
            result[key] = float(item)
    return result


def _map_name(info, *, node_name=None, effective_map_names=None):
    props = _props(info)
    if info['type'] == PAINT:
        match = re.fullmatch(r'\(StringValue="([^"\\\n]+)"\)', props.get('OutputName', ''))
        if not match:
            if (props.get('OutputName') in ('()', '(StringValue="")') and
                    effective_map_names is not None and node_name in effective_map_names):
                return _name(effective_map_names[node_name])
            raise ValueError('Paint node has no explicit or validated String-authority output map name')
        literal = _name(match.group(1))
        if effective_map_names is not None and node_name in effective_map_names and effective_map_names[node_name] != literal:
            raise ValueError('Validated map identity disagrees with the authored literal: ' + str(node_name))
        return literal
    return _name(str(props.get('WeightMapName', '')).strip('"'))


def _weight_ref(info, pin):
    parent, _, field = pin.rpartition('.')
    if field != 'WeightMap' or not parent or '.' in parent:
        return None
    value = _props(info).get(parent, '')
    match = re.search(r'(?:^\(|,)WeightMap="([^"\\\n]+)"(?=,|\))', value)
    return _name(match.group(1)) if match else None


def _output_pin(info):
    return 'OutputName.StringValue' if info['type'] == PAINT else 'WeightMapName'


def _is_config(info):
    return re.fullmatch(r'FChaosClothAssetSimulation\w*ConfigNode(?:_v\d+)?', info['type']) is not None


def _fingerprint(structure, infos):
    value = {'nodes': sorted(structure['nodes'], key=lambda row: row['name']),
             'connections': _edges(structure['connections']), 'infos': infos}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf8')).hexdigest()


def _incoming(connections, node, pin):
    found = [edge for edge in connections if edge['toNode'] == node and edge['toPin'] == pin]
    if len(found) > 1:
        raise ValueError('Dataflow input has multiple producers: ' + node + '.' + pin)
    return found[0] if found else None


def _upstream(connections, node):
    path, current = [], node
    while True:
        edge = _incoming(connections, current, 'Collection')
        if edge is None:
            return path
        if edge['fromPin'] != 'Collection' or edge['fromNode'] in path or edge['fromNode'] == node:
            raise ValueError('Unsupported or cyclic Collection route')
        current = edge['fromNode']
        path.append(current)


def _paint_snapshot(name, info, native, count):
    props = native.get('props', {})
    if (native.get('active') is not True or props.get('MeshTarget') != 'Simulation'
            or props.get('MapOverrideType') != 'ReplaceAll'
            or props.get('Snapshots') != '(ActiveSnapshot=-1)'
            or props.get('bIsFrozen') != 'False' or props.get('InputName', '()') not in ('()', '(StringValue="")')):
        raise ValueError('Unsupported authored paint state: ' + name)
    text = props.get('VertexWeights', '')
    if not isinstance(text, str) or not text.startswith('(') or not text.endswith(')'):
        raise ValueError('Missing original paint array: ' + name)
    values = [float(value) for value in text[1:-1].split(',') if value]
    if len(values) != count or any(not math.isfinite(value) for value in values):
        raise ValueError('Incomplete original paint array: ' + name)
    # These are sufficient to restore this deliberately narrow ReplaceAll state.
    restore = {'OutputName': {'StringValue': _map_name(info)},
               'InputName': {'StringValue': ''}, 'MeshTarget': 'Simulation',
               'MapOverrideType': 'ReplaceAll', 'VertexWeights': values}
    for key in ('TransferType', 'Name'):
        if key in props:
            restore[key] = props[key]
    return {'name': name, 'type': PAINT, 'properties': copy.deepcopy(props),
            'values': values, 'restore_parameters': restore,
            'posX': int(info.get('posX', 0)), 'posY': int(info.get('posY', 0))}


def plan_weight_routes(structure, infos, source_mesh_path, *, native_nodes=None,
                       collection=None, derived_maps=(), g_node_name=None,
                       expected_green_weights=None, green_node_name='SimulationGreenWeights',
                       allow_source_rebind=False, ensure_skinning_blend=True,
                       skinning_node_name='SimulationKinematicBlend', maximum_node_name=None,
                       effective_map_names=None):
    """Return a JSON-serializable dry-run plan, without calling Unreal.

    ``collection`` is the current dump_cloth_collection_colors collection row.
    ``native_nodes`` is dump_dataflow_nodes' ``nodes`` list (or its whole receipt).
    Existing G paint can be replaced only with caller-proven current G values.
    A derived role needs a nondegenerate old kinematic set, an exact binary mask
    and native weighted consumers after that producer on the Collection route.
    Already-native routes are accepted without requiring the old array again.
    """
    if not isinstance(source_mesh_path, str) or not source_mesh_path.startswith('/') or any(
            char in source_mesh_path for char in '\0\r\n\'"'):
        raise ValueError('A concrete simulation source mesh path is required')
    if len(set(derived_maps)) != len(derived_maps):
        raise ValueError('Duplicate derived-map role')
    derived_maps = [_name(name) for name in derived_maps]
    node_rows = {row['name']: row for row in structure['nodes']}
    if len(node_rows) != len(structure['nodes']) or set(node_rows) != set(infos):
        raise ValueError('Graph nodes are unresolved or duplicated')
    connections = _edges(structure['connections'])
    for edge in connections:
        if edge['fromNode'] not in infos or edge['toNode'] not in infos:
            raise ValueError('Connection references an unresolved node')
        if (edge['fromPin'] not in _pins(infos[edge['fromNode']], 'output') or
                edge['toPin'] not in _pins(infos[edge['toNode']], 'input')):
            raise ValueError('Connection references an unresolved pin')
        _incoming(connections, edge['toNode'], edge['toPin'])
    string_authorities = {}
    for name, info in infos.items():
        if info['type'] != PAINT:
            continue
        value = _map_name(info, node_name=name, effective_map_names=effective_map_names)
        if _props(info).get('OutputName') not in ('()', '(StringValue="")'):
            continue
        edge = _incoming(connections, name, 'InputName.StringValue')
        getter = infos.get(edge['fromNode'], {}) if edge else {}
        pins = [pin for pin in getter.get('outputPins', []) if pin['name'] == 'Value']
        if (not edge or edge['fromPin'] != 'Value' or getter.get('type') != 'FGetDataflowVariableNode'
                or getter.get('active', True) is not True or
                any(key in _props(getter) for key in ('VariableGuid', 'VariableGUID', 'VariableId', 'VariableID')) or
                any(key in getter for key in ('variable_guid', 'variable_id')) or
                not str(_props(getter).get('VariableName', '')).strip('"') or
                len(pins) != 1 or pins[0].get('type', '').lower() not in ('string', 'fstring') or
                any(row['toNode'] == edge['fromNode'] for row in connections)):
            raise ValueError('Validated String map has an unknown or cyclic native getter: ' + name)
        previous = string_authorities.setdefault(edge['fromNode'], value)
        if previous != value:
            raise ValueError('Shared String authority has contradictory validated map identities')
    maxima = [name for name, info in infos.items() if re.fullmatch(MAXIMUM + r'(?:_v\d+)?', info['type'])]
    if maximum_node_name is None:
        if len(maxima) != 1:
            raise ValueError('Explicit G route requires exactly one MaxDistance config or an explicit maximum_node_name')
        maximum = maxima[0]
    elif maximum_node_name not in maxima:
        raise ValueError('The selected source MaxDistance config is missing or has a different type')
    else:
        maximum = maximum_node_name
    if 'KinematicVertices3D' not in _pins(infos[maximum], 'output'):
        raise ValueError('MaxDistance config has no native kinematic-name output')
    def effective_map(node, pin):
        edge = _incoming(connections, node, pin)
        if edge is not None:
            producer = infos[edge['fromNode']]
            if producer['type'] in (PAINT, GREEN, MASK) and edge['fromPin'] == _output_pin(producer):
                return _map_name(producer, node_name=edge['fromNode'], effective_map_names=effective_map_names)
            if (pin.endswith('.WeightMap') and edge['fromPin'] == 'Value'
                    and edge['fromNode'] in string_authorities):
                return string_authorities[edge['fromNode']]
        return _weight_ref(infos[node], pin)

    g_map = effective_map(maximum, 'MaxDistance.WeightMap')
    if not g_map or g_map in derived_maps:
        raise ValueError('MaxDistance needs a distinct explicit G map')
    ancestors = _upstream(connections, maximum)
    if not ancestors:
        raise ValueError('MaxDistance has no Collection source')
    if isinstance(native_nodes, dict):
        native_nodes = native_nodes['nodes']
    native = {row['name']: row for row in (native_nodes or [])}
    built = (collection or {}).get('built_mapping', {})
    count = len(built.get('sim_positions', []))
    pins = set(built.get('kinematic_sim_indices', []))
    replacements, additions, snapshots, outputs, updates = {}, [], {}, {}, []

    def consumers(map_name):
        found = []
        for name, info in infos.items():
            for pin in _pins(info, 'input'):
                if effective_map(name, pin) == map_name:
                    if not _is_config(info):
                        raise ValueError('Map is consumed outside native weighted physics: ' + map_name)
                    found.append((name, pin))
        return sorted(found)

    def producers(map_name):
        if any(info['type'] == 'FDataflowSamplerToAttributeNode' and
               str(_props(info).get('AttributeName', '')).strip('"') == map_name
               for info in infos.values()):
            raise ValueError('Weight-map role has an authored sampler attribute: ' + map_name)
        return [name for name, info in infos.items() if info['type'] in (PAINT, GREEN, MASK)
                and _map_name(info, node_name=name, effective_map_names=effective_map_names) == map_name]

    def replace_paint(old, new, kind, parameters):
        if old not in native or native[old].get('type') != PAINT:
            raise ValueError('Native original paint snapshot is required: ' + old)
        snapshot = _paint_snapshot(old, infos[old], native[old], count)
        for edge in connections:
            if edge['toNode'] == old and edge['toPin'] != 'Collection':
                raise ValueError('Paint has an authored auxiliary input: ' + old)
            if edge['fromNode'] == old and edge['fromPin'] not in ('Collection', 'OutputName.StringValue'):
                raise ValueError('Paint has an unrelated output consumer: ' + old)
        replacements[old] = new
        snapshots[old] = snapshot
        additions.append({'name': new, 'type': kind, 'parameters': parameters,
                          'posX': snapshot['posX'], 'posY': snapshot['posY']})
        return snapshot['values']

    green_candidates = producers(g_map)
    if len(green_candidates) > 1:
        raise ValueError('G map has multiple producers')
    if g_node_name is not None and green_candidates != [g_node_name]:
        raise ValueError('Named G producer does not uniquely own the MaxDistance map')
    current_green = green_candidates[0] if green_candidates else None
    green_parameters = {'SourceMesh': source_mesh_path, 'LODIndex': 0, 'ScalingFactor': 1.,
                        'bInvert': False, 'WeightMapName': g_map, 'PositionTolerance': .001}
    if current_green and infos[current_green]['type'] == GREEN:
        if native.get(current_green, {}).get('active') is not True:
            raise ValueError('Fresh active native G state is required')
        props = _props(infos[current_green])
        source = _object_path(props.get('SourceMesh', ''))
        allowed_source = {source_mesh_path, source_mesh_path + '.' + source_mesh_path.rsplit('/', 1)[-1]}
        if (str(props.get('LODIndex')) != '0'
                or float(props.get('ScalingFactor', math.nan)) != 1.
                or str(props.get('bInvert')).lower() != 'false'
                or float(props.get('PositionTolerance', math.nan)) != .001
                or _incoming(connections, current_green, 'SourceMesh') is not None):
            raise ValueError('Existing native G node has authored overrides')
        if source not in allowed_source:
            if not allow_source_rebind or not source.startswith('/'):
                raise ValueError('Existing native G source differs; explicit source rebind is required')
            updates.append({'name': current_green, 'parameters': {'SourceMesh': source_mesh_path},
                            'restore_parameters': {'SourceMesh': source}})
        green = current_green
    elif current_green:
        if infos[current_green]['type'] != PAINT or current_green not in ancestors:
            raise ValueError('Existing G producer is not the upstream simulation paint role')
        green = _name(green_node_name)
        values = replace_paint(current_green, green, GREEN, green_parameters)
        if (expected_green_weights is None or len(expected_green_weights) != len(values)
                or any(not math.isfinite(value) or not math.isfinite(expected)
                       or abs(value - expected) > .500001e-6
                       for value, expected in zip(values, expected_green_weights))):
            raise ValueError('Existing G paint differs from caller-proven simulation G')
    else:
        green = _name(green_node_name)
        row = node_rows[maximum]
        additions.append({'name': green, 'type': GREEN, 'parameters': green_parameters,
                          'posX': int(row.get('posX', 0)) - 240, 'posY': int(row.get('posY', 0))})
    outputs[g_map] = (green, 'WeightMapName')
    roles = {g_map: {'producer': green, 'consumers': consumers(g_map), 'kind': 'green'}}
    if (maximum, 'MaxDistance.WeightMap') not in roles[g_map]['consumers']:
        raise ValueError('MaxDistance has no accessible native weighted input')
    for map_name in derived_maps:
        candidate = producers(map_name)
        if len(candidate) != 1:
            raise ValueError('Derived role needs exactly one explicit producer: ' + map_name)
        old = candidate[0]
        if maximum not in _upstream(connections, old):
            raise ValueError('Derived mask must follow MaxDistance on its Collection route')
        parameters = {'WeightMapName': map_name, 'SelectedValue': 0., 'UnselectedValue': 1.}
        if infos[old]['type'] == MASK:
            if native.get(old, {}).get('active') is not True:
                raise ValueError('Fresh active native mask state is required: ' + map_name)
            props = _props(infos[old])
            if (float(props.get('SelectedValue', math.nan)) != 0.
                    or float(props.get('UnselectedValue', math.nan)) != 1.
                    or _incoming(connections, old, 'SelectionName') !=
                    _edge(maximum, 'KinematicVertices3D', old, 'SelectionName')):
                raise ValueError('Existing native mask has authored overrides: ' + map_name)
            mask = old
        elif infos[old]['type'] == PAINT:
            if (not 0 < len(pins) < count or any(index < 0 or index >= count for index in pins)):
                raise ValueError('Constant or missing old kinematic state is ambiguous: ' + map_name)
            mask = _name(old + '_FromKinematic')
            values = replace_paint(old, mask, MASK, parameters)
            if any(value != (0. if index in pins else 1.) for index, value in enumerate(values)):
                raise ValueError('Authored paint is not the exact old kinematic mask: ' + map_name)
        else:
            raise ValueError('Derived role is not paint or an existing native mask: ' + map_name)
        consumers_for_role = consumers(map_name)
        if not consumers_for_role or any(old not in _upstream(connections, name) for name, _ in consumers_for_role):
            raise ValueError('Derived mask has no downstream native weighted consumers: ' + map_name)
        outputs[map_name] = (mask, 'WeightMapName')
        roles[map_name] = {'producer': mask, 'consumers': consumers_for_role, 'kind': 'kinematic'}
    names = [row['name'] for row in additions]
    if len(set(names)) != len(names) or any(name in infos for name in names):
        raise ValueError('Native route node name already exists')
    planned = []
    for edge in connections:
        item = dict(edge)
        if item['fromNode'] in replacements:
            item['fromNode'] = replacements[item['fromNode']]
            if item['fromPin'] == 'OutputName.StringValue':
                item['fromPin'] = 'WeightMapName'
        if item['toNode'] in replacements:
            item['toNode'] = replacements[item['toNode']]
        planned.append(item)
    if current_green is None:
        previous = _incoming(planned, maximum, 'Collection')
        planned.remove(previous)
        planned.extend((_edge(previous['fromNode'], previous['fromPin'], green, 'Collection'),
                        _edge(green, 'Collection', maximum, 'Collection')))
    elif current_green not in ancestors:
        raise ValueError('Native G must precede MaxDistance on the Collection route')
    for map_name, role in roles.items():
        producer, output = outputs[map_name]
        if role['kind'] == 'kinematic':
            selection = _edge(maximum, 'KinematicVertices3D', producer, 'SelectionName')
            if selection not in planned:
                planned.append(selection)
        for name, pin in role['consumers']:
            expected = _edge(producer, output, name, pin)
            existing = _incoming(planned, name, pin)
            if existing is not None and existing != expected:
                raise ValueError('Weighted input has a different authored producer: ' + name + '.' + pin)
            if expected not in planned:
                planned.append(expected)
    skinning = None
    if ensure_skinning_blend:
        terminals = [name for name, info in infos.items() if 'TerminalNode' in info['type']]
        if len(terminals) != 1:
            raise ValueError('Skinning blend requires exactly one cloth terminal')
        terminal = terminals[0]
        terminal_inputs = sorted(pin for pin in _pins(infos[terminal], 'input') if pin.startswith('CollectionLods['))
        terminal_edges = [_incoming(planned, terminal, pin) for pin in terminal_inputs]
        if not terminal_edges or any(edge is None or edge['fromPin'] != 'Collection' for edge in terminal_edges):
            raise ValueError('Every terminal LOD needs an explicit Collection route')
        candidate = [name for name, info in infos.items() if info['type'] == SKINNING]
        if len(candidate) > 1:
            raise ValueError('Multiple skinning blend roles require an explicit per-LOD review')
        if candidate:
            skinning = candidate[0]
            if native.get(skinning, {}).get('active') is not True:
                raise ValueError('Fresh active native skinning-blend state is required')
            if (maximum not in _upstream(planned, skinning) or any(
                    skinning not in _upstream(planned, edge['fromNode']) + [edge['fromNode']]
                    for edge in terminal_edges)):
                raise ValueError('Skinning blend must follow MaxDistance and precede every terminal LOD')
            existing = _incoming(planned, skinning, 'KinematicVertices3D.StringValue')
            expected = _edge(maximum, 'KinematicVertices3D', skinning, 'KinematicVertices3D.StringValue')
            literal = _props(infos[skinning]).get('KinematicVertices3D', '()')
            if (existing is not None and existing != expected) or (existing is None and literal not in (
                    '()', '(StringValue="")', '(StringValue="KinematicVertices3D")')):
                raise ValueError('Existing skinning blend has an authored kinematic source')
        else:
            if any(info['type'] == PAINT and _props(info).get('MeshTarget') == 'Render' and
                   _map_name(info, node_name=name, effective_map_names=effective_map_names) == 'RenderDeformerSkinningBlend'
                   for name, info in infos.items()):
                raise ValueError('An authored render skinning-blend map must not be overwritten')
            tails = {(edge['fromNode'], edge['fromPin']) for edge in terminal_edges}
            if len(tails) != 1:
                raise ValueError('Different LOD Collection tails require separate skinning-blend review')
            tail, _ = next(iter(tails))
            ancestry = _upstream(planned, tail) + [tail]
            if maximum not in ancestry:
                raise ValueError('The terminal Collection bypasses the native MaxDistance route')
            proxy = bool(built.get('render_deformer_sim_indices_3d') and built.get('render_deformer_weight')
                         and built.get('render_deformer_position_bary_coords_and_dist'))
            proxy = proxy or any(name in infos and infos[name]['type'].startswith('FChaosClothAssetProxyDeformerNode')
                                 for name in ancestry)
            if not proxy:
                raise ValueError('Skinning blend requires verified imported or upstream proxy mapping')
            skinning = _name(skinning_node_name)
            if skinning in infos or skinning in {row['name'] for row in additions}:
                raise ValueError('Native skinning-blend node name already exists')
            position = node_rows.get(tail, node_rows[maximum])
            additions.append({'name': skinning, 'type': SKINNING,
                              'parameters': {'bUseSmoothTransition': True},
                              'posX': int(position.get('posX', 0)) + 240,
                              'posY': int(position.get('posY', 0))})
            for edge in terminal_edges:
                planned.remove(edge)
                planned.append(_edge(skinning, 'Collection', terminal, edge['toPin']))
            planned.append(_edge(tail, 'Collection', skinning, 'Collection'))
        selection = _edge(maximum, 'KinematicVertices3D', skinning, 'KinematicVertices3D.StringValue')
        if selection not in planned:
            planned.append(selection)
    # Never preserve an unaccounted name-output branch while deleting its owner.
    for edge in planned:
        if edge['fromNode'] in {row['name'] for row in additions} and edge['fromPin'] == 'WeightMapName':
            if (edge['toNode'], edge['toPin']) not in roles[
                    next(name for name, output in outputs.items() if output[0] == edge['fromNode'])]['consumers']:
                raise ValueError('Generated map has an unrelated name-output consumer')
    planned = _edges(planned)
    for role in roles.values():
        if any(role['producer'] not in _upstream(planned, consumer)
               for consumer, _ in role['consumers']):
            raise ValueError('Map producer must precede every consumer on its Collection route')
    return {'version': 1, 'source_mesh_path': source_mesh_path, 'maximum': maximum,
            'skinning_blend': skinning,
            'green_map': g_map, 'roles': roles, 'add_nodes': additions,
            'update_nodes': updates,
            'remove_nodes': sorted(replacements), 'original_weight_arrays': snapshots,
            'before_connections': connections, 'after_connections': planned,
            'guard': _fingerprint(structure, infos),
            'before_structure': copy.deepcopy(structure), 'before_infos': copy.deepcopy(infos)}


def plan_source_maximum(structure, infos, *, maximum_parameters,
                        import_node_name='GeneratorBindings', node_name='SourceGMaxDistance'):
    """Plan a missing source-MaxDistance override immediately after owned import.

    Caller must obtain Low/High/WeightMap from the verified binding source, rather
    than from an artist response override. Existing MaxDistance nodes and their
    input/output links remain untouched. Use install_weight_routes with a native
    before/after verification callback, then reacquire the graph and plan its G
    route with maximum_node_name=node_name.
    """
    node_name = _name(node_name)
    rows = {row['name']: row for row in structure['nodes']}
    if set(rows) != set(infos) or len(rows) != len(structure['nodes']):
        raise ValueError('Source-MaxDistance graph nodes are unresolved or duplicated')
    if node_name in infos:
        raise ValueError('Source-MaxDistance destination already exists; review the existing role explicitly')
    if (import_node_name not in infos or
            infos[import_node_name]['type'] != 'FChaosClothAssetImportNode'):
        raise ValueError('Source-MaxDistance requires the owned ClothAsset import node')
    if set(maximum_parameters) - {'MaxDistance', 'InKinematic', 'bWarnDuplicateProperty'}:
        raise ValueError('Source-MaxDistance parameters contain unrelated fields')
    weighted = maximum_parameters.get('MaxDistance')
    if (not isinstance(weighted, dict) or set(weighted) - {'bIsAnimatable', 'Low', 'High', 'WeightMap'}
            or not {'Low', 'High', 'WeightMap'} <= set(weighted)
            or any(not isinstance(weighted[key], (int, float)) or isinstance(weighted[key], bool)
                   or not math.isfinite(weighted[key]) or weighted[key] < 0 for key in ('Low', 'High'))
            or ('bIsAnimatable' in weighted and not isinstance(weighted['bIsAnimatable'], bool))):
        raise ValueError('Source-MaxDistance needs verified finite Low/High and an explicit map name')
    map_name = _name(weighted['WeightMap'])
    if maximum_parameters.get('InKinematic', {}) not in ({}, {'StringValue': ''}):
        raise ValueError('An authored source InKinematic selection requires a separate dependency plan')
    parameters = copy.deepcopy(maximum_parameters)
    parameters.setdefault('InKinematic', {'StringValue': ''})
    if 'bWarnDuplicateProperty' in parameters and not isinstance(parameters['bWarnDuplicateProperty'], bool):
        raise ValueError('Source-MaxDistance warning setting must be Boolean')
    before = _edges(structure['connections'])
    for edge in before:
        if edge['fromNode'] not in infos or edge['toNode'] not in infos:
            raise ValueError('Source-MaxDistance connection references an unresolved node')
        if (edge['fromPin'] not in _pins(infos[edge['fromNode']], 'output') or
                edge['toPin'] not in _pins(infos[edge['toNode']], 'input')):
            raise ValueError('Source-MaxDistance connection references an unresolved pin')
        _incoming(before, edge['toNode'], edge['toPin'])
    outgoing = [edge for edge in before if edge['fromNode'] == import_node_name and edge['fromPin'] == 'Collection']
    if not outgoing or any(edge['toPin'] != 'Collection' and not edge['toPin'].startswith('CollectionLods[')
                           for edge in outgoing):
        raise ValueError('Owned import must have explicit Collection consumers')
    after = [edge for edge in before if edge not in outgoing]
    after.append(_edge(import_node_name, 'Collection', node_name, 'Collection'))
    after.extend(_edge(node_name, 'Collection', edge['toNode'], edge['toPin']) for edge in outgoing)
    position = rows[import_node_name]
    return {'version': 1, 'source_mesh_path': None, 'maximum': node_name,
            'skinning_blend': None, 'green_map': map_name, 'roles': {},
            'add_nodes': [{'name': node_name, 'type': MAXIMUM, 'parameters': parameters,
                           'posX': int(position.get('posX', 0)) + 240,
                           'posY': int(position.get('posY', 0))}],
            'update_nodes': [], 'remove_nodes': [], 'original_weight_arrays': {},
            'before_connections': before, 'after_connections': _edges(after),
            'guard': _fingerprint(structure, infos),
            'before_structure': copy.deepcopy(structure), 'before_infos': copy.deepcopy(infos)}


def install_weight_routes(graph, call, structure, infos, nodes, plan, *, dry_run=False,
                          verify=None, read_native=None):
    """Install a reviewed plan; verify(plan, current_nodes) must audit native data.

    Dry-run performs only read calls. Reacquire graph handles after this function:
    replaced paint nodes are removed on success. ``read_native()`` must return a
    fresh native node dump when replacing paint, since private arrays are absent
    from GetNodeInfo. A failed rollback raises
    WeightRouteError(rollback_complete=False), so callers must preserve recovery
    dependencies and must not evaluate/save uncertain state.
    """
    if plan.get('version') != 1 or _fingerprint(structure, infos) != plan['guard']:
        raise ValueError('Weight route plan does not match the supplied graph')
    live_structure = json.loads(call('GetGraphStructure', graph))
    live_infos = {name: json.loads(call('GetNodeInfo', node)) for name, node in nodes.items()}
    if set(nodes) != set(infos) or _fingerprint(live_structure, live_infos) != plan['guard']:
        raise ValueError('Dataflow changed after the weight-route plan was reviewed')
    if plan['remove_nodes']:
        if not callable(read_native):
            raise ValueError('Fresh native paint-array reader is required before replacing paint')
        fresh = read_native()
        fresh = fresh['nodes'] if isinstance(fresh, dict) else fresh
        fresh = {row['name']: row for row in fresh}
        for name, snapshot in plan['original_weight_arrays'].items():
            row = fresh.get(name, {})
            if row.get('active') is not True or row.get('props') != snapshot['properties']:
                raise ValueError('Original paint changed after planning: ' + name)
    changed = bool(plan['add_nodes'] or plan['update_nodes'] or
                   plan['before_connections'] != plan['after_connections'])
    if dry_run or not changed:
        return {'changed': changed, 'dry_run': bool(dry_run), 'nodes': dict(nodes), 'plan': plan}
    if not callable(verify):
        raise ValueError('Native collection verification callback is required before editing')
    current, added, removed, updated = dict(nodes), [], [], []
    before, after = plan['before_connections'], plan['after_connections']
    disconnect = [edge for edge in before if edge not in after]
    connect = [edge for edge in after if edge not in before]

    def link(method, edge):
        result = call(method, current[edge['fromNode']], edge['fromPin'],
                      current[edge['toNode']], edge['toPin'])
        if method == 'ConnectNodePins' and not result:
            raise RuntimeError('Native pin connection failed: ' + str(edge))

    def check_parameters(node, parameters):
        properties = _props(json.loads(call('GetNodeInfo', node)))
        for key, value in parameters.items():
            actual = properties.get(key)
            if key == 'SourceMesh':
                allowed = {value, value + '.' + value.rsplit('/', 1)[-1]}
                matched = _object_path(actual) in allowed
            elif isinstance(value, bool):
                matched = str(actual).lower() == str(value).lower()
            elif isinstance(value, (int, float)):
                try:
                    matched = math.isfinite(float(actual)) and abs(float(actual) - value) <= .500001e-6
                except (ValueError, TypeError):
                    matched = False
            elif isinstance(value, dict):
                defaults = ({'bIsAnimatable': True, 'Low': 0., 'High': 100., 'WeightMap': 'MaxDistance'}
                            if key == 'MaxDistance' else {'StringValue': ''} if key == 'InKinematic' else {})
                try:
                    fields = dict(defaults, **_flat_struct(actual))
                    matched = all(fields.get(field) == expected for field, expected in value.items())
                except (ValueError, TypeError):
                    matched = False
            else:
                matched = str(actual).strip('"') == value
            if not matched:
                raise RuntimeError('Native route property was not applied: ' + key)

    try:
        for row in plan['add_nodes']:
            node = call('AddNode', graph, row['type'], row['name'],
                        json.dumps(row['parameters']), row['posX'], row['posY'])
            if not node:
                raise RuntimeError('Native weight-route node could not be created: ' + row['name'])
            current[row['name']] = node
            added.append(row['name'])
            check_parameters(node, row['parameters'])
        for row in plan['update_nodes']:
            updated.append(row)
            call('UpdateNode', current[row['name']], json.dumps(row['parameters']))
            check_parameters(current[row['name']], row['parameters'])
        for edge in disconnect:
            link('DisconnectNodePins', edge)
        for edge in connect:
            link('ConnectNodePins', edge)
        # Original arrays stay in memory until native verification succeeds.
        # Their orphan nodes cannot contribute to the terminal evaluation.
        staged = json.loads(call('GetGraphStructure', graph))
        if _edges(staged['connections']) != after:
            raise RuntimeError('Native weight-route pins differ from the reviewed plan')
        for name in set(infos) - set(plan['remove_nodes']) - {row['name'] for row in updated}:
            if json.loads(call('GetNodeInfo', current[name])) != infos[name]:
                raise RuntimeError('An unrelated authored node changed: ' + name)
        verification = verify(plan, current)
        if verification is False:
            raise RuntimeError('Native collection verification rejected the route')
        for name in plan['remove_nodes']:
            call('RemoveNode', graph, current[name])
            removed.append(name)
            del current[name]
        final = json.loads(call('GetGraphStructure', graph))
        expected_names = (set(nodes) | set(added)) - set(removed)
        if {row['name'] for row in final['nodes']} != expected_names or _edges(final['connections']) != after:
            raise RuntimeError('Native weight-route commit differs from the reviewed plan')
        return {'changed': True, 'dry_run': False, 'nodes': current, 'plan': plan,
                'verification': verification}
    except Exception as error:
        rollback_error = None
        try:
            for name in reversed(added):
                call('RemoveNode', graph, current[name])
                del current[name]
            for name in removed:
                row = plan['original_weight_arrays'][name]
                node = call('AddNode', graph, row['type'], name,
                            json.dumps(row['restore_parameters']), row['posX'], row['posY'])
                if not node:
                    raise RuntimeError('Original paint node could not be restored: ' + name)
                current[name] = node
            for row in reversed(updated):
                call('UpdateNode', current[row['name']], json.dumps(row['restore_parameters']))
            actual_edges = _edges(json.loads(call('GetGraphStructure', graph))['connections'])
            for edge in actual_edges:
                if edge not in before:
                    link('DisconnectNodePins', edge)
            for edge in before:
                if edge not in actual_edges:
                    link('ConnectNodePins', edge)
            restored = json.loads(call('GetGraphStructure', graph))
            if {row['name'] for row in restored['nodes']} != set(nodes) or _edges(restored['connections']) != before:
                raise RuntimeError('Original weight-route connections did not restore')
            # Removed nodes can be recreated with new GUIDs. The snapshot still
            # retains their original native properties and per-vertex arrays.
            restored_native = read_native() if removed else []
            restored_native = restored_native['nodes'] if isinstance(restored_native, dict) else restored_native
            restored_native = {row['name']: row for row in restored_native}
            for name in removed:
                props = restored_native.get(name, {}).get('props', {})
                original = plan['original_weight_arrays'][name]
                if _map_name({'type': PAINT, 'properties': props}) != _map_name(infos[name]):
                    raise RuntimeError('Original paint output did not restore: ' + name)
                values = [float(value) for value in props.get('VertexWeights', '()').strip('()').split(',') if value]
                if values != original['values']:
                    raise RuntimeError('Original paint array did not restore: ' + name)
        except Exception as failure:
            rollback_error = failure
        if rollback_error:
            message = str(error) + '; rollback failed: ' + str(rollback_error)
        elif removed:
            message = str(error) + '; paint arrays restored with new node GUIDs; exact graph recovery is required'
        else:
            message = str(error) + '; route edits rolled back'
        raise WeightRouteError(message, plan=plan,
                               rollback_complete=rollback_error is None and not removed) from error
