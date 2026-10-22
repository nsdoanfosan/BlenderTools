"""Map-name authority is read from the graph without baking authored values.

These are app-free input/contract tests, not a Dataflow or Chaos evaluator.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from test_ue_hair_weight_refresh import fixture as refresh_fixture


SPEC = importlib.util.spec_from_file_location(
    'hair_map_name_authority', Path(__file__).parents[1] /
    'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

GUID = '1234567890ABCDEF1234567890ABCDEF'


def edge(source='ReadTravelMap', output='Value', target='ArtistMask',
         input_pin='InputName.StringValue'):
    return dict(fromNode=source, fromPin=output, toNode=target, toPin=input_pin)


def context(*, values='None', guids='()', preview='None'):
    def bag(value):
        return '(Value=None)' if value == 'None' else '(Value=<PropertyBag>(' + value + '))'
    return {
        'terminal': 'ClothAssetTerminal',
        'owner': 'None',
        'overrides': '(Variables=' + bag(values) + ',OverriddenVariableGuids=' + guids + ')',
        'preview': bag(preview),
    }


def dynamic_fixture():
    graph, _, _ = refresh_fixture()
    graph['nodes'][0]['props']['OutputName'] = '()'
    graph['nodes'].append({
        'name': 'ReadTravelMap', 'type': 'FGetDataflowVariableNode', 'active': True,
        'props': {'VariableName': 'TravelMapName',
                  'VariablePropertyBag': '(Value="UNTRUSTED_CACHED_COPY")'},
        'outputPins': [{'name': 'Value', 'type': 'String'}],
    })
    graph['connections'] = [edge()]
    infos = {node['name']: node for node in graph['nodes']}
    variables = [{'name': 'TravelMapName', 'type': 'String', 'value': 'Travel'}]
    return graph, infos, variables, context()


def native_getter(**changes):
    row = {'name': 'ReadTravelMap', 'type': 'FGetDataflowVariableNode', 'active': True}
    row.update(changes)
    return {'nodes': [row]}


class Asset:
    """Only the reflected context read used by _graph_context is available."""
    def __init__(self, instance_context):
        self.context = copy.deepcopy(instance_context)
        self.reads = []

    def get_editor_property(self, name):
        self.reads.append(name)
        if name != 'dataflow_instance':
            raise AssertionError('Unexpected asset access: ' + name)
        return self

    def export_text(self):
        return ('(DataflowTerminal=' + self.context['terminal'] +
                ',VariableOverrides=' + self.context['overrides'] +
                ',PreviewBlueprintVariableOverrides=' + self.context['preview'] +
                ',Owner=' + self.context['owner'] + ')')


class Graph:
    def get_path_name(self):
        return '/Game/Fixtures/HairDataflow.HairDataflow'


class WeightMapNameTests(unittest.TestCase):
    def resolve(self, infos, connections, variables, instance_context):
        before = copy.deepcopy((infos, connections, variables, instance_context))
        try:
            return M._effective_weight_map_names(
                infos, connections, variables=variables, context=instance_context)
        finally:
            self.assertEqual((infos, connections, variables, instance_context), before)

    def reject(self, infos, connections, variables, instance_context):
        with self.assertRaises(ValueError):
            self.resolve(infos, connections, variables, instance_context)

    def test_literal_output_keeps_the_authored_name_and_needs_no_variable_context(self):
        graph, _, _ = refresh_fixture()
        infos = {node['name']: node for node in graph['nodes']}
        self.assertEqual(self.resolve(infos, [], None, None), {'ArtistMask': 'Travel'})

    def test_literal_output_is_the_authority_even_when_an_input_name_is_connected(self):
        graph, infos, _, _ = dynamic_fixture()
        infos['ArtistMask']['props']['OutputName'] = '(StringValue="ArtistTravel")'
        self.assertEqual(self.resolve(infos, graph['connections'], None, None),
                         {'ArtistMask': 'ArtistTravel'})

    def test_empty_output_uses_the_native_string_default_without_baking_it(self):
        for output in ('()', '(StringValue="")'):
            for value in ('Travel', '"Travel"'):
                with self.subTest(output=output, value=value):
                    graph, infos, variables, instance = dynamic_fixture()
                    infos['ArtistMask']['props']['OutputName'] = output
                    variables[0]['value'] = value
                    self.assertEqual(self.resolve(infos, graph['connections'], variables, instance),
                                     {'ArtistMask': 'Travel'})
                    self.assertEqual(infos['ArtistMask']['props']['OutputName'], output)
                    self.assertEqual(infos['ArtistMask']['props']['VertexWeights'], '(0,1,1)')
                    self.assertEqual(infos['ReadTravelMap']['props']['VariablePropertyBag'],
                                     '(Value="UNTRUSTED_CACHED_COPY")')

    def test_infos_properties_and_snake_case_connections_are_supported(self):
        graph, infos, variables, instance = dynamic_fixture()
        for info in infos.values():
            info['properties'] = info.pop('props')
        connections = [{'from_node': 'ReadTravelMap', 'from_output': 'Value',
                        'to_node': 'ArtistMask', 'to_input': 'InputName.StringValue'}]
        self.assertEqual(self.resolve(infos, connections, variables, instance),
                         {'ArtistMask': 'Travel'})

    def test_concrete_string_pin_spellings_and_quoted_variable_name(self):
        for pin_type in ('String', 'FString', 'string'):
            with self.subTest(pin_type=pin_type):
                graph, infos, variables, instance = dynamic_fixture()
                infos['ReadTravelMap']['props']['VariableName'] = '"TravelMapName"'
                infos['ReadTravelMap']['outputPins'][0]['type'] = pin_type
                self.assertEqual(self.resolve(infos, graph['connections'], variables, instance),
                                 {'ArtistMask': 'Travel'})

    def test_equal_active_and_preview_values_keep_the_same_native_identity(self):
        for instance in (
                context(values='TravelMapName="Travel"', guids='(' + GUID + ')'),
                context(preview='TravelMapName="Travel"'),
                context(values='TravelMapName="Travel"', guids='(' + GUID + ')',
                        preview='TravelMapName="Travel"')):
            with self.subTest(instance=instance):
                graph, infos, variables, _ = dynamic_fixture()
                self.assertEqual(self.resolve(infos, graph['connections'], variables, instance),
                                 {'ArtistMask': 'Travel'})

    def test_stale_non_overridden_bag_value_cannot_replace_the_graph_default(self):
        graph, infos, variables, _ = dynamic_fixture()
        instance = context(values='TravelMapName="UnusedStaleName"', guids='()')
        self.assertEqual(self.resolve(infos, graph['connections'], variables, instance),
                         {'ArtistMask': 'Travel'})

    def test_different_active_or_preview_value_requires_native_resolution(self):
        for instance in (
                context(values='TravelMapName="OtherMap"', guids='(' + GUID + ')'),
                context(preview='TravelMapName="OtherMap"')):
            with self.subTest(instance=instance):
                graph, infos, variables, _ = dynamic_fixture()
                self.reject(infos, graph['connections'], variables, instance)

    def test_unknown_or_ambiguous_guid_serialization_is_rejected(self):
        for guids in ('None', '(not-a-guid)', '(' + GUID + ',)',
                      '(12345678-90AB-CDEF-1234-567890ABCDEF)', '("' + GUID + '")'):
            with self.subTest(guids=guids):
                graph, infos, variables, _ = dynamic_fixture()
                self.reject(infos, graph['connections'], variables,
                            context(values='TravelMapName="Travel"', guids=guids))

    def test_context_is_required_and_extra_or_malformed_override_fields_are_rejected(self):
        invalid = [None, {}, {'overrides': '(Variables=(Value=None),OverriddenVariableGuids=())'}]
        for text in ('(Variables=(Value=None))',
                     '(Variables=(Value=None),OverriddenVariableGuids=(),Unexpected=True)',
                     '(Variables=(Value=None),Variables=(Value=None),OverriddenVariableGuids=())',
                     '(Variables=(Value=None),OverriddenVariableGuids=()'):
            instance = context()
            instance['overrides'] = text
            invalid.append(instance)
        for instance in invalid:
            with self.subTest(instance=instance):
                graph, infos, variables, _ = dynamic_fixture()
                self.reject(infos, graph['connections'], variables, instance)

    def test_unrecognized_or_duplicate_property_bag_payload_is_rejected(self):
        for field in ('overrides', 'preview'):
            for bag in ('(Value="Travel")', '(Value=UnknownBag(TravelMapName="Travel"))',
                        '(Value=<PropertyBag>(TravelMapName="Travel",TravelMapName="Travel"))'):
                with self.subTest(field=field, bag=bag):
                    graph, infos, variables, instance = dynamic_fixture()
                    instance[field] = (bag if field == 'preview' else
                                       '(Variables=' + bag + ',OverriddenVariableGuids=())')
                    self.reject(infos, graph['connections'], variables, instance)

    def test_case_alias_in_instance_bag_is_rejected_as_fname_ambiguity(self):
        for field in ('values', 'preview'):
            for payload in ('travelmapname="OtherMap"', 'travelmapname="Travel"',
                            'TravelMapName="Travel",travelmapname="OtherMap"'):
                with self.subTest(field=field, payload=payload):
                    graph, infos, variables, _ = dynamic_fixture()
                    kwargs = {field: payload}
                    if field == 'values':
                        kwargs['guids'] = '(' + GUID + ')'
                    self.reject(infos, graph['connections'], variables, context(**kwargs))

    def test_missing_duplicate_or_case_alias_graph_variable_is_rejected(self):
        invalid = [[],
                   [{'name': 'OtherName', 'type': 'String', 'value': 'Travel'}],
                   [{'name': 'travelmapname', 'type': 'String', 'value': 'Travel'}],
                   [{'name': 'TravelMapName', 'type': 'String', 'value': 'Travel'}] * 2,
                   [{'name': 'TravelMapName', 'type': 'String', 'value': 'Travel'},
                    {'name': 'travelmapname', 'type': 'String', 'value': 'OtherMap'}]]
        for variables in invalid:
            with self.subTest(variables=variables):
                graph, infos, _, instance = dynamic_fixture()
                self.reject(infos, graph['connections'], variables, instance)

    def test_wrong_typed_or_non_scalar_graph_value_is_rejected(self):
        for typ, value in (('Name', 'Travel'), ('Float', '1.0'), ('FString', 'Travel'),
                           ('String', 5), ('String', None), ('String', ''),
                           ('String', '(Value="Travel")'), ('String', 'Travel Map'),
                           ('String', '"unterminated')):
            with self.subTest(typ=typ, value=value):
                graph, infos, variables, instance = dynamic_fixture()
                variables[0].update(type=typ, value=value)
                self.reject(infos, graph['connections'], variables, instance)

    def test_future_guid_bound_layout_requires_an_explicit_native_binding_contract(self):
        for field in ('VariableGuid', 'VariableGUID', 'VariableId', 'VariableID',
                      'variable_guid', 'variable_id'):
            for graph_guid in (None, GUID, 'F' * 32):
                with self.subTest(field=field, graph_guid=graph_guid):
                    graph, infos, variables, instance = dynamic_fixture()
                    target = (infos['ReadTravelMap'] if field.startswith('variable_')
                              else infos['ReadTravelMap']['props'])
                    target[field] = GUID
                    if graph_guid is not None:
                        variables[0]['guid'] = graph_guid
                    with self.assertRaisesRegex(ValueError, 'Unsupported GUID-bound'):
                        self.resolve(infos, graph['connections'], variables, instance)

    def test_ordinary_node_guid_does_not_change_name_based_variable_lookup(self):
        graph, infos, variables, instance = dynamic_fixture()
        infos['ReadTravelMap']['guid'] = GUID
        self.assertEqual(self.resolve(infos, graph['connections'], variables, instance),
                         {'ArtistMask': 'Travel'})

    def test_empty_name_requires_one_direct_string_producer(self):
        for connections in ([], [edge(), edge()], [edge(input_pin='InputName')],
                            [edge(output='OtherOutput')], [edge(source='MissingNode')]):
            with self.subTest(connections=connections):
                graph, infos, variables, instance = dynamic_fixture()
                self.reject(infos, connections, variables, instance)

    def test_unknown_inactive_or_cyclic_getter_is_rejected(self):
        for mutation in ('unknown', 'inactive', 'untyped-active', 'self-cycle', 'incoming-producer'):
            with self.subTest(mutation=mutation):
                graph, infos, variables, instance = dynamic_fixture()
                getter = infos['ReadTravelMap']
                if mutation == 'unknown':
                    getter['type'] = 'FUnknownStringNode'
                elif mutation == 'inactive':
                    getter['active'] = False
                elif mutation == 'untyped-active':
                    getter['active'] = 'True'
                else:
                    graph['connections'].append(edge(
                        source='ReadTravelMap' if mutation == 'self-cycle' else 'ArtistMask',
                        target='ReadTravelMap', input_pin='UnknownInput'))
                self.reject(infos, graph['connections'], variables, instance)

    def test_missing_duplicate_and_non_string_value_pins_are_rejected(self):
        for pins in ([], [{'name': 'WrongPin', 'type': 'String'}],
                     [{'name': 'Value', 'type': 'Float'}], [{'name': 'Value'}],
                     [{'name': 'Value', 'type': 'String'}] * 2):
            with self.subTest(pins=pins):
                graph, infos, variables, instance = dynamic_fixture()
                infos['ReadTravelMap']['outputPins'] = pins
                self.reject(infos, graph['connections'], variables, instance)

    def test_nonliteral_serialized_output_is_not_guessed(self):
        for output in ('', 'Travel', '(Name="Travel")', '(StringValue=None)'):
            with self.subTest(output=output):
                graph, infos, variables, instance = dynamic_fixture()
                infos['ArtistMask']['props']['OutputName'] = output
                self.reject(infos, graph['connections'], variables, instance)

    def test_asset_wrapper_reads_exact_instance_context_and_graph_variable_authority(self):
        graph, infos, variables, instance = dynamic_fixture()
        asset, graph_object, calls = Asset(instance), object(), []
        before = copy.deepcopy((graph, variables, instance))

        def call(name, *args):
            calls.append((name, args))
            self.assertEqual((name, args), ('ListVariables', (graph_object,)))
            return json.dumps(variables)

        result = M._asset_weight_map_names(
            asset, graph_object, call, infos, graph['connections'], read_native=native_getter)
        self.assertEqual(result, {'ArtistMask': 'Travel'})
        self.assertEqual(calls, [('ListVariables', (graph_object,))])
        self.assertEqual(asset.reads, ['dataflow_instance'])
        self.assertEqual((graph, variables, instance), before)

    def test_literal_asset_wrapper_needs_no_graph_or_native_reads(self):
        graph, _, _ = refresh_fixture()
        infos = {node['name']: node for node in graph['nodes']}

        def forbidden(*args, **kwargs):
            self.fail('Literal authored name should not require authority reads')

        self.assertEqual(M._asset_weight_map_names(
            None, None, forbidden, infos, [], read_native=forbidden), {'ArtistMask': 'Travel'})

    def test_dynamic_asset_requires_one_active_matching_native_getter(self):
        invalid = (
            ('missing-reader', None), ('missing-node', lambda: {'nodes': []}),
            ('inactive', lambda: native_getter(active=False)),
            ('untyped-active', lambda: native_getter(active='True')),
            ('wrong-type', lambda: native_getter(type='FUnknownStringNode')),
            ('duplicate-node', lambda: {'nodes': native_getter()['nodes'] * 2}),
            ('fname-alias', lambda: {'nodes': native_getter()['nodes'] +
                                   native_getter(name='readtravelmap')['nodes']}),
        )
        for label, read_native in invalid:
            with self.subTest(native=label):
                graph, infos, variables, instance = dynamic_fixture()
                before = copy.deepcopy((graph, variables, instance))
                with self.assertRaises(ValueError):
                    M._asset_weight_map_names(
                        Asset(instance), object(), lambda *args: json.dumps(variables),
                        infos, graph['connections'], read_native=read_native)
                self.assertEqual((graph, variables, instance), before)

    def test_current_native_activity_is_authoritative_and_list_form_is_supported(self):
        graph, infos, variables, instance = dynamic_fixture()
        infos['ReadTravelMap']['active'] = False
        before = copy.deepcopy((graph, variables, instance))
        self.assertEqual(M._asset_weight_map_names(
            Asset(instance), object(), lambda *args: json.dumps(variables), infos,
            graph['connections'], read_native=lambda: native_getter()['nodes']),
            {'ArtistMask': 'Travel'})
        self.assertEqual((graph, variables, instance), before)

    def test_native_dump_asset_identity_must_match_the_actual_graph(self):
        for path in (Graph().get_path_name(), '/Game/Fixtures/Different.Different'):
            with self.subTest(path=path):
                graph, infos, variables, instance = dynamic_fixture()
                proof = native_getter()
                proof['asset'] = path
                args = (Asset(instance), Graph(), lambda *args: json.dumps(variables),
                        infos, graph['connections'])
                if path == Graph().get_path_name():
                    self.assertEqual(M._asset_weight_map_names(
                        *args, read_native=lambda: proof), {'ArtistMask': 'Travel'})
                else:
                    with self.assertRaisesRegex(ValueError, 'different Dataflow'):
                        M._asset_weight_map_names(*args, read_native=lambda: proof)

    def test_native_variable_binding_name_must_match_editor_metadata(self):
        for variable in ('TravelMapName', 'OtherName', 'travelmapname'):
            with self.subTest(variable=variable):
                graph, infos, variables, instance = dynamic_fixture()
                proof = native_getter(props={'VariableName': variable})
                args = (Asset(instance), Graph(), lambda *args: json.dumps(variables),
                        infos, graph['connections'])
                if variable == 'TravelMapName':
                    self.assertEqual(M._asset_weight_map_names(
                        *args, read_native=lambda: proof), {'ArtistMask': 'Travel'})
                else:
                    with self.assertRaisesRegex(ValueError, 'binding name differs'):
                        M._asset_weight_map_names(*args, read_native=lambda: proof)

    def test_future_native_guid_bound_getter_is_not_inferred_from_matching_names(self):
        for field in ('VariableGuid', 'VariableGUID', 'VariableId', 'VariableID'):
            with self.subTest(field=field):
                graph, infos, variables, instance = dynamic_fixture()
                variables[0]['guid'] = GUID
                proof = native_getter(props={'VariableName': 'TravelMapName', field: GUID})
                with self.assertRaisesRegex(ValueError, 'Unsupported GUID-bound'):
                    M._asset_weight_map_names(
                        Asset(instance), Graph(), lambda *args: json.dumps(variables),
                        infos, graph['connections'], read_native=lambda: proof)

    def test_variable_named_paint_transfers_the_same_mask_as_literal_paint(self):
        graph, old, new = refresh_fixture()
        expected = M._weight_map_updates(graph, old, new, None, None)
        dynamic, infos, variables, instance = dynamic_fixture()
        before = copy.deepcopy((dynamic, old, new, variables, instance))
        names = self.resolve(infos, dynamic['connections'], variables, instance)
        actual = M._weight_map_updates(dynamic, old, new, None, None,
                                       effective_map_names=names)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['ArtistMask'], ('Travel', [0, 1, 1], [0, 1, 1, 0]))
        self.assertEqual((dynamic, old, new, variables, instance), before)

    def test_validated_name_does_not_reinterpret_arbitrary_authored_paint(self):
        dynamic, infos, variables, instance = dynamic_fixture()
        _, old, _ = refresh_fixture()
        new = copy.deepcopy(old)
        new['built_mapping']['kinematic_sim_indices'] = [1]
        infos['ArtistMask']['props']['VertexWeights'] = '(0,0.4,1)'
        before = copy.deepcopy((dynamic, old, new))
        names = self.resolve(infos, dynamic['connections'], variables, instance)
        self.assertEqual(M._weight_map_updates(dynamic, old, new, None, None,
                                               effective_map_names=names)['ArtistMask'],
                         ('Travel', [0, 0.4, 1], [0, 0.4, 1]))
        self.assertEqual((dynamic, old, new), before)

    def test_missing_validated_identity_cannot_silently_discard_a_paint_node(self):
        dynamic, _, _, _ = dynamic_fixture()
        _, old, new = refresh_fixture()
        before = copy.deepcopy((dynamic, old, new))
        with self.assertRaisesRegex(ValueError, 'validated native identity'):
            M._weight_map_updates(dynamic, old, new, None, None, effective_map_names={})
        self.assertEqual((dynamic, old, new), before)

    def test_a_valid_name_does_not_bypass_authored_topology_correspondence(self):
        dynamic, infos, variables, instance = dynamic_fixture()
        _, old, new = refresh_fixture()
        infos['ArtistMask']['props']['VertexWeights'] = '(0,0.4,1)'
        before = copy.deepcopy((dynamic, old, new))
        names = self.resolve(infos, dynamic['connections'], variables, instance)
        with self.assertRaisesRegex(ValueError, 'explicit source correspondence'):
            M._weight_map_updates(dynamic, old, new, None, None, effective_map_names=names)
        self.assertEqual((dynamic, old, new), before)


if __name__ == '__main__':
    unittest.main()
