"""Native Send2UE linked-source contract and source-asset protection regressions."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1] / 'src/addons/send2ue'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load_module('test_loa_runtime', BASE / 'resources/pipeline/ue_linked_opening_assembly.py')


def placement():
    return dict(instance_id='stable-window-1', source_pivot='window_wood_single_02',
                source_blend='C:/Assets/window.blend', source_requires_blueprint=True,
                location=[100, -30, 90], rotation=[0, 0, 0, 1], scale=[1, 1, 1])


def manifest():
    return dict(schema_version=1, root='house_cliff_set_03', assembly_id='stable-house-1',
                source_blend='C:/Assets/house.blend',
                root_mesh_asset_path='/Game/House/house_cliff_set_03', placements=[placement()])


class ManifestTests(unittest.TestCase):
    def test_native_bc_destination_and_transform_are_preserved(self):
        result = runtime.validate_assembly(manifest())
        self.assertEqual(result['blueprint_asset_path'], '/Game/House/bc_house_cliff_set_03')
        self.assertEqual(result['placements'][0]['location'], [100, -30, 90])
        self.assertEqual(result['placements'][0]['source_pivot'], 'window_wood_single_02')

    def test_empty_placement_list_allows_owned_components_to_be_removed(self):
        data = manifest()
        data['placements'] = []
        self.assertEqual(runtime.validate_assembly(data)['placements'], [])

    def test_invalid_ids_transforms_and_paths_are_rejected(self):
        changes = [('instance_id', ''), ('rotation', [0, 0, 0, 0]),
                   ('scale', [1, 0, 1]), ('location', [float('nan'), 0, 0]),
                   ('asset_path', '/Game/../Wrong'), ('source_requires_blueprint', 'true')]
        for field, value in changes:
            with self.subTest(field=field):
                data = manifest()
                data['placements'][0][field] = value
                with self.assertRaises(ValueError):
                    runtime.validate_assembly(data)

    def test_duplicate_instance_is_rejected(self):
        data = manifest()
        data['placements'].append(copy.deepcopy(data['placements'][0]))
        with self.assertRaisesRegex(ValueError, 'unique stable'):
            runtime.validate_assembly(data)


class Blueprint:
    def __init__(self, path):
        self.path = path

    def get_path_name(self):
        return self.path + '.' + self.path.rsplit('/', 1)[-1]


class StaticMesh:
    pass


class SourceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.bp_path = '/Game/Window/bc_window_wood_single_02'
        self.mesh_path = '/Game/Window/window_wood_single_02'
        self.bp = Blueprint(self.bp_path)
        self.assets = {self.bp_path: self.bp, self.mesh_path: StaticMesh()}
        self.native_manifest = {'schema_version': 1, 'root': 'window_wood_single_02', 'components': [
            {'name': 'window_wood_single_02', 'mesh_asset_path': self.mesh_path}]}
        self.tags = {
            runtime.NESTED_OWNER_KEY: runtime.NESTED_OWNER_VERSION + self.mesh_path,
            runtime.NESTED_MANIFEST_KEY: json.dumps(self.native_manifest),
        }
        self.library = SimpleNamespace(get_metadata_tag=lambda bp, key: self.tags.get(key, ''),
                                       load_blueprint_class=Mock(return_value='generated-source-class'))
        self.unreal = SimpleNamespace(Blueprint=Blueprint, StaticMesh=StaticMesh,
                                      load_asset=lambda path: self.assets.get(path), EditorAssetLibrary=self.library)
        self.paths = patch.object(runtime, '_exact_paths', side_effect=self.candidates).start()
        self.addCleanup(patch.stopall)

    def candidates(self, unreal, name, class_name):
        wanted_type = Blueprint if class_name == 'Blueprint' else StaticMesh
        return [path for path, asset in self.assets.items()
                if path.rsplit('/', 1)[-1] == name and isinstance(asset, wanted_type)]

    def test_window_reuses_native_bc_and_never_inspects_glass(self):
        result = runtime.resolve_source(self.unreal, placement())
        self.assertEqual(result['asset_path'], self.bp_path)
        self.assertEqual(result['kind'], 'BLUEPRINT')
        self.assertEqual(self.paths.call_count, 1)

    def test_missing_required_blueprint_never_falls_back_to_frame_mesh(self):
        self.assets.pop(self.bp_path)
        with self.assertRaisesRegex(RuntimeError, 'Send the source file first'):
            runtime.resolve_source(self.unreal, placement())
        self.assertEqual(self.paths.call_count, 1)

    def test_unowned_matching_blueprint_is_refused(self):
        self.tags[runtime.NESTED_OWNER_KEY] = 'user-blueprint'
        with self.assertRaisesRegex(RuntimeError, 'not owned'):
            runtime.resolve_source(self.unreal, placement())

    def test_ambiguous_blueprints_require_explicit_path(self):
        self.assets['/Game/Duplicate/bc_window_wood_single_02'] = Blueprint('/Game/Duplicate/bc_window_wood_single_02')
        with self.assertRaisesRegex(RuntimeError, 'Multiple source Blueprints'):
            runtime.resolve_source(self.unreal, placement())
        data = placement()
        data['asset_path'] = self.bp_path
        self.assertEqual(runtime.resolve_source(self.unreal, data)['asset_path'], self.bp_path)

    def test_required_bc_rejects_explicit_frame_mesh(self):
        data = placement()
        data['asset_path'] = self.mesh_path
        with self.assertRaisesRegex(RuntimeError, 'required export pivots'):
            runtime.resolve_source(self.unreal, data)

    def test_single_pivot_source_uses_existing_static_mesh(self):
        data = placement()
        data.update(source_pivot='door_wood_double_01', source_requires_blueprint=False)
        path = '/Game/Door/door_wood_double_01'
        self.assets[path] = StaticMesh()
        result = runtime.resolve_source(self.unreal, data)
        self.assertEqual(result['kind'], 'MESH')
        self.assertEqual(result['asset_path'], path)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        modules = {name: ModuleType(name) for name in (
            'send2ue', 'send2ue.constants', 'send2ue.core', 'send2ue.core.extension',
            'send2ue.dependencies', 'send2ue.dependencies.unreal')}
        modules['send2ue.constants'].UnrealTypes = SimpleNamespace(STATIC_MESH='StaticMesh')
        modules['send2ue.core.extension'].ExtensionBase = object
        self.commands = []
        def run_commands(commands):
            self.commands.append(commands)
            return 'SEND2UE_LINKED_OPENINGS_OK:' + json.dumps({
                'verified': True, 'blueprint_asset_path': '/Game/House/bc_house_cliff_set_03', 'placement_count': 1})
        modules['send2ue.dependencies.unreal'].run_commands = run_commands
        with patch.dict(sys.modules, modules):
            self.module = load_module('test_loa_extension', BASE / 'resources/extensions/linked_opening_assemblies.py')
        self.api = SimpleNamespace(for_export=Mock(return_value=manifest()), filter_objects=Mock())
        patch.object(self.module, '_authoring_api', return_value=self.api).start()
        self.addCleanup(patch.stopall)
        self.extension = self.module.LinkedOpeningAssembliesExtension()

    def asset(self):
        return dict(_asset_type='StaticMesh', asset_path='/Game/House/house_cliff_set_03')

    def test_only_identified_house_uses_native_pivot_origin(self):
        asset = self.asset()
        self.extension.pre_mesh_export(asset, None)
        self.assertTrue(asset['_nested_pivot_origin'])
        self.assertEqual(asset[self.module.ASSEMBLY_KEY]['assembly_id'], 'stable-house-1')
        self.api.for_export.return_value = None
        ordinary = self.asset()
        self.extension.pre_mesh_export(ordinary, None)
        self.assertNotIn(self.module.ASSEMBLY_KEY, ordinary)
        self.assertNotIn('_nested_pivot_origin', ordinary)

    def test_skipped_asset_never_builds_blueprint(self):
        asset = self.asset()
        self.extension.pre_mesh_export(asset, None)
        asset['skip'] = True
        self.extension.post_import(asset, None)
        self.assertEqual(self.commands, [])

    def test_post_import_commands_support_deferred_and_normal_queue(self):
        asset = self.asset()
        self.extension.pre_mesh_export(asset, None)
        self.extension.post_import(asset, None)
        text = '\n'.join(self.commands[0])
        self.assertIn('apply_assembly(', text)
        self.assertIn("'root_mesh_asset_path': '/Game/House/house_cliff_set_03'", text)
        self.assertNotIn('import_asset(', text)
        self.assertFalse(hasattr(self.module.LinkedOpeningAssembliesExtension, 'post_operation'))

    def test_nested_house_ownership_conflict_is_rejected(self):
        asset = self.asset()
        asset['_nested_pivot_component'] = {'root': 'house'}
        with self.assertRaisesRegex(RuntimeError, 'both nested-pivot'):
            self.extension.pre_mesh_export(asset, None)

    def test_remote_error_without_receipt_fails_normal_operation(self):
        asset = self.asset()
        self.extension.pre_mesh_export(asset, None)
        with patch.object(self.module, 'run_commands', return_value='Source Blueprint missing'):
            with self.assertRaisesRegex(RuntimeError, 'did not complete.*Source Blueprint missing'):
                self.extension.post_import(asset, None)

    def test_deferred_recording_does_not_require_a_runtime_receipt_yet(self):
        asset = self.asset()
        self.extension.pre_mesh_export(asset, None)
        with patch.dict(sys.modules, {'send2ue.dependencies.unreal': SimpleNamespace(_COMMAND_RECORDING_STACK=[[]])}):
            with patch.object(self.module, 'run_commands', return_value='') as commands:
                self.extension.post_import(asset, None)
        commands.assert_called_once()

    def test_source_filter_preserves_api_result(self):
        self.api.filter_objects.return_value = (['armature'], ['house-wall'], [])
        self.assertEqual(self.extension.filter_objects([], ['house-wall', 'source-frame'], []),
                         (['armature'], ['house-wall'], []))


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.assembly = runtime.validate_assembly(manifest())
        self.blueprint = Blueprint(self.assembly['blueprint_asset_path'])
        self.root_mesh = StaticMesh()
        self.owner = runtime.OWNER_VERSION + 'stable-house-1:/Game/House/house_cliff_set_03'
        self.tag = self.owner
        class SceneComponent:
            pass
        class StaticMeshComponent(SceneComponent):
            pass
        class ChildActorComponent(SceneComponent):
            pass
        self.root_component = StaticMeshComponent()
        self.root_row = dict(identifier='root', object=self.root_component, variable='house_cliff_set_03',
                             data=SimpleNamespace(root=True), parent_object=None)
        self.rows = [self.root_row]
        self.generated = {'root': self.root_row}
        self.unreal = SimpleNamespace(
            Blueprint=Blueprint, StaticMesh=StaticMesh, SceneComponent=SceneComponent,
            StaticMeshComponent=StaticMeshComponent, ChildActorComponent=ChildActorComponent,
            load_asset=lambda path: self.blueprint if path == self.assembly['blueprint_asset_path'] else self.root_mesh,
            EditorAssetLibrary=SimpleNamespace(does_asset_exist=lambda _: True,
                                               get_metadata_tag=lambda *_: self.tag),
            SubobjectDataBlueprintFunctionLibrary=SimpleNamespace(is_root_component=lambda data: data.root))
        patch.object(runtime, 'resolve_source', return_value=dict(
            kind='BLUEPRINT', source_class=object(), asset_path='/Game/Window/bc_window_wood_single_02')).start()
        patch.object(runtime, '_gather', side_effect=lambda *_: (object(), self.rows, self.generated)).start()
        self.addCleanup(patch.stopall)

    def prepare(self):
        return runtime._prepare(self.unreal, object(), self.assembly)

    def user_component(self, name, parent=None):
        return dict(identifier=None, variable=name, object=self.unreal.SceneComponent(),
                    parent_object=parent, data=SimpleNamespace(root=False))

    def test_another_native_or_user_blueprint_owner_is_refused(self):
        for tag in ('', 'send2ue.nested_pivots.v1:/Game/House/house_cliff_set_03'):
            with self.subTest(tag=tag):
                self.tag = tag
                with self.assertRaisesRegex(RuntimeError, 'not owned'):
                    self.prepare()

    def test_owned_blueprint_preserves_unrelated_user_components(self):
        self.rows.append(self.user_component('UserSpotLight', self.root_component))
        result = self.prepare()
        self.assertIs(result['blueprint'], self.blueprint)
        self.assertEqual(len(result['specs']), 3)
        self.assertEqual(self.rows[-1]['variable'], 'UserSpotLight')

    def test_user_component_name_collision_is_refused(self):
        self.rows.append(self.user_component('house_cliff_set_03'))
        with self.assertRaisesRegex(RuntimeError, 'collides with a user component'):
            self.prepare()

    def test_stale_placement_with_user_child_is_refused(self):
        stale = self.user_component('OldPlacement')
        stale['identifier'] = 'pivot:removed'
        self.rows.append(stale)
        self.generated[stale['identifier']] = stale
        self.rows.append(self.user_component('UserLight', stale['object']))
        with self.assertRaisesRegex(RuntimeError, 'user components'):
            self.prepare()

    def test_changed_root_is_refused(self):
        self.root_row['data'].root = False
        self.rows.append(dict(self.user_component('UserRoot'), data=SimpleNamespace(root=True)))
        with self.assertRaisesRegex(RuntimeError, 'root was changed'):
            self.prepare()


class NativeImportFailureTests(unittest.TestCase):
    def run_import(self, result_paths, asset):
        source = BASE / 'dependencies/unreal.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        importer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'UnrealImportAsset')
        method = next(node for node in importer.body if isinstance(node, ast.FunctionDef) and node.name == 'run_import')
        self.post = Mock()
        unreal = SimpleNamespace(StaticMesh=StaticMesh, load_asset=Mock(return_value=asset),
            AssetToolsHelpers=SimpleNamespace(get_asset_tools=lambda: SimpleNamespace(import_asset_tasks=Mock())))
        instance = SimpleNamespace(
            _import_task=SimpleNamespace(get_editor_property=lambda _: result_paths), _options=None,
            _asset_data={'_asset_type': 'StaticMesh', '_linked_opening_assembly': manifest(),
                         'asset_path': '/Game/House/house_cliff_set_03'},
            ensure_hair_tool_uv_precision=self.post, ensure_hair_tool_nanite=self.post, audit_hair_tool_payload=self.post)
        namespace = {'unreal': unreal}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace['run_import'](instance)

    def test_failed_house_import_cannot_reuse_stale_existing_mesh(self):
        with self.assertRaisesRegex(RuntimeError, 'did not produce the expected'):
            self.run_import([], StaticMesh())
        self.post.assert_not_called()

    def test_exact_house_result_must_load_as_static_mesh(self):
        with self.assertRaisesRegex(RuntimeError, 'missing or is not a StaticMesh'):
            self.run_import(['/Game/House/house_cliff_set_03.house_cliff_set_03'], None)
        self.post.assert_not_called()

    def test_successful_house_result_runs_normal_post_processing(self):
        paths = ['/Game/House/house_cliff_set_03.house_cliff_set_03']
        self.assertEqual(self.run_import(paths, StaticMesh()), paths)
        self.assertEqual(self.post.call_count, 3)


class HouseCollisionTests(unittest.TestCase):
    def setUp(self):
        self.path = '/Game/House/house_cliff_set_03'
        self.value = 'DEFAULT'
        self.body = SimpleNamespace(
            modify=Mock(), get_editor_property=lambda _: self.value,
            set_editor_property=lambda _, value: setattr(self, 'value', value))
        self.mesh = StaticMesh()
        self.mesh.get_path_name = lambda: self.path + '.house_cliff_set_03'
        self.mesh.get_editor_property = lambda _: self.body
        self.mesh.modify = Mock()
        self.save = Mock(return_value=True)
        self.unreal = SimpleNamespace(StaticMesh=StaticMesh,
            CollisionTraceFlag=SimpleNamespace(CTF_USE_COMPLEX_AS_SIMPLE='COMPLEX'),
            EditorAssetLibrary=SimpleNamespace(save_loaded_asset=self.save))

    def test_only_fresh_exact_house_collision_is_changed_and_saved(self):
        self.assertTrue(runtime._configure_house_collision(self.unreal, self.mesh, self.path))
        self.assertEqual(self.value, 'COMPLEX')
        self.save.assert_called_once_with(self.mesh)
        self.mesh.modify.assert_called_once()
        self.body.modify.assert_called_once()

    def test_matching_collision_is_not_resaved(self):
        self.value = 'COMPLEX'
        self.assertFalse(runtime._configure_house_collision(self.unreal, self.mesh, self.path))
        self.save.assert_not_called()
        self.mesh.modify.assert_not_called()

    def test_source_mesh_or_wrong_path_is_refused_before_modification(self):
        with self.assertRaisesRegex(RuntimeError, 'exact imported house'):
            runtime._configure_house_collision(self.unreal, self.mesh, '/Game/Window/window_wood_single_02')
        self.save.assert_not_called()
        self.mesh.modify.assert_not_called()

    def test_missing_body_setup_is_actionable_error(self):
        self.body = None
        with self.assertRaisesRegex(RuntimeError, 'no BodySetup'):
            runtime._configure_house_collision(self.unreal, self.mesh, self.path)
        self.save.assert_not_called()

    def test_save_failure_propagates(self):
        self.save.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Could not save'):
            runtime._configure_house_collision(self.unreal, self.mesh, self.path)


if __name__ == '__main__':
    unittest.main()
