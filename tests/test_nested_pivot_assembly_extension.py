"""Integration contract: only complete successful import sets can make a BP."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'src/addons/send2ue/resources/extensions/nested_pivot_assemblies.py'


def load_extension():
    mocks = {name: ModuleType(name) for name in ('bpy', 'send2ue', 'send2ue.constants',
        'send2ue.core', 'send2ue.core.extension', 'send2ue.core.nested_pivots',
        'send2ue.dependencies', 'send2ue.dependencies.unreal')}
    mocks['send2ue.constants'].ToolInfo = SimpleNamespace(EXPORT_COLLECTION=SimpleNamespace(value='Export'))
    mocks['send2ue.constants'].UnrealTypes = SimpleNamespace(STATIC_MESH='StaticMesh')
    mocks['send2ue.core.extension'].ExtensionBase = object
    mocks['send2ue.core.nested_pivots'].get_assembly_root = lambda *a: None
    mocks['send2ue.core.nested_pivots'].iter_assembly_pivots = lambda *a: []
    commands = []
    mocks['send2ue.dependencies.unreal'].run_commands = commands.append
    with patch.dict(sys.modules, mocks):
        spec = importlib.util.spec_from_file_location('test_pivot_extension', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, commands


class TestNestedPivotAssemblyExtension(unittest.TestCase):
    def setUp(self):
        self.module, self.commands = load_extension()
        self.extension = self.module.NestedPivotAssembliesExtension()
        self.extension.pre_operation(None)

    def asset(self, name='frame', skip=False):
        return {'_asset_type':'StaticMesh','skip':skip,'asset_path':'/Game/Window/' + name,
                self.module.RUN_KEY:self.module._RUN_ID,
                self.module.COMPONENT_KEY:{'root':'frame','name':name,
                'parent':None if name=='frame' else 'frame',
                'required_pivots':['frame','glass'], 'location':[0,0,0],
                'rotation':[0,0,0,1], 'scale':[1,1,1]}}

    def test_ordinary_import_never_calls_runtime(self):
        self.extension.pre_operation(None)
        self.extension.post_import({'_asset_type':'StaticMesh','asset_path':'/Game/Old'}, None)
        self.assertEqual(self.commands, [])

    def test_partial_or_skipped_child_cannot_use_stale_asset(self):
        self.extension.post_import(self.asset('glass', skip=True), None)
        self.assertEqual(self.commands, [])

    def test_receipts_are_recorded_for_replay_in_both_orders(self):
        for order in (('frame','glass'),('glass','frame')):
            with self.subTest(order=order):
                self.extension.pre_operation(None)
                for name in order:
                    self.extension.post_import(self.asset(name), None)
                commands = '\n'.join('\n'.join(group) for group in self.commands[-2:])
                self.assertIn('record_imported_pivot', commands)
                self.assertIn('/Game/Window/frame', commands)
                self.assertIn('/Game/Window/glass', commands)
                self.assertIn(self.module._RUN_ID, commands)
                self.assertNotIn('apply_pivot_assemblies(', commands)
                self.assertFalse(hasattr(self.module, '_IMPORTED'))

    def test_new_run_cannot_inherit_previous_receipts(self):
        self.extension.post_import(self.asset(), None)
        old_run = self.module._RUN_ID
        self.extension.pre_operation(None)
        self.extension.post_import(self.asset('glass'), None)
        self.assertNotEqual(old_run, self.module._RUN_ID)
        self.assertIn(old_run, '\n'.join(self.commands[0]))
        self.assertNotIn(old_run, '\n'.join(self.commands[1]))

    def test_missing_export_run_is_error(self):
        asset = self.asset()
        asset.pop(self.module.RUN_KEY)
        with self.assertRaisesRegex(RuntimeError, 'run identity'):
            self.extension.post_import(asset, None)


if __name__ == '__main__':
    unittest.main()
