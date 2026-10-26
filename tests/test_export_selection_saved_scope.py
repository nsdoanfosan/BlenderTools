import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch


class SavedExportScopeTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(window_manager={}, scene={})
        path = Path(__file__).parents[1] / 'src/addons/send2ue/core/export_selection.py'
        spec = importlib.util.spec_from_file_location('saved_export_scope_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'bpy': SimpleNamespace(context=self.context)}):
            spec.loader.exec_module(self.module)
        self.units = {'A': object(), 'B': object()}

    def test_existing_files_default_to_all_units(self):
        self.assertEqual(self.module._requested_unit_names(self.units), {'A', 'B'})

    def test_file_scope_survives_a_new_window_manager(self):
        self.context.scene['send2ue_export_unit_names'] = '["A"]'
        self.assertEqual(self.module._requested_unit_names(self.units), {'A'})
        self.context.window_manager = {}
        self.assertEqual(self.module._requested_unit_names(self.units), {'A'})

    def test_operation_scope_overrides_file_scope(self):
        self.context.scene['send2ue_export_unit_names'] = '["A"]'
        self.context.window_manager['send2ue_export_unit_names'] = '["B"]'
        self.assertEqual(self.module._requested_unit_names(self.units), {'B'})

    def test_explicit_empty_operation_exports_only_shared_dependencies(self):
        self.context.scene['send2ue_export_unit_names'] = '["A"]'
        self.context.window_manager['send2ue_export_unit_names'] = '[]'
        self.assertEqual(self.module._requested_unit_names(self.units), set())

    def test_invalid_file_scope_fails_instead_of_exporting_everything(self):
        self.context.scene['send2ue_export_unit_names'] = 'invalid'
        with self.assertRaises(ValueError):
            self.module._requested_unit_names(self.units)
