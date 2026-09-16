"""Import corner sharing requires one original vertex and one spline parent."""
import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
SPEC = importlib.util.spec_from_file_location('ue_hair_source_corner_identity', PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture():
    mesh = {'vertices': [[0, 0, 0], [.01, 0, 0], [0, .01, 0]],
            'triangles': [[0, 1, 2]], 'guide_ids': [10, 10, 10], 'weights': [0, 0, 1]}
    packet = {'meshes': {'sim': copy.deepcopy(mesh), 'render': copy.deepcopy(mesh)}}
    built = {role + '_positions': [[0, 0, 0], [1, 0, 0], [0, -1, 0]] for role in ('sim', 'render')}
    built.update({role + '_triangles': [[0, 1, 2]] for role in ('sim', 'render')})
    built['sim_weights'] = [0, 0, 1]
    return built, packet


class SourceCornerIdentityTests(unittest.TestCase):
    def test_imported_shading_splits_share_proven_source_vertex(self):
        built, packet = fixture()
        built['render_positions'].extend([[0, 0, 0], [0, 0, 0]])
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['render_source_indices'], [0, 1, 2, 0, 0])
        self.assertEqual(result['render_source_identity_unique'], [True] * 5)
        self.assertEqual(result['render_guide_ids'], [10] * 5)

    def test_coincident_distinct_strands_of_same_spline_cannot_share(self):
        built, packet = fixture()
        packet['meshes']['render']['vertices'].append([0, 0, 0])
        packet['meshes']['render']['guide_ids'].append(10)
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['render_guide_ids'], [10, 10, 10])
        self.assertEqual(result['render_source_identity_unique'], [False, True, True])

    def test_three_overlapping_spline_parents_cannot_attach_by_proximity(self):
        built, packet = fixture()
        render = packet['meshes']['render']
        # One generating object can own A/B/C splines. Their distinct guide IDs
        # remain authoritative even when all three roots occupy the same point.
        render['vertices'].extend([[0, 0, 0], [0, 0, 0]])
        render['guide_ids'].extend([11, 12])
        with self.assertRaisesRegex(ValueError, 'ambiguous guide identity'):
            M.normalize_ownership(built, packet)

    def test_near_coincident_distinct_source_vertices_remain_unproven(self):
        built, packet = fixture()
        packet['meshes']['render']['vertices'].append([0.000001, 0, 0])
        packet['meshes']['render']['guide_ids'].append(10)
        result = M.normalize_ownership(built, packet)
        self.assertFalse(result['render_source_identity_unique'][0])


if __name__ == '__main__':
    unittest.main()
