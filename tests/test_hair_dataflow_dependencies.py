"""Regression model for graph authoring preservation, not a Chaos simulation."""
import copy
import json
import unittest

from test_ue_hair_guide_cloth_external import ExternalGraphHarness, Node, M
from test_ue_hair_guide_cloth import fixture


def edge(a, output, b, input_pin):
    return dict(fromNode=a, fromPin=output, toNode=b, toPin=input_pin)


def add(graph, name, typ, properties):
    graph.nodes[name] = Node(graph, name, typ, properties, f'{len(graph.nodes) + 500:032x}')


def authored_template(h):
    graph = h.graph(h.source)
    add(graph, 'ArtistMaxDistance', 'FChaosClothAssetSimulationMaxDistanceConfigNode',
        {'MaxDistance': {'Low': 0., 'High': 8., 'WeightMap': 'MaxDistance'}})
    add(graph, 'ArtistSelection', 'FChaosClothAssetSelectionNode_v2',
        {'OutputName': '(StringValue="ArtistRoots")', 'Indices': '(0)', 'Group': '(Name="SimVertices3D")'})
    add(graph, 'ArtistSolver', 'FChaosClothAssetSimulationSolverConfigNode', {'NumIterations': 99})
    add(graph, 'ReadSelection', 'FGetDataflowVariableNode',
        {'VariableName': 'RootSelection', 'VariablePropertyBag': '(Value="ArtistRoots")'})
    graph.context['variables']['RootSelection'] = 'ArtistRoots'
    graph.connections = [edge(a, 'Collection', b, 'Collection') for a, b in zip(
        ['SourceInput', 'ArtistSelection', 'ArtistMaxDistance'],
        ['ArtistSelection', 'ArtistMaxDistance', 'ArtistSolver'])]
    graph.connections += [edge('ArtistSolver', 'Collection', 'ClothAssetTerminal', f'CollectionLods[{i}]')
                          for i in range(3)]
    graph.connections += [edge('ReadSelection', 'Value', 'ArtistMaxDistance', 'InKinematic.StringValue'),
                          edge('ReadSelection', 'Value', 'ArtistSelection', 'InputName.StringValue')]
    return graph


