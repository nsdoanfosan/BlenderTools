"""Native route plans protect paint and express dependencies through real pins.

The model checks API failure paths and graph semantics; it does not claim an
Unreal native evaluation. Production callers must supply that separate proof.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location('weight_routes', Path(__file__).parents[1] /
    'src/addons/send2ue/resources/pipeline/ue_hair_weight_routes.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def info(name, kind, props=None, inputs=('Collection',), outputs=('Collection',)):
    return {'name': name, 'guid': name + '-guid', 'type': kind, 'posX': 0, 'posY': 0,
            'inputPins': [{'name': pin, 'type': 'test'} for pin in inputs],
            'outputPins': [{'name': pin, 'type': 'test'} for pin in outputs],
            'properties': props or {}}


def paint(name, map_name, values):
    row = info(name, M.PAINT, {'OutputName': '(StringValue="' + map_name + '")',
        'MeshTarget': 'Simulation', 'MapOverrideType': 'ReplaceAll', 'InputName': '()'},
        inputs=('Collection', 'TransferCollection', 'InputName.StringValue'),
        outputs=('Collection', 'OutputName.StringValue'))
    native = {'name': name, 'type': M.PAINT, 'active': True, 'props': dict(row['properties'],
        Snapshots='(ActiveSnapshot=-1)', bIsFrozen='False',
        VertexWeights='(' + ','.join(map(str, values)) + ')')}
    return row, native


def structure(infos, edges):
    return {'nodes': [{key: value for key, value in row.items() if key != 'properties'}
                      for row in infos.values()], 'connections': copy.deepcopy(edges)}


def fixture(g_paint=False):
    infos = {'GeneratorBindings': info('GeneratorBindings', 'FChaosClothAssetImportNode',
                                       {'ClothAsset': '/Game/VerifiedBinding'}, inputs=()),
             'Distance': info('Distance', M.MAXIMUM,
                {'MaxDistance': '(bIsAnimatable=True,Low=0.000000,High=58.336273,WeightMap="MaxDistance")'},
                inputs=('Collection', 'MaxDistance.WeightMap', 'InKinematic.StringValue'),
                outputs=('Collection', 'KinematicVertices3D')),
             'Body': info('Body', 'FChaosClothAssetSetPhysicsAssetNode',
                          {'PhysicsAsset': '/Game/ExistingArtistCollider'}),
             'Gravity': info('Gravity', 'FChaosClothAssetSimulationGravityConfigNode',
                {'GravityScaleWeighted': '(Low=0.700000,High=1.000000,WeightMap="Travel")'},
                inputs=('Collection', 'GravityScaleWeighted.WeightMap')),
             'Damping': info('Damping', 'FChaosClothAssetSimulationDampingConfigNode',
                {'DampingCoefficientWeighted': '(Low=0.015000,High=0.075000,WeightMap="ArtistPaint")'},
                inputs=('Collection', 'DampingCoefficientWeighted.WeightMap')),
             'Random': info('Random', 'FDataflowRandomFloatSamplerNode',
                            {'Seed': '104', 'Min': '0.200000', 'Max': '0.800000'}, inputs=(), outputs=('Sampler',)),
             'ClothAssetTerminal': info('ClothAssetTerminal', 'FChaosClothAssetTerminalNode_v2',
                                       inputs=('CollectionLods[0]', 'CollectionLods[1]', 'CollectionLods[2]'), outputs=())}
    native = []
    for name, map_name, values in (('DynamicMask', 'Travel', [0, 1, 1]),
                                  ('ArtistMask', 'ArtistPaint', [.2, .8, .5])):
        infos[name], row = paint(name, map_name, values)
        native.append(row)
    chain = ['GeneratorBindings']
    if g_paint:
        infos['GuideOwnG'], row = paint('GuideOwnG', 'MaxDistance', [0, .4, 1])
        native.append(row)
        chain.append('GuideOwnG')
    chain += ['Distance', 'DynamicMask', 'Body', 'Gravity', 'ArtistMask', 'Damping']
    edges = [M._edge(a, 'Collection', b, 'Collection') for a, b in zip(chain, chain[1:])]
    for lod in range(3):
        edges.append(M._edge('Damping', 'Collection', 'ClothAssetTerminal', 'CollectionLods[' + str(lod) + ']'))
    if g_paint:
        edges.append(M._edge('GuideOwnG', 'OutputName.StringValue', 'Distance', 'MaxDistance.WeightMap'))
    edges.append(M._edge('DynamicMask', 'OutputName.StringValue', 'Gravity', 'GravityScaleWeighted.WeightMap'))
    collection = {'built_mapping': {'sim_positions': [[0,0,0], [1,0,0], [0,1,0]],
                                   'sim_triangles': [[0,1,2]], 'kinematic_sim_indices': [0],
                                   'render_deformer_sim_indices_3d': [[[0,1,2]]],
                                   'render_deformer_weight': [[1.]],
                                   'render_deformer_position_bary_coords_and_dist': [[[.2,.4,.4,0.]]]}}
    return structure(infos, edges), infos, native, collection


class NativeModel:
    def __init__(self, graph, infos, native):
        self.infos = copy.deepcopy(infos)
        self.edges = copy.deepcopy(graph['connections'])
        self.native = {row['name']: copy.deepcopy(row) for row in native}
        self.calls = []
        self.green = [0, .4, 1]
        self.fail_connect = False

    def state(self):
        return structure(self.infos, self.edges)

    def handles(self):
        return dict(zip(self.infos, self.infos))

    def read_native(self):
        return [copy.deepcopy(self.native.get(name, {
            'name': name, 'type': row['type'], 'active': True,
            'props': dict(row['properties'], bIsFrozen='False')})) for name, row in self.infos.items()]

    def call(self, method, *args):
        self.calls.append(method)
        if method == 'GetGraphStructure':
            return json.dumps(self.state())
        if method == 'GetNodeInfo':
            return json.dumps(self.infos[args[0]])
        if method == 'AddNode':
            _, kind, name, text, x, y = args
            params = json.loads(text)
            if kind == M.PAINT:
                row, native = paint(name, params['OutputName']['StringValue'], params['VertexWeights'])
                self.native[name] = native
            elif kind == M.MAXIMUM:
                def export(values):
                    fields = []
                    for key, value in values.items():
                        text = ('True' if value else 'False') if isinstance(value, bool) else (
                            '"' + value + '"' if isinstance(value, str) else str(value))
                        fields.append(key + '=' + text)
                    return '(' + ','.join(fields) + ')'
                props = {key: export(value) if isinstance(value, dict) else str(value)
                         for key, value in params.items()}
                row = info(name, kind, props,
                    inputs=('Collection', 'MaxDistance.WeightMap', 'InKinematic.StringValue'),
                    outputs=('Collection', 'KinematicVertices3D'))
            else:
                props = {key: str(value) for key, value in params.items()}
                row = info(name, kind, props,
                    inputs={'Collection'} | ({'SourceMesh'} if kind == M.GREEN else
                        {'KinematicVertices3D.StringValue'} if kind == M.SKINNING else {'SelectionName'}),
                    outputs=('Collection', 'SkinningBlendName') if kind == M.SKINNING else ('Collection', 'WeightMapName'))
            row.update(posX=x, posY=y)
            self.infos[name] = row
            return name
        if method == 'RemoveNode':
            _, name = args
            self.infos.pop(name)
            self.native.pop(name, None)
            self.edges = [edge for edge in self.edges if edge['fromNode'] != name and edge['toNode'] != name]
            return None
        if method == 'UpdateNode':
            name, text = args
            self.infos[name]['properties'].update({key: str(value) for key, value in json.loads(text).items()})
            return None
        if method in ('ConnectNodePins', 'DisconnectNodePins'):
            edge = M._edge(*args)
            if method == 'ConnectNodePins':
                if self.fail_connect:
                    self.fail_connect = False
                    return False
                if M._incoming(self.edges, edge['toNode'], edge['toPin']):
                    raise RuntimeError('An input was overwritten without disconnecting it')
                self.edges.append(edge)
                return True
            self.edges.remove(edge)
            return None
        raise AssertionError(method)

    def evaluate_terminal(self):
        """Independent native-node model following only the terminal Collection."""
        maps, selected = {'MaxDistance': [0, .4, 1]}, set()
        terminal = M._incoming(self.edges, 'ClothAssetTerminal', 'CollectionLods[0]')['fromNode']
        chain = list(reversed(M._upstream(self.edges, terminal))) + [terminal]
        for name in chain:
            row = self.infos[name]
            kind, props = row['type'], row['properties']
            if kind == M.GREEN:
                maps[props['WeightMapName']] = list(self.green)
            elif kind == M.MAXIMUM:
                selected = {i for i, value in enumerate(maps['MaxDistance']) if value == 0}
            elif kind == M.MASK:
                source = M._incoming(self.edges, name, 'SelectionName')
                if source != M._edge('Distance', 'KinematicVertices3D', name, 'SelectionName'):
                    raise AssertionError('No actual native kinematic-name dependency')
                maps[props['WeightMapName']] = [0. if i in selected else 1. for i in range(3)]
            elif kind == M.PAINT:
                native = self.native[name]['props']
                output = M._map_name(row)
                maps[output] = [float(value) for value in native['VertexWeights'].strip('()').split(',')]
            elif kind == M.SKINNING:
                maps['RenderDeformerSkinningBlend'] = [len(selected) / 3.]
        return maps


class WeightRouteTests(unittest.TestCase):
    def plan(self, *, g_paint=False):
        graph, infos, native, collection = fixture(g_paint)
        plan = M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
            collection=collection, derived_maps=['Travel'],
            expected_green_weights=[0, .4, 1] if g_paint else None)
        return graph, infos, native, collection, plan

    def test_plan_adds_explicit_green_selection_and_consumer_names_without_mutation(self):
        graph, infos, native, collection = fixture()
        previous = copy.deepcopy((graph, infos, native, collection))
        plan = M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                    collection=collection, derived_maps=['Travel'])
        self.assertEqual((graph, infos, native, collection), previous)
        self.assertEqual(plan['remove_nodes'], ['DynamicMask'])
        self.assertEqual(plan['original_weight_arrays']['DynamicMask']['values'], [0, 1, 1])
        self.assertIn(M._edge('SimulationGreenWeights', 'WeightMapName', 'Distance', 'MaxDistance.WeightMap'), plan['after_connections'])
        self.assertIn(M._edge('Distance', 'KinematicVertices3D', 'DynamicMask_FromKinematic', 'SelectionName'), plan['after_connections'])
        self.assertIn(M._edge('DynamicMask_FromKinematic', 'WeightMapName', 'Gravity', 'GravityScaleWeighted.WeightMap'), plan['after_connections'])
        self.assertEqual(len(plan['add_nodes']), 3)
        json.dumps(plan)

    def test_verified_source_build_paint_is_replaced_by_native_green(self):
        graph, infos, native, collection, plan = self.plan(g_paint=True)
        self.assertEqual(plan['remove_nodes'], ['DynamicMask', 'GuideOwnG'])
        self.assertIn(M._edge('GeneratorBindings', 'Collection', 'SimulationGreenWeights', 'Collection'), plan['after_connections'])
        self.assertIn(M._edge('SimulationGreenWeights', 'Collection', 'Distance', 'Collection'), plan['after_connections'])
        self.assertEqual(plan['original_weight_arrays']['GuideOwnG']['values'], [0, .4, 1])

    def test_source_green_paint_requires_an_exact_external_source_proof(self):
        graph, infos, native, collection = fixture(True)
        for expected in (None, [0, .7, 1], [0, float('nan'), 1]):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, 'caller-proven'):
                    M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                        collection=collection, expected_green_weights=expected)

    def test_arbitrary_mask_paint_is_rejected_when_named_as_generated(self):
        graph, infos, native, collection = fixture()
        native[0]['props']['VertexWeights'] = '(0,0.4,1)'
        with self.assertRaisesRegex(ValueError, 'not the exact old kinematic'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_unlisted_artist_paint_is_never_automatically_converted(self):
        graph, infos, native, collection = fixture()
        plan = M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                    collection=collection)
        self.assertEqual(plan['remove_nodes'], [])
        self.assertEqual(len(plan['add_nodes']), 2)

    def test_constant_old_masks_are_ambiguous_and_rejected(self):
        for pins, values in (([], '(1,1,1)'), ([0,1,2], '(0,0,0)')):
            with self.subTest(pins=pins):
                graph, infos, native, collection = fixture()
                collection['built_mapping']['kinematic_sim_indices'] = pins
                native[0]['props']['VertexWeights'] = values
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                          collection=collection, derived_maps=['Travel'])

    def test_authored_snapshots_and_auxiliary_pins_are_rejected(self):
        graph, infos, native, collection = fixture()
        native[0]['props']['Snapshots'] = '(ActiveSnapshot=0)'
        with self.assertRaisesRegex(ValueError, 'authored paint state'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])
        native[0]['props']['Snapshots'] = '(ActiveSnapshot=-1)'
        graph['connections'].append(M._edge('GeneratorBindings', 'Collection', 'DynamicMask', 'TransferCollection'))
        with self.assertRaisesRegex(ValueError, 'authored auxiliary input'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_an_existing_different_weight_input_producer_is_not_overwritten(self):
        graph, infos, native, collection = fixture()
        infos['MapName'] = info('MapName', 'FGetDataflowVariableNode', outputs=('Value',), inputs=())
        graph['nodes'] = structure(infos, [])['nodes']
        graph['connections'].append(M._edge('MapName', 'Value', 'Distance', 'MaxDistance.WeightMap'))
        with self.assertRaisesRegex(ValueError, 'different authored producer'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection)

    def test_duplicate_map_producers_are_rejected(self):
        graph, infos, native, collection = fixture()
        infos['SecondMask'], extra = paint('SecondMask', 'Travel', [0, 1, 1])
        native.append(extra)
        graph['nodes'] = structure(infos, [])['nodes']
        with self.assertRaisesRegex(ValueError, 'exactly one explicit producer'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_downstream_only_rule_prevents_a_mask_dependency_cycle(self):
        graph, infos, native, collection = fixture()
        graph['connections'] = [edge for edge in graph['connections'] if edge['toNode'] not in ('DynamicMask', 'Distance')]
        graph['connections'] += [M._edge('GeneratorBindings', 'Collection', 'DynamicMask', 'Collection'),
                                M._edge('DynamicMask', 'Collection', 'Distance', 'Collection')]
        with self.assertRaisesRegex(ValueError, 'must follow MaxDistance'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_explicit_source_maximum_preserves_a_separate_artist_maximum(self):
        graph, infos, native, collection = fixture()
        infos['Distance']['properties']['MaxDistance'] = '(Low=0,High=25,WeightMap="ArtistResponse")'
        source = M.plan_source_maximum(graph, infos, maximum_parameters={
            'MaxDistance': {'bIsAnimatable': True, 'Low': 0., 'High': 8., 'WeightMap': 'MaxDistance'}})
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), source, verify=lambda *_: True)
        with self.assertRaisesRegex(ValueError, 'explicit maximum_node_name'):
            M.plan_weight_routes(model.state(), model.infos, '/Game/Hair_GuideSim',
                native_nodes=model.read_native(), collection=collection)
        plan = M.plan_weight_routes(model.state(), model.infos, '/Game/Hair_GuideSim',
            native_nodes=model.read_native(), collection=collection, maximum_node_name='SourceGMaxDistance')
        self.assertEqual(plan['maximum'], 'SourceGMaxDistance')
        self.assertEqual(plan['green_map'], 'MaxDistance')
        self.assertEqual(plan['roles']['MaxDistance']['consumers'], [('SourceGMaxDistance', 'MaxDistance.WeightMap')])
        self.assertEqual(model.infos['Distance'], infos['Distance'])
        self.assertIn(M._edge('SourceGMaxDistance', 'Collection', 'Distance', 'Collection'),
                      plan['after_connections'])
        self.assertEqual(plan['remove_nodes'], [])

    def test_dry_run_reads_but_does_not_mutate_native_graph(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        result = M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                         dry_run=True, read_native=model.read_native)
        self.assertTrue(result['changed'])
        self.assertEqual(set(model.calls), {'GetGraphStructure', 'GetNodeInfo'})
        self.assertEqual(model.state(), graph)

    def test_stale_plan_is_rejected_before_first_mutation(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        model.infos['Body']['properties']['PhysicsAsset'] = '/Game/ArtistNewCollider'
        with self.assertRaisesRegex(ValueError, 'changed after'):
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                     verify=lambda *_: True, read_native=model.read_native)
        self.assertNotIn('AddNode', model.calls)

    def test_no_install_without_native_collection_verification(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        with self.assertRaisesRegex(ValueError, 'verification callback'):
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                     read_native=model.read_native)
        self.assertNotIn('AddNode', model.calls)

    def test_install_preserves_artist_maps_collider_random_and_terminal_lods(self):
        graph, infos, native, _, plan = self.plan(g_paint=True)
        model = NativeModel(graph, infos, native)
        result = M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
            verify=lambda *_: self.assertEqual(model.evaluate_terminal()['ArtistPaint'], [.2, .8, .5]),
            read_native=model.read_native)
        self.assertTrue(result['changed'])
        for name in ('Body', 'Random', 'ArtistMask', 'Distance', 'Gravity', 'Damping', 'ClothAssetTerminal'):
            self.assertEqual(model.infos[name], infos[name])
        self.assertNotIn('DynamicMask', model.infos)
        self.assertNotIn('GuideOwnG', model.infos)
        self.assertEqual({edge['toPin'] for edge in model.edges if edge['toNode'] == 'ClothAssetTerminal'},
                         {edge['toPin'] for edge in graph['connections'] if edge['toNode'] == 'ClothAssetTerminal'})
        self.assertEqual({edge['fromNode'] for edge in model.edges if edge['toNode'] == 'ClothAssetTerminal'},
                         {'SimulationKinematicBlend'})

    def test_native_dependency_model_refreshes_all_zero_all_one_and_pin_moves_repeatedly(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                verify=lambda *_: True, read_native=model.read_native)
        for green, expected in (([0,.4,1], [0,1,1]), ([0,0,0], [0,0,0]),
                                 ([1,1,1], [1,1,1]), ([1,0,1], [1,0,1]),
                                 ([0,0,0], [0,0,0]), ([.4,1,0], [1,1,0])):
            with self.subTest(green=green):
                model.green = green
                maps = model.evaluate_terminal()
                self.assertEqual(maps['MaxDistance'], green)
                self.assertEqual(maps['Travel'], expected)
                self.assertEqual(maps['ArtistPaint'], [.2,.8,.5])
                self.assertEqual(maps['RenderDeformerSkinningBlend'], [expected.count(0) / 3.])

    def test_native_route_is_idempotent_even_after_all_pins_or_no_pins(self):
        graph, infos, native, collection, plan = self.plan()
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                verify=lambda *_: True, read_native=model.read_native)
        for pins in ([], [0,1,2]):
            collection['built_mapping']['kinematic_sim_indices'] = pins
            again = M.plan_weight_routes(model.state(), model.infos, '/Game/Hair_GuideSim',
                native_nodes=model.read_native(), collection=collection, derived_maps=['Travel'])
            self.assertEqual(again['add_nodes'], [])
            self.assertEqual(again['remove_nodes'], [])
            self.assertEqual(again['before_connections'], again['after_connections'])

    def test_native_green_source_rebind_needs_explicit_intent_and_changes_only_reference(self):
        graph, infos, native, collection, plan = self.plan()
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                verify=lambda *_: True, read_native=model.read_native)
        original = copy.deepcopy(model.infos)
        with self.assertRaisesRegex(ValueError, 'explicit source rebind'):
            M.plan_weight_routes(model.state(), model.infos, '/Game/Renamed_GuideSim',
                native_nodes=model.read_native(), collection=collection, derived_maps=['Travel'])
        rebound = M.plan_weight_routes(model.state(), model.infos, '/Game/Renamed_GuideSim',
            native_nodes=model.read_native(), collection=collection, derived_maps=['Travel'],
            allow_source_rebind=True)
        self.assertEqual(rebound['add_nodes'], [])
        self.assertEqual(rebound['remove_nodes'], [])
        self.assertEqual(rebound['update_nodes'], [{'name': 'SimulationGreenWeights',
            'parameters': {'SourceMesh': '/Game/Renamed_GuideSim'},
            'restore_parameters': {'SourceMesh': '/Game/Hair_GuideSim'}}])
        M.install_weight_routes(None, model.call, model.state(), model.infos, model.handles(), rebound,
                                verify=lambda *_: True)
        self.assertEqual(model.infos['SimulationGreenWeights']['properties']['SourceMesh'], '/Game/Renamed_GuideSim')
        model.infos['SimulationGreenWeights']['properties']['SourceMesh'] = '/Game/Hair_GuideSim'
        self.assertEqual(model.infos, original)

    def test_failed_rebind_verification_restores_original_source_reference(self):
        graph, infos, native, collection, plan = self.plan()
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                verify=lambda *_: True, read_native=model.read_native)
        previous = copy.deepcopy(model.infos)
        rebound = M.plan_weight_routes(model.state(), model.infos, '/Game/New_GuideSim',
            native_nodes=model.read_native(), collection=collection, derived_maps=['Travel'],
            allow_source_rebind=True)
        with self.assertRaises(M.WeightRouteError) as caught:
            M.install_weight_routes(None, model.call, model.state(), model.infos, model.handles(), rebound,
                                    verify=lambda *_: False)
        self.assertTrue(caught.exception.rollback_complete)
        self.assertEqual(model.infos, previous)

    def test_private_paint_array_change_is_detected_even_when_node_info_is_unchanged(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        model.native['DynamicMask']['props']['VertexWeights'] = '(0,0.6,1)'
        with self.assertRaisesRegex(ValueError, 'Original paint changed after planning'):
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                    verify=lambda *_: True, read_native=model.read_native)
        self.assertNotIn('AddNode', model.calls)

    def test_existing_skinning_blend_keeps_artist_smoothing_and_gets_the_name_pin(self):
        graph, infos, native, collection = fixture()
        infos['ExistingBlend'] = info('ExistingBlend', M.SKINNING,
            {'KinematicVertices3D': '(StringValue="KinematicVertices3D")', 'bUseSmoothTransition': 'False'},
            inputs=('Collection', 'KinematicVertices3D.StringValue'), outputs=('Collection', 'SkinningBlendName'))
        native.append({'name': 'ExistingBlend', 'type': M.SKINNING, 'active': True,
                       'props': dict(infos['ExistingBlend']['properties'], bIsFrozen='False')})
        for edge in graph['connections']:
            if edge['toNode'] == 'ClothAssetTerminal':
                edge['fromNode'] = 'ExistingBlend'
        graph['connections'].append(M._edge('Damping', 'Collection', 'ExistingBlend', 'Collection'))
        graph['nodes'] = structure(infos, [])['nodes']
        plan = M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                    collection=collection, derived_maps=['Travel'])
        self.assertEqual(plan['skinning_blend'], 'ExistingBlend')
        self.assertNotIn(M.SKINNING, [row['type'] for row in plan['add_nodes']])
        self.assertIn(M._edge('Distance', 'KinematicVertices3D', 'ExistingBlend', 'KinematicVertices3D.StringValue'),
                      plan['after_connections'])
        self.assertEqual(infos['ExistingBlend']['properties']['bUseSmoothTransition'], 'False')

    def test_no_proxy_mapping_cannot_silently_pass_through_a_new_blend(self):
        graph, infos, native, collection = fixture()
        collection['built_mapping'].pop('render_deformer_sim_indices_3d')
        with self.assertRaisesRegex(ValueError, 'verified imported or upstream proxy'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_artist_render_skinning_blend_paint_is_not_overwritten(self):
        graph, infos, native, collection = fixture()
        infos['ArtistRender'], row = paint('ArtistRender', 'RenderDeformerSkinningBlend', [.2,.8,.4])
        infos['ArtistRender']['properties']['MeshTarget'] = 'Render'
        row['props']['MeshTarget'] = 'Render'
        native.append(row)
        graph['nodes'] = structure(infos, [])['nodes']
        with self.assertRaisesRegex(ValueError, 'authored render skinning-blend map'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_different_lod_tails_require_an_explicit_review(self):
        graph, infos, native, collection = fixture()
        for edge in graph['connections']:
            if edge['toNode'] == 'ClothAssetTerminal' and edge['toPin'] == 'CollectionLods[2]':
                edge['fromNode'] = 'Gravity'
        with self.assertRaisesRegex(ValueError, 'Different LOD Collection tails'):
            M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                  collection=collection, derived_maps=['Travel'])

    def test_addnode_silently_ignoring_a_property_is_detected_and_rolled_back(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        def ignored_parameter(method, *args):
            result = model.call(method, *args)
            if method == 'AddNode' and args[1] == M.GREEN:
                model.infos[result]['properties']['ScalingFactor'] = '2.000000'
            return result
        with self.assertRaisesRegex(M.WeightRouteError, 'property was not applied') as caught:
            M.install_weight_routes(None, ignored_parameter, graph, infos, model.handles(), plan,
                                    verify=lambda *_: True, read_native=model.read_native)
        self.assertTrue(caught.exception.rollback_complete)
        self.assertEqual(model.state(), graph)

    def test_wired_name_output_takes_precedence_over_a_stale_literal_name(self):
        graph, infos, native, collection = fixture(g_paint=True)
        infos['Distance']['properties']['MaxDistance'] = '(Low=0,High=58,WeightMap="OldLiteral")'
        plan = M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
            collection=collection, derived_maps=['Travel'], expected_green_weights=[0,.4,1])
        self.assertEqual(plan['green_map'], 'MaxDistance')
        self.assertIn(M._edge('SimulationGreenWeights', 'WeightMapName', 'Distance', 'MaxDistance.WeightMap'),
                      plan['after_connections'])

    def test_failed_native_connection_restores_original_pins_and_arrays(self):
        graph, infos, native, _, plan = self.plan(g_paint=True)
        model = NativeModel(graph, infos, native)
        model.fail_connect = True
        with self.assertRaises(M.WeightRouteError) as caught:
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                    verify=lambda *_: True, read_native=model.read_native)
        self.assertTrue(caught.exception.rollback_complete)
        self.assertEqual(model.state()['nodes'], graph['nodes'])
        self.assertEqual(M._edges(model.state()['connections']), M._edges(graph['connections']))
        self.assertEqual(list(model.native.values()), native)

    def test_rejected_native_collection_restores_original_pins_and_arrays(self):
        graph, infos, native, _, plan = self.plan()
        model = NativeModel(graph, infos, native)
        with self.assertRaises(M.WeightRouteError) as caught:
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                    verify=lambda *_: False, read_native=model.read_native)
        self.assertTrue(caught.exception.rollback_complete)
        self.assertEqual(model.state()['nodes'], graph['nodes'])
        self.assertEqual(M._edges(model.state()['connections']), M._edges(graph['connections']))
        self.assertEqual(list(model.native.values()), native)


class SourceMaximumPlanTests(unittest.TestCase):
    PARAMETERS = {'MaxDistance': {'bIsAnimatable': True, 'Low': 0., 'High': 8., 'WeightMap': 'MaxDistance'}}

    def test_source_maximum_fanout_insertion_only_changes_import_collection_edges(self):
        graph, infos, native, _ = fixture()
        before = copy.deepcopy((graph, infos, native))
        plan = M.plan_source_maximum(graph, infos, maximum_parameters=self.PARAMETERS)
        self.assertEqual((graph, infos, native), before)
        self.assertEqual(plan['remove_nodes'], [])
        self.assertEqual(plan['update_nodes'], [])
        self.assertEqual(plan['add_nodes'][0]['type'], M.MAXIMUM)
        self.assertIn(M._edge('GeneratorBindings', 'Collection', 'SourceGMaxDistance', 'Collection'),
                      plan['after_connections'])
        for edge in graph['connections']:
            if edge['fromNode'] != 'GeneratorBindings':
                self.assertIn(edge, plan['after_connections'])
        model = NativeModel(graph, infos, native)
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan, verify=lambda *_: True)
        for name in infos:
            self.assertEqual(model.infos[name], infos[name])
        self.assertEqual(model.infos['SourceGMaxDistance']['properties']['MaxDistance'],
                         '(bIsAnimatable=True,Low=0.0,High=8.0,WeightMap="MaxDistance")')

    def test_rejected_source_maximum_native_baseline_restores_original_graph(self):
        graph, infos, native, _ = fixture()
        plan = M.plan_source_maximum(graph, infos, maximum_parameters=self.PARAMETERS)
        model = NativeModel(graph, infos, native)
        with self.assertRaises(M.WeightRouteError) as caught:
            M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan, verify=lambda *_: False)
        self.assertTrue(caught.exception.rollback_complete)
        self.assertEqual(model.infos, infos)
        self.assertEqual(M._edges(model.edges), M._edges(graph['connections']))

    def test_an_authored_input_selection_or_unverified_parameters_are_rejected(self):
        graph, infos, _, _ = fixture()
        invalid = [dict(self.PARAMETERS, InKinematic={'StringValue': 'ArtistPins'}),
                   {'MaxDistance': {'Low': 0, 'High': float('nan'), 'WeightMap': 'MaxDistance'}},
                   {'MaxDistance': {'Low': 0, 'WeightMap': 'MaxDistance'}},
                   dict(self.PARAMETERS, UnrelatedSolverProperty=3)]
        for params in invalid:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    M.plan_source_maximum(graph, infos, maximum_parameters=params)

    def test_existing_destination_is_never_reused_implicitly(self):
        graph, infos, _, _ = fixture()
        with self.assertRaisesRegex(ValueError, 'destination already exists'):
            M.plan_source_maximum(graph, infos, maximum_parameters=self.PARAMETERS, node_name='Distance')


if __name__ == '__main__':
    unittest.main()
