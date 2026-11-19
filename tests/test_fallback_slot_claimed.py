import ast
from pathlib import Path
import unittest


PATH = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline/ue_material_setup.py'


def _load(name):
    tree = ast.parse(PATH.read_text(encoding='utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(PATH), 'exec'), env)
    return env[name]


class FallbackSlotTests(unittest.TestCase):
    def test_back_material_does_not_overwrite_name_matched_front_slot(self):
        fallback = _load('_fallback_slot_index')
        # Jacket: front matched slot 1 by name; the back entry has no section
        # and its JSON slot_index (1) collides with the front.
        self.assertIsNone(fallback({'name': 'M_FabricTwoSided_Jacket_02_back', 'slot_index': 1}, {0, 1, 2}))

    def test_unclaimed_json_index_is_still_used(self):
        fallback = _load('_fallback_slot_index')
        # Skirt: a leftover slot 1 that no entry matched keeps the old behavior.
        self.assertEqual(fallback({'slot_index': 1}, {0}), 1)
        self.assertEqual(fallback({}, set()), 0)

    def test_back_entry_sharing_front_slot_is_unmatched(self):
        drop = _load('_drop_backface_slot_collisions')
        materials = [
            {'name': 'M_FT_Jacket', 'backface_material': 'M_FT_Jacket_back'},
            {'name': 'M_FT_Jacket_back', 'backface_material': 'M_FT_Jacket_back'},
            {'name': 'M_Ornament'},
        ]
        self.assertEqual(drop(materials, [1, 1, 0]), [1, None, 0])
        # A back material with its own section (Solidify shell kept) stays.
        self.assertEqual(drop(materials, [0, 1, 2]), [0, 1, 2])
        # Ordinary materials never unmatch each other.
        self.assertEqual(drop([{'name': 'A'}, {'name': 'B'}], [0, 0]), [0, 0])

    def test_loop_uses_prepass_and_guard(self):
        source = PATH.read_text(encoding='utf8')
        self.assertIn('slot_index = name_matched_slots[entry_index]', source)
        self.assertIn('slot_index = _fallback_slot_index(entry, claimed_slots)', source)


if __name__ == '__main__':
    unittest.main()
