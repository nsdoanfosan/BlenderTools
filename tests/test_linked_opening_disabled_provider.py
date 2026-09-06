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
        self.scene_type = type('Scene', (), {})
        self.object_type = type('Object', (), {})
        modules = {name: ModuleType(name) for name in (
            'bpy', 'send2ue', 'send2ue.constants', 'send2ue.core',
            'send2ue.core.extension', 'send2ue.dependencies', 'send2ue.dependencies.unreal',
            'linked_opening_assembly')}
        modules['bpy'].types = SimpleNamespace(Scene=self.scene_type, Object=self.object_type)
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

    def test_absent_provider_is_inactive_even_if_rna_exists(self):
        self.scene_type.loa_settings = object()
        self.object_type.loa_settings = object()
        sys.modules.pop('linked_opening_assembly')
        self.assert_inactive()

    def test_cached_unregistered_provider_is_inactive(self):
        self.assert_inactive()

    def test_partial_provider_registration_is_inactive(self):
        self.scene_type.loa_settings = object()
        self.assert_inactive()
        del self.scene_type.loa_settings
        self.object_type.loa_settings = object()
        self.assert_inactive()

    def test_disable_after_registered_export_stops_dispatch(self):
        self.scene_type.loa_settings = object()
        self.object_type.loa_settings = object()
        self.extension.filter_objects([], ['house'], [])
        self.provider.filter_objects.assert_called_once()
        self.provider.filter_objects.reset_mock()
        del self.scene_type.loa_settings
        del self.object_type.loa_settings
        self.assert_inactive()


if __name__ == '__main__':
    unittest.main()
