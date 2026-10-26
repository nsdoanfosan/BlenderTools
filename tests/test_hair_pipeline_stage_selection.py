"""Declared native Hair Tool chains retain the intended immediate physical guide."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch


class Object:
    def __init__(self, name, unit='unit', native=True, upstream=None):
        self.name, self.unit, self.native, self.upstream = name, unit, native, upstream


class StageSelectionTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'src/addons/send2ue/core/export_selection.py'
        spec = importlib.util.spec_from_file_location('stage_selection_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'bpy': SimpleNamespace()}):
            spec.loader.exec_module(self.module)

    def resolve(self, sources, candidates):
        return self.module.resolve_hair_outputs(sources, candidates, 'unit',
            lambda obj: obj.native, lambda obj: obj.upstream, lambda obj: obj.unit)

    def test_three_stage_chain_exports_final_and_uses_stage_two_as_guide(self):
        surface = Object('arbitrary_surface', native=False)
        guide = Object('arbitrary_splines', upstream=surface)
        render = Object('arbitrary_final', upstream=guide)
        result = self.resolve([guide], [surface, guide, render])
        self.assertEqual(result, {render})
        self.assertIs(next(iter(result)).upstream, guide)
        self.assertIsNot(next(iter(result)).upstream, surface)

    def test_names_and_object_order_do_not_determine_stage(self):
        surface = Object('Final_Render', native=False)
        guide = Object('guide2', upstream=surface)
        render = Object('GuideSim', upstream=guide)
        self.assertEqual(self.resolve([guide], [render, surface, guide]), {render})

    def test_already_final_source_is_unchanged(self):
        guide, render = Object('guide'), Object('render')
        render.upstream = guide
        self.assertEqual(self.resolve([render], [guide, render]), {render})

    def test_declared_upstream_and_final_are_not_exported_twice(self):
        guide = Object('guide')
        render = Object('render', upstream=guide)
        self.assertEqual(self.resolve([guide, render], [guide, render]), {render})

    def test_other_unit_and_ordinary_legacy_children_are_excluded(self):
        guide = Object('guide')
        foreign = Object('foreign', unit='other', upstream=guide)
        legacy = Object('legacy', native=False, upstream=guide)
        self.assertEqual(self.resolve([guide], [guide, foreign, legacy]), {guide})

    def test_ordinary_declared_mesh_keeps_exact_scope(self):
        low = Object('low', native=False)
        child = Object('unexpected_child', upstream=low)
        self.assertEqual(self.resolve([low], [low, child]), {low})

    def test_two_stage_surface_to_hair_keeps_existing_immediate_surface(self):
        surface = Object('surface', native=False)
        render = Object('render', upstream=surface)
        self.assertEqual(self.resolve([render], [surface, render]), {render})

    def test_cycle_is_rejected(self):
        first, second = Object('first'), Object('second')
        first.upstream, second.upstream = second, first
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            self.resolve([first], [first, second])


if __name__ == '__main__':
    unittest.main()