class DataflowDependencyTests(unittest.TestCase):
    def test_native_sampler_inputs_keep_int_float_struct_variables_and_float_fanout(self):
        infos = {
            'Damping': {'type': 'FChaosClothAssetSimulationDampingConfigNode',
                        'inputPins': [{'name': 'DampingCoefficientWeighted.WeightMap'}]},
            'Map': {'type': 'FDataflowSamplerToAttributeNode', 'properties': {
                'AttributeName': 'ArtistResponse', 'VertexGroup': '(Name="SimVertices3D")',
                'bSaveAsColor': 'False', 'bUseDefaultValue': 'False'}},
            'Random': {'type': 'FDataflowRandomFloatSamplerNode',
                       'inputPins': [{'name': name} for name in ('Seed', 'Min', 'Max', 'RenderBounds')]},
        }
        for name, typ, value in (('Seed', 'Int32', '42'), ('Range', 'Float', '.5'),
                                 ('Bounds', 'Struct', '(Min=(X=-5,Y=-5,Z=-5),Max=(X=5,Y=5,Z=5))')):
            infos[name] = {'type': 'FGetDataflowVariableNode', 'properties': {
                'VariableName': name, 'VariablePropertyBag': f'(Type={typ},Value={value})'}}
        connections = [edge('Seed', 'Value', 'Random', 'Seed'),
                       edge('Range', 'Value', 'Random', 'Min'), edge('Range', 'Value', 'Random', 'Max'),
                       edge('Bounds', 'Value', 'Random', 'RenderBounds'),
                       edge('Random', 'Sampler', 'Map', 'Sampler'),
                       edge('Map', 'AttributeName', 'Damping', 'DampingCoefficientWeighted.WeightMap'),
                       edge('Map', 'Collection', 'Damping', 'Collection')]
        before = copy.deepcopy((infos, connections))
        self.assertEqual(M._configuration_dependencies(infos, connections, {'Map', 'Damping'}),
                         {'Random', 'Seed', 'Range', 'Bounds'})
        self.assertEqual(M._native_response_attributes(infos, connections, set(infos), {'Map', 'Damping'}),
                         {'ArtistResponse'})
        self.assertEqual((infos, connections), before)

    def test_template_keeps_variable_bag_typed_fanout_and_selection_chain(self):
        for external in (False, True):
            with self.subTest(external=external):
                h = ExternalGraphHarness(external_source=external)
                original = authored_template(h)
                before = original.snapshot()
                overrides = copy.deepcopy(h.source.instance.values['variable_overrides'])
                built, _ = h.build()
                graph = h.graph(built)
                self.assertEqual(original.snapshot(), before)
                self.assertEqual(graph.context, original.context)
                self.assertEqual(built.instance.values['variable_overrides'], overrides)
                for name in ('ReadSelection', 'ArtistSelection', 'ArtistSolver'):
                    self.assertEqual(graph.nodes[name].properties, original.nodes[name].properties)
                    self.assertEqual(graph.nodes[name].guid, original.nodes[name].guid)
                    self.assertIsNot(graph.nodes[name], original.nodes[name])
                for row in before['connections']:
                    if row['fromNode'].startswith('Read'):
                        self.assertIn(row, graph.connections)
                self.assertIn(edge('BodyPhysics', 'Collection', 'ArtistSelection', 'Collection'), graph.connections)
                self.assertIn(edge('ArtistSelection', 'Collection', 'ArtistMaxDistance', 'Collection'), graph.connections)
                self.assertNotIn('SourceInput', graph.nodes)

    def test_template_keeps_selection_branch_and_rebinds_its_collection(self):
        h = ExternalGraphHarness()
        graph = authored_template(h)
        graph.connections = [row for row in graph.connections
                             if row not in (edge('ArtistSelection', 'Collection', 'ArtistMaxDistance', 'Collection'),
                                            edge('ReadSelection', 'Value', 'ArtistMaxDistance', 'InKinematic.StringValue'))]
        graph.connections.append(edge('SourceInput', 'Collection', 'ArtistMaxDistance', 'Collection'))
        graph.connections.append(edge('ArtistSelection', 'OutputName.StringValue', 'ArtistMaxDistance', 'InKinematic.StringValue'))
        built, _ = h.build()
        self.assertIn(edge('BodyPhysics', 'Collection', 'ArtistSelection', 'Collection'), h.graph(built).connections)
        self.assertIn(edge('ArtistSelection', 'OutputName.StringValue', 'ArtistMaxDistance', 'InKinematic.StringValue'),
                      h.graph(built).connections)

    def test_template_does_not_silently_drop_an_unsupported_connected_producer(self):
        h = ExternalGraphHarness()
        graph = authored_template(h)
        add(graph, 'ForeignMesh', 'FChaosClothAssetDeleteElementNode', {})
        graph.connections.append(edge('ForeignMesh', 'Collection', 'ArtistSelection', 'TransferCollection'))
        original = graph.snapshot()
        with self.assertRaisesRegex(ValueError, 'unsupported configuration dependency'):
            h.build()
        self.assertEqual(graph.snapshot(), original)
        self.assertNotIn('/Game/Fixtures/NewBuild', h.assets)

    def test_republication_keeps_variables_and_selection_on_all_lods(self):
        h = ExternalGraphHarness()
        original = h.graph(h.final)
        add(original, 'ReadGust', 'FGetDataflowVariableNode',
            {'VariableName': 'RootSelection', 'VariablePropertyBag': '(Value="Roots")'})
        original.context['variables']['RootSelection'] = 'Roots'
        add(original, 'ArtistSelection', 'FChaosClothAssetSelectionNode_v2',
            {'OutputName': '(StringValue="Roots")', 'Indices': '(0)'})
        add(original, 'ArtistCopySelection', 'FChaosClothAssetProceduralSelectionNode',
            {'OutputName': 'CopyRoots', 'SelectionType': 'Conversion'})
        for row in original.connections:
            if row['fromNode'] == 'ArtistPaint' and row['fromPin'] == 'Collection':
                row['fromNode'] = 'ArtistCopySelection'
        original.connections += [edge('ArtistPaint', 'Collection', 'ArtistSelection', 'Collection'),
                                 edge('ArtistSelection', 'Collection', 'ArtistCopySelection', 'Collection'),
                                 edge('ReadGust', 'Value', 'ArtistSelection', 'InputName.StringValue'),
                                 edge('ReadGust', 'Value', 'ArtistCopySelection', 'ConversionInputName.StringValue'),
                                 edge('ArtistSelection', 'OutputName.StringValue', 'ArtistMaxDistance', 'InKinematic.StringValue')]
        before = original.snapshot()
        instance = M._graph_context(h.final)
        for binding in (h.new_binding, '/Game/Fixtures/NextBinding'):
            h.new_binding = binding
            h.publish()
            graph = h.graph(h.final)
            self.assertEqual(graph.context, original.context)
            self.assertEqual(graph.connections, original.connections)
            self.assertEqual(M._graph_context(h.final), instance)
            self.assertEqual(graph.nodes['ReadGust'].properties, original.nodes['ReadGust'].properties)
            self.assertEqual(graph.nodes['ArtistSelection'].properties, original.nodes['ArtistSelection'].properties)
            h.old_binding = binding
        self.assertEqual(original.snapshot(), before)

    def test_variable_cycle_and_foreign_geometry_are_rejected_before_final_writes(self):
        for kind in ('cycle', 'geometry', 'import_override', 'duplicate_input'):
            with self.subTest(kind=kind):
                h = ExternalGraphHarness()
                graph = h.graph(h.final)
                if kind == 'geometry':
                    add(graph, 'Foreign', 'FChaosClothAssetDeleteElementNode', {})
                    graph.connections.append(edge('Foreign', 'Collection', 'ArtistSolver', 'TransferCollection'))
                else:
                    add(graph, 'Variable', 'FGetDataflowVariableNode', {'VariableName': 'GustScale'})
                    graph.connections.append(edge('Variable', 'Value', 'ArtistSolver', 'NumIterations'))
                    if kind == 'cycle':
                        graph.connections.append(edge('ArtistSolver', 'Value', 'Variable', 'Value'))
                    elif kind == 'import_override':
                        graph.connections.append(edge('Variable', 'Value', 'GeneratorBindings', 'ClothAsset'))
                    else:
                        graph.connections.append(edge('Variable', 'Value', 'ArtistSolver', 'NumIterations'))
                original = graph.snapshot()
                with self.assertRaises(ValueError):
                    h.publish()
                self.assertEqual(graph.snapshot(), original)
                self.assertNotIn(h.final, h.evaluations)

    def test_selection_to_weight_map_requires_new_explicit_map_and_native_consumers(self):
        infos = {
            'SelectionMap': {'type': 'FChaosClothAssetSelectionToWeightMapNode',
                             'properties': {'WeightMapName': 'ArtistDynamicMask'}},
            'Damping': {'type': 'FChaosClothAssetSimulationDampingConfigNode',
                        'inputPins': [{'name': 'DampingCoefficientWeighted.WeightMap'}]}}
        connections = [edge('SelectionMap', 'WeightMapName', 'Damping', 'DampingCoefficientWeighted.WeightMap')]
        self.assertEqual(M._selection_weight_map_attributes(infos, connections, set(infos)), {'ArtistDynamicMask'})
        for name in ('', 'MaxDistance', 'CodexParentForceKey', 'ArtistPaint'):
            infos['SelectionMap']['properties']['WeightMapName'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                M._selection_weight_map_attributes(infos, connections, set(infos), {'ArtistPaint'})
        infos['SelectionMap']['properties']['WeightMapName'] = 'ArtistDynamicMask'
        with self.assertRaisesRegex(ValueError, 'explicit native weighted physics consumer'):
            M._selection_weight_map_attributes(infos, [], set(infos))

    def test_republication_preserves_explicit_selection_map_and_its_physics_connection(self):
        h = ExternalGraphHarness()
        original = h.graph(h.final)
        add(original, 'DynamicMask', 'FChaosClothAssetSelectionToWeightMapNode',
            {'SelectionName': 'KinematicVertices3D', 'WeightMapName': 'ArtistDynamicMask',
             'SelectedValue': '0.000000', 'UnselectedValue': '1.000000'})
        for row in original.connections:
            if row['fromNode'] == 'ArtistMaxDistance' and row['fromPin'] == 'Collection':
                row['fromNode'] = 'DynamicMask'
        original.connections += [edge('ArtistMaxDistance', 'Collection', 'DynamicMask', 'Collection'),
                                 edge('ArtistMaxDistance', 'KinematicVertices3D', 'DynamicMask', 'SelectionName'),
                                 edge('DynamicMask', 'WeightMapName', 'ArtistDamping', 'DampingCoefficientWeighted.WeightMap')]
        native_call, dump = h.tool.call_method, h.dump
        def typed_call(operation, args=()):
            result = native_call(operation, args=args)
            if operation == 'GetNodeInfo' and args[0].name == 'ArtistDamping':
                info = json.loads(result)
                info['inputPins'].append({'name': 'DampingCoefficientWeighted.WeightMap'})
                return json.dumps(info)
            return result
        def dump_with_mask(path):
            result = json.loads(dump(path))
            asset = h.load(path)
            if asset is not None and hasattr(asset, 'instance') and 'DynamicMask' in h.graph(asset).nodes:
                result['collections'][0]['weight_maps'].append({'name': 'ArtistDynamicMask', 'values': [0., 1., 1.]})
                result['collections'][0]['sim_float_attributes'].append({'name': 'ArtistDynamicMask', 'values': [0., 1., 1.]})
            return json.dumps(result)
        h.tool.call_method = typed_call
        h.unreal.CodexClothToolsLibrary.dump_cloth_collection_colors = dump_with_mask
        before = original.snapshot()
        h.publish()
        self.assertEqual(original.snapshot(), before)
        graph = h.graph(h.final)
        self.assertEqual(graph.nodes['DynamicMask'].properties, original.nodes['DynamicMask'].properties)
        self.assertEqual(graph.connections, original.connections)


class NativeGPublicationTests(unittest.TestCase):
    sim = '/Game/Fixtures/Sim'

    def test_raw_g_maximum_role_follows_connections_through_renames_and_rejects_ambiguity(self):
        infos = {'ReadG': {'type': 'FCodexClothVertexColorWeightMapNode'},
                 'RawMaximum': {'type': 'FChaosClothAssetSimulationMaxDistanceConfigNode'},
                 'ArtistMaximum': {'type': 'FChaosClothAssetSimulationMaxDistanceConfigNode'},
                 'Mask': {'type': 'FChaosClothAssetSelectionToWeightMapNode'}}
        connections = [edge('ReadG', 'WeightMapName', 'RawMaximum', 'MaxDistance.WeightMap'),
                       edge('Mask', 'WeightMapName', 'ArtistMaximum', 'MaxDistance.WeightMap')]
        self.assertEqual(M._raw_g_maximum(infos, connections), 'RawMaximum')
        infos['RenamedByArtist'] = infos.pop('RawMaximum')
        connections[0]['toNode'] = 'RenamedByArtist'
        self.assertEqual(M._raw_g_maximum(infos, connections), 'RenamedByArtist')
        connections.append(edge('ReadG', 'WeightMapName', 'ArtistMaximum', 'MaxDistance.WeightMap'))
        with self.assertRaisesRegex(ValueError, 'one structurally connected source MaxDistance role'):
            M._raw_g_maximum(infos, connections)

    def native_tools(self, h):
        native_call, native_dump = h.tool.call_method, h.dump_graph
        def text(value):
            if isinstance(value, dict):
                return '(' + ','.join(key + '=' + text(item) for key, item in value.items()) + ')'
            return json.dumps(value) if isinstance(value, str) else str(value)
        def call(operation, args=()):
            result = native_call(operation, args=args)
            if operation != 'GetNodeInfo':
                return result
            info = json.loads(result)
            node = args[0]
            graph = node.get_typed_outer(type(h.graph(h.source)))
            for direction, endpoint, pin in (('input', 'toNode', 'toPin'), ('output', 'fromNode', 'fromPin')):
                names = {item['name'] for item in info[direction + 'Pins']}
                names |= {row[pin] for row in graph.connections if row[endpoint] == node.name}
                info[direction + 'Pins'] = [{'name': name} for name in sorted(names)]
            info['properties'] = {key: text(value) if isinstance(value, dict) else value
                                  for key, value in info['properties'].items()}
            return json.dumps(info)
        def dump(path):
            result = json.loads(native_dump(path))
            for node in result['nodes']:
                node['active'] = True
            return json.dumps(result)
        h.tool.call_method = call
        h.unreal.CodexClothToolsLibrary.dump_dataflow_nodes = dump

    def source(self, h):
        graph = authored_template(h)
        graph.connections = [row for row in graph.connections
            if row != edge('ReadSelection', 'Value', 'ArtistMaxDistance', 'InKinematic.StringValue')]
        graph.connections.append(edge('ArtistSelection', 'OutputName.StringValue',
                                      'ArtistMaxDistance', 'InKinematic.StringValue'))
        return h.build()[0]

    def test_initial_final_keeps_g_maximum_selection_variables_and_blend_without_double_physics(self):
        h = ExternalGraphHarness(existing=False)
        source = self.source(h)
        source_graph = h.graph(source)
        before = source_graph.snapshot()
        M._publish(h.unreal, source, h.new_binding, h.target, h.owner, 'new-content', sim_mesh_path=self.sim)
        final = h.load(h.target)
        graph = h.graph(final)
        self.assertEqual(source_graph.snapshot(), before)
        self.assertEqual(set(graph.nodes), {'ClothAssetTerminal', 'GeneratorBindings', 'GuideOwnG',
            'ArtistMaxDistance', 'ArtistSelection', 'ReadSelection', 'SimulationKinematicBlend'})
        self.assertEqual(graph.nodes['GuideOwnG'].node_type, 'FCodexClothVertexColorWeightMapNode')
        self.assertEqual(graph.nodes['ArtistMaxDistance'].properties,
                         source_graph.nodes['ArtistMaxDistance'].properties)
        self.assertEqual(graph.context, source_graph.context)
        self.assertNotIn('ArtistSolver', graph.nodes)
        self.assertIn(edge('GeneratorBindings', 'Collection', 'GuideOwnG', 'Collection'), graph.connections)
        self.assertIn(edge('GuideOwnG', 'WeightMapName', 'ArtistMaxDistance', 'MaxDistance.WeightMap'), graph.connections)
        self.assertIn(edge('ArtistMaxDistance', 'KinematicVertices3D', 'SimulationKinematicBlend',
                           'KinematicVertices3D.StringValue'), graph.connections)
        for i in range(3):
            self.assertIn(edge('SimulationKinematicBlend', 'Collection', 'ClothAssetTerminal', f'CollectionLods[{i}]'),
                          graph.connections)

    def test_native_g_source_rejects_wrong_g_before_saving_and_cleans_its_request_assets(self):
        h = ExternalGraphHarness(existing=False)
        authored_template(h)
        built, packet = fixture()
        built['sim_weights'] = [1., 1., 1.]
        h.source.collection['built_mapping'] = built
        record = {'cloth_template_asset_path': h.source_path, 'sim_asset_path': self.sim,
                  'render_asset_path': '/Game/Fixtures/Render', 'physics_asset_path': '/Game/Fixtures/BodyPhysics'}
        before = h.graph(h.source).snapshot()
        with self.assertRaisesRegex(ValueError, 'differs from exported simulation G'):
            M._build_source(h.unreal, record, '/Game/Fixtures/BadGSource', packet)
        self.assertNotIn('/Game/Fixtures/BadGSource', h.assets)
        self.assertNotIn('/Game/Fixtures/BadGSource', h.saved_cloths)
        self.assertEqual(h.graph(h.source).snapshot(), before)

    def test_new_native_g_source_accepts_constant_g_without_inferring_an_old_paint_role(self):
        for value in (0., 1.):
            with self.subTest(value=value):
                h = ExternalGraphHarness(existing=False)
                authored_template(h)
                built, packet = fixture()
                built['sim_weights'] = [value] * 3
                packet['meshes']['sim']['weights'] = [value] * 3
                h.source.collection['built_mapping'] = built
                record = {'cloth_template_asset_path': h.source_path, 'sim_asset_path': self.sim,
                          'render_asset_path': '/Game/Fixtures/Render', 'physics_asset_path': '/Game/Fixtures/BodyPhysics'}
                source, audit = M._build_source(h.unreal, record, '/Game/Fixtures/ConstantGSource', packet)
                self.assertEqual(h.graph(source).nodes['GuideOwnG'].node_type, 'FCodexClothVertexColorWeightMapNode')
                self.assertEqual(audit['collections'][0]['built_mapping']['sim_weights'], [value] * 3)

    def test_comparison_checks_baseline_maps_instead_of_hiding_a_native_g_map_difference(self):
        expected = {'weight_maps': [{'name': 'MaxDistance', 'values': [0., 1.]}],
                    'sim_float_attributes': []}
        actual = {'collections': [copy.deepcopy(expected)]}
        M._verify_published_collection(actual, expected, {'MaxDistance'})
        actual['collections'][0]['weight_maps'][0]['values'] = [1., 1.]
        with self.assertRaisesRegex(RuntimeError, 'differs from its verified collection: weight_maps'):
            M._verify_published_collection(actual, expected, {'MaxDistance'})

    def test_reimport_rebinds_only_the_native_g_source_and_restores_it_on_save_failure(self):
        for external in (False, True):
            for fail_save in (False, True):
                with self.subTest(external=external, fail_save=fail_save):
                    h = ExternalGraphHarness(existing=False, external_source=external)
                    source = self.source(h)
                    self.native_tools(h)
                    M._publish(h.unreal, source, h.new_binding, h.target, h.owner,
                               'first-content', sim_mesh_path=self.sim)
                    h.final = h.load(h.target)
                    original_graph = h.graph(h.final)
                    before = original_graph.snapshot()
                    original_context = M._graph_context(h.final)
                    old_binding = h.new_binding
                    h.new_binding = '/Game/Fixtures/NextBinding'
                    next_sim = '/Game/Fixtures/ReplacementSim'
                    h.graph(source).nodes['GuideOwnG'].properties['SourceMesh'] = next_sim
                    h.fail_target_save_once = fail_save
                    if fail_save:
                        with self.assertRaisesRegex(RuntimeError, 'Updated cloth could not be saved'):
                            M._publish(h.unreal, source, h.new_binding, h.target, h.owner,
                                       'next-content', sim_mesh_path=next_sim)
                        self.assertEqual(h.graph(h.final).snapshot(), before)
                        self.assertEqual(h.final.metadata[h.binding_key], old_binding)
                    else:
                        M._publish(h.unreal, source, h.new_binding, h.target, h.owner,
                                   'next-content', sim_mesh_path=next_sim)
                        self.assertEqual(h.graph(h.final).nodes['GuideOwnG'].properties['SourceMesh'], next_sim)
                        self.assertEqual(h.graph(h.final).connections, before['connections'])
                        self.assertEqual(h.final.metadata[h.binding_key], h.new_binding)
                    self.assertEqual(M._graph_context(h.final), original_context)

    def test_initial_stage_keeps_a_native_derived_map_without_reapplying_its_baked_physics(self):
        h = ExternalGraphHarness(existing=False)
        graph = authored_template(h)
        add(graph, 'SourceDynamicMask', 'FChaosClothAssetSelectionToWeightMapNode',
            {'SelectionName': 'KinematicVertices3D', 'WeightMapName': 'SourceDynamic',
             'SelectedValue': '0.000000', 'UnselectedValue': '1.000000'})
        add(graph, 'SourceDamping', 'FChaosClothAssetSimulationDampingConfigNode',
            {'DampingCoefficientWeighted': {'Low': .02, 'High': .09, 'WeightMap': 'SourceDynamic'}})
        graph.connections = [row for row in graph.connections
            if row != edge('ArtistMaxDistance', 'Collection', 'ArtistSolver', 'Collection')]
        graph.connections += [edge('ArtistMaxDistance', 'Collection', 'SourceDynamicMask', 'Collection'),
                              edge('SourceDynamicMask', 'Collection', 'SourceDamping', 'Collection'),
                              edge('SourceDamping', 'Collection', 'ArtistSolver', 'Collection'),
                              edge('ArtistMaxDistance', 'KinematicVertices3D', 'SourceDynamicMask', 'SelectionName'),
                              edge('SourceDynamicMask', 'WeightMapName', 'SourceDamping', 'DampingCoefficientWeighted.WeightMap')]
        self.native_tools(h)
        source = h.build()[0]
        M._publish(h.unreal, source, h.new_binding, h.target, h.owner, 'first-content', sim_mesh_path=self.sim)
        h.final = h.load(h.target)
        final = h.graph(h.final)
        self.assertIn('SourceDynamicMask', final.nodes)
        self.assertNotIn('SourceDamping', final.nodes)
        self.assertNotIn('ArtistSolver', final.nodes)
        self.assertEqual(json.loads(h.final.metadata[M.NATIVE_DERIVED_MAPS]), ['SourceDynamic'])
        self.assertIn(edge('ArtistMaxDistance', 'Collection', 'SourceDynamicMask', 'Collection'), final.connections)
        self.assertIn(edge('SourceDynamicMask', 'Collection', 'SimulationKinematicBlend', 'Collection'), final.connections)
        M._publish(h.unreal, source, h.new_binding, h.target, h.owner, 'next-content', sim_mesh_path=self.sim)
        self.assertIn('SourceDynamicMask', h.graph(h.final).nodes)
        self.assertNotIn('SourceDamping', h.graph(h.final).nodes)


if __name__ == '__main__':
    unittest.main()
