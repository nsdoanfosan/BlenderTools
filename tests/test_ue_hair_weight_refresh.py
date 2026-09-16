"""Authored masks must never be silently discarded during reimport."""
import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('weight_refresh', Path(__file__).parents[1] /
    'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture():
    graph = {'nodes': [{'name': 'ArtistMask', 'type': 'FChaosClothAssetWeightMapNode',
        'props': {'MeshTarget': 'Simulation', 'MapOverrideType': 'ReplaceAll',
        'Snapshots': '(ActiveSnapshot=-1)', 'bIsFrozen': 'False',
        'OutputName': '(StringValue="Travel")', 'VertexWeights': '(0,1,1)'}}]}
    old = {'built_mapping': {'sim_positions': [[0,0,0], [1,0,0], [0,1,0]],
        'sim_triangles': [[0,1,2]], 'kinematic_sim_indices': [0]}}
    new = copy.deepcopy(old)
    new['built_mapping']['sim_positions'].append([1,1,0])
    new['built_mapping']['sim_triangles'].append([1,2,3])
    new['built_mapping']['kinematic_sim_indices'] = [0,3]
    return graph, old, new


class WeightRefreshTests(unittest.TestCase):
    def test_exact_kinematic_mask_follows_new_binding(self):
        graph, old, new = fixture()
        result = M._weight_map_updates(graph, old, new, None, None)
        self.assertEqual(result['ArtistMask'], ('Travel', [0,1,1], [0,1,1,0]))

    def test_arbitrary_paint_requires_correspondence_after_topology_change(self):
        graph, old, new = fixture()
        graph['nodes'][0]['props']['VertexWeights'] = '(0,0.4,1)'
        with self.assertRaisesRegex(ValueError, 'explicit source correspondence'):
            M._weight_map_updates(graph, old, new, None, None)

    def test_identical_topology_preserves_arbitrary_paint(self):
        graph, old, new = fixture()
        graph['nodes'][0]['props']['VertexWeights'] = '(0,0.4,1)'
        result = M._weight_map_updates(graph, old, old, None, None)
        self.assertEqual(result['ArtistMask'][2], [0,0.4,1])

    def test_snapshot_is_not_reinterpreted_as_generated_mask(self):
        graph, old, new = fixture()
        graph['nodes'][0]['props']['Snapshots'] = '(ActiveSnapshot=0)'
        with self.assertRaisesRegex(ValueError, 'Unsupported authored'):
            M._weight_map_updates(graph, old, new, None, None)

    def test_parent_keys_require_source_identity(self):
        graph, old, new = fixture()
        graph['nodes'][0]['props']['OutputName'] = '(StringValue="CodexParentForceKey")'
        with self.assertRaisesRegex(ValueError, 'generator provenance'):
            M._weight_map_updates(graph, old, new, None, None)


if __name__ == '__main__':
    unittest.main()
