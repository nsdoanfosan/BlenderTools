"""Native Chaos response maps survive reimport without permitting geometry edits."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
spec = importlib.util.spec_from_file_location('native_response_pipeline_test', PATH)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class NativeResponseSamplerTests(unittest.TestCase):
    def setUp(self):
        self.infos = {
            'Damping': {'type': 'FChaosClothAssetSimulationDampingConfigNode',
                        'inputPins': [{'name': 'DampingCoefficientWeighted.WeightMap'}]},
            'Map': {'type': 'FDataflowSamplerToAttributeNode', 'properties': {
                'AttributeName': 'HairResponseRandom', 'VertexGroup': '(Name="SimVertices3D")',
                'bSaveAsColor': 'False', 'bUseDefaultValue': 'False'}},
            'Random': {'type': 'FDataflowRandomFloatSamplerNode'},
        }
        self.edges = [
            {'fromNode': 'Random', 'fromPin': 'Sampler', 'toNode': 'Map', 'toPin': 'Sampler'},
            {'fromNode': 'Map', 'fromPin': 'Collection', 'toNode': 'Damping', 'toPin': 'Collection'},
            {'fromNode': 'Map', 'fromPin': 'AttributeName', 'toNode': 'Damping',
             'toPin': 'DampingCoefficientWeighted.WeightMap'},
        ]
        self.configs, self.chain = set(self.infos), {'Map', 'Damping'}

    def validate(self):
        return api._native_response_attributes(self.infos, self.edges, self.configs, self.chain)

    def test_native_random_map_is_accepted_without_mutating_graph(self):
        before = copy.deepcopy((self.infos, self.edges))
        self.assertEqual(self.validate(), {'HairResponseRandom'})
        self.assertEqual((self.infos, self.edges), before)

    def test_existing_graph_without_sampler_keeps_its_contract(self):
        self.assertEqual(api._native_response_attributes({'Damping': self.infos['Damping']}, [],
            {'Damping'}, {'Damping'}), set())

    def test_render_color_changes_are_rejected(self):
        self.infos['Map']['properties']['bSaveAsColor'] = 'True'
        with self.assertRaises(ValueError): self.validate()

    def test_other_collection_groups_are_rejected(self):
        self.infos['Map']['properties']['VertexGroup'] = '(Name="RenderVertices")'
        with self.assertRaises(ValueError): self.validate()

    def test_binding_max_distance_cannot_be_overwritten(self):
        self.infos['Map']['properties']['AttributeName'] = 'MaxDistance'
        with self.assertRaises(ValueError): self.validate()

    def test_authored_paint_cannot_be_overwritten(self):
        self.infos['Paint'] = {'type': 'FChaosClothAssetWeightMapNode',
            'properties': {'OutputName': '(StringValue="HairResponseRandom")'}}
        with self.assertRaises(ValueError): self.validate()

    def test_missing_physics_consumer_is_rejected(self):
        self.edges = self.edges[:2]
        with self.assertRaises(ValueError): self.validate()

    def test_nonexistent_physics_pin_is_rejected(self):
        self.edges[-1]['toPin'] = 'Invented.WeightMap'
        with self.assertRaises(ValueError): self.validate()

    def test_unused_or_unrelated_sampler_is_rejected(self):
        self.infos['Extra'] = {'type': 'FDataflowRandomFloatSamplerNode'}
        self.configs.add('Extra')
        with self.assertRaises(ValueError): self.validate()

    def test_non_float_sampler_is_rejected(self):
        self.infos['Random']['type'] = 'FDataflowRandomVectorSamplerNode'
        with self.assertRaises(ValueError): self.validate()


if __name__ == '__main__': unittest.main()
