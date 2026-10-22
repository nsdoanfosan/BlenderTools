"""String map authorities remain authoring inputs during native route planning."""
import copy
import unittest

from test_ue_hair_weight_routes import M, NativeModel, fixture, info, structure


class StringAuthorityRouteTests(unittest.TestCase):
    def authority(self):
        graph, infos, native, collection = fixture()
        infos['ArtistMask']['properties']['OutputName'] = '()'
        infos['ReadArtist'] = info('ReadArtist', 'FGetDataflowVariableNode',
                                  {'VariableName': 'ArtistMapName'}, inputs=(), outputs=('Value',))
        infos['ReadArtist']['outputPins'][0]['type'] = 'String'
        graph = structure(infos, graph['connections'] + [
            M._edge('ReadArtist', 'Value', 'ArtistMask', 'InputName.StringValue'),
            M._edge('ReadArtist', 'Value', 'Damping', 'DampingCoefficientWeighted.WeightMap')])
        for row in native:
            if row['name'] == 'ArtistMask': row['props']['OutputName'] = '()'
        return graph, infos, native, collection, {'ArtistMask': 'ArtistPaint'}

    def plan(self, values, **kwargs):
        graph, infos, native, collection, names = values
        return M.plan_weight_routes(graph, infos, '/Game/Hair_GuideSim', native_nodes=native,
                                    collection=collection, effective_map_names=names, **kwargs)

    def test_variable_input_artist_map_is_never_baked_or_converted_by_other_routes(self):
        values = self.authority(); before = copy.deepcopy(values)
        graph, infos, native, collection, names = values
        plan = self.plan(values, derived_maps=['Travel'])
        self.assertEqual(values, before)
        self.assertNotIn('ArtistMask', plan['remove_nodes'])
        self.assertIn(M._edge('ReadArtist', 'Value', 'ArtistMask', 'InputName.StringValue'), plan['after_connections'])
        self.assertIn(M._edge('ReadArtist', 'Value', 'Damping', 'DampingCoefficientWeighted.WeightMap'), plan['after_connections'])
        model = NativeModel(graph, infos, native)
        artist_before = copy.deepcopy(model.infos['ArtistMask'])
        native_before = copy.deepcopy(model.native['ArtistMask'])
        M.install_weight_routes(None, model.call, graph, infos, model.handles(), plan,
                                verify=lambda *_: True, read_native=model.read_native)
        self.assertEqual(model.infos['ArtistMask'], artist_before)
        self.assertEqual(model.native['ArtistMask'], native_before)
        self.assertEqual(model.infos['ReadArtist'], infos['ReadArtist'])

    def test_unknown_unvalidated_bad_typed_or_cyclic_string_sources_are_rejected(self):
        for fault in ('unvalidated', 'unknown', 'type', 'cycle', 'duplicate', 'guid'):
            with self.subTest(fault=fault):
                values = self.authority(); graph, infos, native, collection, names = values
                if fault == 'unvalidated': names.clear()
                if fault == 'unknown': infos['ReadArtist']['type'] = 'FUnknownStringNode'
                if fault == 'type': infos['ReadArtist']['outputPins'][0]['type'] = 'Float'
                if fault == 'guid': infos['ReadArtist']['properties']['VariableGuid'] = '1' * 32
                if fault == 'cycle':
                    infos['ReadArtist']['inputPins'] = [{'name': 'Aux', 'type': 'String'}]
                    graph['connections'].append(M._edge('ArtistMask', 'OutputName.StringValue', 'ReadArtist', 'Aux'))
                if fault == 'duplicate':
                    graph['connections'].append(M._edge('ArtistMask', 'OutputName.StringValue', 'ArtistMask', 'InputName.StringValue'))
                with self.assertRaises(ValueError): self.plan(values)

    def test_literal_name_cannot_be_overridden_by_an_inconsistent_validated_dictionary(self):
        graph, infos, native, collection = fixture()
        values = graph, infos, native, collection, {'ArtistMask': 'WrongMap'}
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            self.plan(values)

    def test_one_getter_cannot_claim_two_different_effective_map_identities(self):
        values = self.authority(); graph, infos, native, collection, names = values
        infos['DynamicMask']['properties']['OutputName'] = '()'
        names['DynamicMask'] = 'Travel'
        graph['connections'].append(M._edge('ReadArtist', 'Value', 'DynamicMask', 'InputName.StringValue'))
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            self.plan(values)

    def test_variable_driven_render_blend_paint_remains_protected(self):
        values = self.authority(); graph, infos, native, collection, names = values
        infos['ArtistMask']['properties']['MeshTarget'] = 'Render'
        names['ArtistMask'] = 'RenderDeformerSkinningBlend'
        with self.assertRaisesRegex(ValueError, 'render skinning-blend map'):
            self.plan(values)


if __name__ == '__main__':
    unittest.main()
