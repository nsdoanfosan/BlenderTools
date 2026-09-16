"""Ordinary exports remain usable when a cached authoring addon is disabled."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = (Path(__file__).resolve().parents[1] / 'src/addons/send2ue'
          / 'resources/extensions/linked_opening_assemblies.py')


class ProviderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.provider = ModuleType('linked_opening_assembly.send2ue_manifest')
        self.provider.for_export = Mock(return_value=None)
        self.provider.filter_objects = Mock(side_effect=lambda *args: args)
        self.provider.is_registered = Mock(return_value=False)
        modules = {name: ModuleType(name) for name in (
            'send2ue', 'send2ue.constants', 'send2ue.core',
            'send2ue.core.extension', 'send2ue.dependencies', 'send2ue.dependencies.unreal',
            'linked_opening_assembly')}
        modules['send2ue.constants'].UnrealTypes = SimpleNamespace(STATIC_MESH='StaticMesh')
        modules['send2ue.core.extension'].ExtensionBase = object
        modules['send2ue.dependencies.unreal'].run_commands = Mock()
        modules['linked_opening_assembly.send2ue_manifest'] = self.provider
        module_patch = patch.dict(sys.modules, modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        spec = importlib.util.spec_from_file_location('test_linked_lifecycle', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.extension = self.module.LinkedOpeningAssembliesExtension()

    def assert_inactive(self):
        inputs = (['rig'], ['hair', 'house'], [])
        self.assertEqual(self.extension.filter_objects(*inputs), inputs)
        asset = {'_asset_type': 'StaticMesh', 'asset_path': '/Game/House/house'}
        self.extension.pre_mesh_export(asset, None)
        self.assertEqual(asset, {'_asset_type': 'StaticMesh', 'asset_path': '/Game/House/house'})
        self.provider.filter_objects.assert_not_called()
        self.provider.for_export.assert_not_called()

    def test_absent_provider_is_inactive(self):
        sys.modules.pop('linked_opening_assembly.send2ue_manifest')
        self.assert_inactive()

    def test_cached_unregistered_provider_is_inactive(self):
        self.assert_inactive()

    def test_provider_without_registration_contract_is_inactive(self):
        del self.provider.is_registered
        self.assert_inactive()

    def test_enabled_legacy_provider_requires_matching_update(self):
        del self.provider.is_registered
        sys.modules['linked_opening_assembly'].__addon_enabled__ = True
        with self.assertRaisesRegex(RuntimeError, 'Update both addons and restart Blender'):
            self.extension.filter_objects([], ['house'], [])
        self.provider.filter_objects.assert_not_called()

    def test_disable_and_reenable_follow_provider_lifecycle(self):
        self.provider.is_registered.return_value = True
        self.extension.filter_objects([], ['house'], [])
        self.provider.filter_objects.assert_called_once()
        self.provider.filter_objects.reset_mock()
        self.provider.is_registered.return_value = False
        self.assert_inactive()
        self.provider.is_registered.return_value = True
        self.extension.filter_objects([], ['house'], [])
        self.provider.filter_objects.assert_called_once()

    def test_active_provider_export_errors_propagate(self):
        self.provider.is_registered.return_value = True
        self.provider.for_export.side_effect = ValueError('Invalid authored placement')
        with self.assertRaisesRegex(ValueError, 'Invalid authored placement'):
            self.extension.pre_mesh_export({'_asset_type': 'StaticMesh'}, None)


if __name__ == '__main__':
    unittest.main()
