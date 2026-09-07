"""Import identity must not silently turn into nearest-guide assignment."""
import importlib.util
import copy
import io
import hashlib
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
SPEC = importlib.util.spec_from_file_location('ue_hair_guide_cloth_test', PATH)
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


def fixture():
    mesh = {'vertices': [[0, 0, 0], [.01, 0, 0], [0, .01, 0]],
            'triangles': [[0, 1, 2]], 'guide_ids': [7, 7, 7], 'weights': [0, 0, 1]}
    packet = {'meshes': {'sim': copy.deepcopy(mesh), 'render': copy.deepcopy(mesh)}}
    built = {role + '_positions': [[0, 0, 0], [1, 0, 0], [0, -1, 0]] for role in ('sim', 'render')}
    built.update({role + '_triangles': [[0, 1, 2]] for role in ('sim', 'render')})
    built['sim_weights'] = [0, 0, 1]
    return built, packet


def render_only_packet(mixed=False):
    built, packet = fixture()
    packet = copy.deepcopy(packet)
    packet.update(version=2, simulation_enabled=mixed,
                  render_only_sources=[{'render': 'UnguidedHair', 'status': 'no_guide_after_search',
                                        'simulation_enabled': False}])
    render = packet['meshes']['render']
    if mixed:
        render['vertices'] += [[.02, 0, 0], [.03, 0, 0], [.02, .01, 0]]
        render['triangles'] += [[3, 4, 5]]
        render['guide_ids'] += [-1, -1, -1]
        built['render_positions'] += [[2, 0, 0], [3, 0, 0], [2, -1, 0]]
        built['render_triangles'] += [[3, 4, 5]]
        render['render_only_vertex_indices'] = [3, 4, 5]
    else:
        packet['meshes']['sim'] = {key: [] for key in ('vertices', 'triangles', 'guide_ids', 'weights')}
        render['guide_ids'] = [-1] * 3
        render['render_only_vertex_indices'] = [0, 1, 2]
    return built, packet


def file_record(root, packet, **overrides):
    path = Path(root) / 'manifest.json'
    path.write_text(json.dumps(packet), encoding='utf8')
    record = {'version': packet.get('version', 1), 'render_asset_path': '/Game/Hair/SK_Render',
              'manifest_path': str(path), 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    if record['version'] == 2:
        record['simulation_enabled'] = packet['simulation_enabled']
    if record.get('simulation_enabled', True):
        record.update(sim_asset_path='/Game/Hair/SK_Sim', cloth_template_asset_path='/Game/Hair/CA_Template',
                      body_mesh_asset_path='/Game/Body/SK_Body', physics_asset_path='/Game/Body/PA_Body')
    record.update(overrides)
    return record


class IdentityTests(unittest.TestCase):
    def test_mixed_no_guide_vertices_stay_explicitly_unbound_after_import_splits(self):
        built, packet = render_only_packet(mixed=True)
        built['render_positions'].append([2, 0, 0])
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['sim_guide_ids'], [7, 7, 7])
        self.assertEqual(result['render_guide_ids'], [7, 7, 7, -1, -1, -1, -1])
        self.assertEqual(result['render_only_vertex_indices'], [3, 4, 5, 6])
        self.assertEqual(result['render_source_indices'], [0, 1, 2, 3, 4, 5, 3])

    def test_coincident_guided_and_unguided_vertices_cannot_choose_a_nearby_guide(self):
        built, packet = render_only_packet(mixed=True)
        packet['meshes']['render']['vertices'][3] = [0, 0, 0]
        with self.assertRaisesRegex(ValueError, 'ambiguous guide'):
            M.normalize_ownership(built, packet)

    def test_split_import_vertices_keep_generator_identity(self):
        built, packet = fixture()
        built['render_positions'].append([0, 0, 0])
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['render_guide_ids'], [7] * 4)
        self.assertEqual(result['render_source_indices'], [0, 1, 2, 0])

    def test_coincident_different_guide_is_rejected(self):
        built, packet = fixture()
        packet['meshes']['render']['vertices'] = packet['meshes']['render']['vertices'] + [[0, 0, 0]]
        packet['meshes']['render']['guide_ids'] = [7, 7, 7, 8]
        with self.assertRaisesRegex(ValueError, 'ambiguous guide'):
            M.normalize_ownership(built, packet)

    def test_wrong_import_scale_is_not_nearest_matched(self):
        built, packet = fixture()
        built['render_positions'][1] = [100, 0, 0]
        with self.assertRaisesRegex(ValueError, 'no exact source identity'):
            M.normalize_ownership(built, packet)

    def test_wrong_sim_g_is_rejected(self):
        built, packet = fixture()
        built['sim_weights'][0] = 1
        with self.assertRaisesRegex(ValueError, 'differs from exported simulation G'):
            M.normalize_ownership(built, packet)

    def test_missing_authoring_weight_exported_static_is_valid(self):
        built, packet = fixture()
        packet['meshes']['sim']['weights'] = [0, 0, 0]
        packet['meshes']['sim']['authored_weight_present'] = False
        built['sim_weights'] = [0, 0, 0]
        self.assertEqual(M.normalize_ownership(built, packet)['sim_weights'], [0, 0, 0])


class NativeReceiptTests(unittest.TestCase):
    def test_fstring_explicit_failure_cannot_pass_as_a_successful_binding(self):
        payload = {'success': False, 'render_only_vertices': 3,
                   'render_only_skinning_blend_one': True, 'detail': 'Save failed after validation'}
        with self.assertRaisesRegex(RuntimeError, 'Save failed after validation'):
            M._native(json.dumps(payload))

    def test_tuple_call_failure_and_embedded_failure_are_both_respected(self):
        for result in ((False, json.dumps({'success': True})), (True, json.dumps({'success': False}))):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'Native cloth operation failed'):
                M._native(result)

    def test_success_and_legacy_audits_without_success_flag_keep_their_receipt(self):
        for payload in ({'success': True, 'render_only_vertices': 3}, {'collections': []}):
            for result in (json.dumps(payload), (True, json.dumps(payload))):
                with self.subTest(result=result):
                    self.assertEqual(M._native(result), payload)

    def test_nonobject_fstring_and_missing_receipt_are_not_success(self):
        for result in ('null', '[]', None):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'did not return'):
                M._native(result)


class NoGuideContractTests(unittest.TestCase):
    def test_guided_v1_exports_still_validate_without_rebuilding_legacy_owner_names(self):
        _, packet = fixture()
        with tempfile.TemporaryDirectory() as directory:
            record, _ = M.validate_record(file_record(directory, packet))
        self.assertTrue(record['simulation_enabled'])
        self.assertEqual(M.VERSION, 'send2ue.hair_guide_cloth.v1')

    def test_mixed_packet_keeps_full_render_and_only_real_guide_sim_geometry(self):
        _, packet = render_only_packet(mixed=True)
        with tempfile.TemporaryDirectory() as directory:
            record, validated = M.validate_record(file_record(directory, packet))
        self.assertTrue(record['simulation_enabled'])
        self.assertEqual(len(validated['meshes']['render']['vertices']), 6)
        self.assertEqual(len(validated['meshes']['sim']['vertices']), 3)

    def test_pure_no_guide_requires_no_sim_template_body_or_physics_paths(self):
        _, packet = render_only_packet()
        with tempfile.TemporaryDirectory() as directory:
            record, validated = M.validate_record(file_record(directory, packet))
        self.assertFalse(record['simulation_enabled'])
        self.assertNotIn('sim_asset_path', record)
        self.assertEqual(validated['meshes']['render']['guide_ids'], [-1, -1, -1])

    def test_unguided_vertices_require_explicit_absent_search_and_matching_indices(self):
        mutations = [lambda packet: packet.pop('render_only_sources'),
                     lambda packet: packet['render_only_sources'][0].update(status='unresolved'),
                     lambda packet: packet['render_only_sources'][0].pop('simulation_enabled'),
                     lambda packet: packet['meshes']['render'].update(render_only_vertex_indices=[3, 4])]
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                _, packet = render_only_packet(mixed=True)
                mutate(packet)
                with self.assertRaisesRegex(ValueError, 'explicit completed no-guide search'):
                    M.validate_record(file_record(directory, packet))

    def test_pure_no_guide_cannot_smuggle_a_sim_fbx_or_geometry(self):
        _, packet = render_only_packet()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'unexpectedly contains simulation geometry'):
                M.validate_record(file_record(directory, packet, sim_asset_path='/Game/Hair/OldFallback'))

    def test_missing_positive_guide_does_not_become_render_only(self):
        _, packet = render_only_packet(mixed=True)
        packet['meshes']['render']['guide_ids'][:3] = [99, 99, 99]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'missing simulation guides'):
                M.validate_record(file_record(directory, packet))

    def test_negative_sim_owner_cannot_create_a_fallback_guide(self):
        _, packet = render_only_packet(mixed=True)
        packet['meshes']['sim']['guide_ids'] = [-1, -1, -1]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'sim has invalid generator ownership'):
                M.validate_record(file_record(directory, packet))

    def test_legacy_render_as_sim_packet_never_recreates_removed_fallback(self):
        _, packet = fixture()
        packet['parts'] = [{'provenance': 'render_as_sim_no_guide'}]
        unreal = SimpleNamespace(EditorAssetLibrary=Mock(), load_asset=Mock())
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'unreal': unreal}), redirect_stdout(io.StringIO()):
            result = M.apply_hair_guide_cloth(file_record(directory, packet))
        self.assertEqual(result['status'], 'cloth_not_updated')
        self.assertIn('re-export', result['detail'])
        unreal.load_asset.assert_not_called()
        self.assertEqual(unreal.EditorAssetLibrary.mock_calls, [])

    def test_pure_no_guide_saves_only_render_and_never_builds_or_reuses_old_cloth(self):
        _, packet = render_only_packet()
        render = object()
        library = SimpleNamespace(save_loaded_asset=Mock(return_value=True))
        unreal = SimpleNamespace(EditorAssetLibrary=library, load_asset=Mock(return_value=render))
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'unreal': unreal}):
            record = file_record(directory, packet, cloth_asset_path='/Game/Hair/OldFallback')
            result = M.apply_hair_guide_cloth(record)
        self.assertTrue(result['verified'])
        self.assertFalse(result['simulation_enabled'])
        self.assertEqual(result['status'], 'render_only')
        self.assertIsNone(result['cloth_asset_path'])
        self.assertEqual(result['sim_vertices'], 0)
        self.assertEqual(result['render_only_vertices'], 3)
        unreal.load_asset.assert_called_once_with('/Game/Hair/SK_Render')
        library.save_loaded_asset.assert_called_once_with(render)


class NonblockingApplyTests(unittest.TestCase):
    def test_malformed_optional_records_never_modify_unreal_assets(self):
        unreal = SimpleNamespace(EditorAssetLibrary=Mock(), load_asset=Mock())
        for record in (None, [], 'bad', 12, {'version': 99}):
            with self.subTest(record=record), patch.dict('sys.modules', {'unreal': unreal}), redirect_stdout(io.StringIO()):
                result = M.apply_hair_guide_cloth(record)
            self.assertFalse(result['verified'])
            self.assertEqual(result['status'], 'cloth_not_updated')
            self.assertIn('Unsupported hair guide cloth handoff version', result['detail'])
        self.assertEqual(unreal.EditorAssetLibrary.mock_calls, [])
        unreal.load_asset.assert_not_called()

    def test_optional_worker_failure_preserves_record_and_allows_export_to_continue(self):
        record = {'version': 1, 'render_asset_path': '/Game/Hair/SK_Render',
                  'sim_asset_path': '/Game/Hair/SK_Sim', 'extras': {'artist': [1, 2]}}
        original = copy.deepcopy(record)
        events = ['ordinary_fbx_import_completed']
        with patch.object(M, '_apply_hair_guide_cloth', side_effect=RuntimeError('native worker unavailable')), redirect_stdout(io.StringIO()):
            result = M.apply_hair_guide_cloth(record)
            events.append('ordinary_post_import_continued')
        self.assertEqual(record, original)
        self.assertEqual(events, ['ordinary_fbx_import_completed', 'ordinary_post_import_continued'])
        self.assertEqual(result['render_asset_path'], record['render_asset_path'])
        self.assertEqual(result['status'], 'cloth_not_updated')
        self.assertFalse(result['verified'])

    def test_successful_verified_receipt_is_returned_without_rewriting_it(self):
        receipt = {'verified': True, 'cloth_asset_path': '/Game/Hair/CA_GuideCloth'}
        with patch.object(M, '_apply_hair_guide_cloth', return_value=receipt), patch('builtins.print') as output:
            self.assertIs(M.apply_hair_guide_cloth({}), receipt)
        output.assert_not_called()


class ApplyHarness:
    """Exercise public editor handoff failures with an isolated no-op worker."""
    def __init__(self, directory):
        self.root = Path(directory)
        self.record = {'version': 1, 'sim_asset_path': '/Game/Hair/SK_Sim',
                       'simulation_enabled': True,
                       'render_asset_path': '/Game/Hair/SK_Render',
                       'cloth_template_asset_path': '/Game/Hair/CA_Template',
                       'body_mesh_asset_path': '/Game/Body/SK_Body',
                       'physics_asset_path': '/Game/Body/PA_Body',
                       'manifest_sha256': 'new-content'}
        self.target = M._target(self.record)
        self.binding = '/Game/Hair/BindingsNew'
        self.receipt = {'verified': True, 'cloth_asset_path': self.target,
                        'manifest_sha256': 'new-content', 'binding_asset_path': self.binding}
        self.assets = {}
        for path in [self.record[key] for key in ('sim_asset_path', 'render_asset_path',
                     'cloth_template_asset_path', 'body_mesh_asset_path', 'physics_asset_path')] + [self.target]:
            package = SimpleNamespace(get_path_name=lambda p=path: p)
            self.assets[path] = SimpleNamespace(get_outer=lambda p=package: p,
                                                get_path_name=lambda p=path: p, metadata={})
        self.final = self.assets[self.target]
        self.final.metadata = {M.OWNER: M.VERSION + ':' + self.record['render_asset_path'],
                               M.CONTENT: 'old-content', PublishHarness.binding_key: '/Game/Hair/BindingsOld'}
        self.library = SimpleNamespace(does_asset_exist=Mock(return_value=True),
            get_metadata_tag=lambda asset, key: asset.metadata.get(key, ''),
            save_loaded_asset=Mock(return_value=True))
        self.loading = SimpleNamespace(get_dirty_content_packages=Mock(return_value=[]),
                                       fully_load_assets=Mock(), reload_packages=Mock(return_value=True))
        self.unreal = SimpleNamespace(load_asset=lambda path: self.assets[path], EditorAssetLibrary=self.library,
            EditorLoadingAndSavingUtils=self.loading,
            Paths=SimpleNamespace(convert_relative_path_to_full=lambda value: value,
                get_project_file_path=lambda: str(self.root/'Project.uproject'),
                engine_dir=lambda: str(self.root/'UE/Engine')),
            AssetRegistryHelpers=SimpleNamespace(get_asset_registry=lambda: SimpleNamespace(scan_paths_synchronous=Mock())),
            ReloadPackagesInteractionMode=SimpleNamespace(ASSUME_NEGATIVE='negative'))
        worker = self.root/'Scripts/HairGuideCloth/native_worker.py'; worker.parent.mkdir(parents=True)
        worker.write_text('import json\nfrom pathlib import Path\n'
            'def run_script(script, **kwargs):\n'
            '    (Path(script).parent/"receipt.json").write_text(json.dumps('+repr(self.receipt)+'))\n'
            '    return {"ok": True}\n', encoding='utf8')

    def apply(self):
        original = copy.deepcopy(self.record)
        with patch.dict('sys.modules', {'unreal': self.unreal}), patch.object(M, 'validate_record', return_value=(self.record, {})), redirect_stdout(io.StringIO()):
            result = M.apply_hair_guide_cloth(self.record)
        assert self.record == original
        return result


class EditorHandoffTests(unittest.TestCase):
    def test_dirty_existing_cloth_skips_worker_and_preserves_all_editor_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory)
            before = copy.deepcopy(h.final.metadata)
            h.loading.get_dirty_content_packages.return_value = [h.final.get_outer()]
            result = h.apply()
            self.assertFalse(result['verified'])
            self.assertIn('unsaved edits', result['detail'])
            self.assertEqual(h.final.metadata, before)
            h.library.save_loaded_asset.assert_not_called()
            h.loading.fully_load_assets.assert_not_called()
            self.assertFalse((h.root/'Saved').exists())

    def test_reload_failure_is_not_reported_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory); h.loading.reload_packages.return_value = False
            result = h.apply()
            self.assertFalse(result['verified'])
            self.assertEqual(result['status'], 'cloth_not_updated')
            self.assertIn('could not reload', result['detail'])
            self.assertIn(h.final, h.loading.fully_load_assets.call_args.args[0])

    def test_successful_reload_return_with_stale_metadata_is_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory)
            result = h.apply()
            self.assertFalse(result['verified'])
            self.assertIn('older cloth result', result['detail'])

    def test_reload_and_new_metadata_confirm_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory)
            def reload(*args):
                h.final.metadata.update({M.CONTENT: 'new-content', PublishHarness.binding_key: h.binding})
                return True
            h.loading.reload_packages.side_effect = reload
            result = h.apply()
            self.assertEqual(result, h.receipt)
            saved = [call.args[0] for call in h.library.save_loaded_asset.call_args_list]
            self.assertEqual(saved, [h.assets[h.record['sim_asset_path']], h.assets[h.record['render_asset_path']]])


class PublishHarness:
    """Model package persistence, not Chaos or Dataflow implementation.

    UE DuplicateAsset does not save with SourceControl disabled. This fake makes
    that distinction explicit, so an in-memory success cannot pass as publication.
    Native/RHI tests still cover the actual collection and deformer behavior.
    """
    target = '/Game/Hair/CA_GuideCloth'
    source_path = '/Game/Hair/BuildSource'
    old_binding = '/Game/Hair/BindingsOld'
    new_binding = '/Game/Hair/BindingsNew'
    owner = M.VERSION + ':/Game/Hair/SK_Render'
    binding_key = 'Send2UE.HairGuideCloth.BindingData'

    def __init__(self, existing=True):
        self.assets = {}
        self.saved = {}
        self.updates = []
        self.save_attempts = []
        self.bad_stage = False
        self.stage_save_fails = False
        self.fail_next_target_save = False
        self.source = self.asset(self.source_path, self.new_binding, source=True)
        if existing:
            final = self.asset(self.target, self.old_binding)
            final.metadata = {M.OWNER: self.owner, M.CONTENT: 'old-content', self.binding_key: self.old_binding}
            self.saved[self.target] = self.snapshot(final)
        self.unreal = SimpleNamespace(
            load_asset=lambda path: self.assets.get(path),
            EditorAssetLibrary=SimpleNamespace(
                duplicate_asset=self.duplicate,
                does_asset_exist=lambda path: path in self.assets,
                get_metadata_tag=lambda asset, key: asset.metadata.get(key, ''),
                set_metadata_tag=lambda asset, key, value: asset.metadata.__setitem__(key, value),
                save_loaded_asset=self.save),
            CodexClothToolsLibrary=SimpleNamespace(dump_cloth_collection_colors=self.dump),
            DataflowBlueprintLibrary=SimpleNamespace(evaluate_terminal_node_by_name=lambda *args: None))

    def asset(self, path, binding, source=False):
        node_types = {'ClothAssetTerminal': 'FChaosClothAssetTerminalNode',
                      ('SourceInput' if source else 'GeneratorBindings'):
                      ('FChaosClothAssetSkeletalMeshImportNode_v2' if source else 'FChaosClothAssetImportNode')}
        asset = SimpleNamespace(path=path, binding=binding, metadata={}, node_types=node_types, import_lod=0)
        asset.get_path_name = lambda: path + '.' + path.rsplit('/', 1)[-1]
        self.assets[path] = asset
        return asset

    def snapshot(self, asset):
        return {'binding': asset.binding, 'metadata': copy.deepcopy(asset.metadata),
                'nodes': dict(asset.node_types), 'import_lod': asset.import_lod}

    def duplicate(self, source, destination):
        original = self.assets[source.split('.', 1)[0]]
        asset = self.asset(destination, original.binding)
        # Actual UE DuplicateAsset does not copy per-object package UMetaData.
        # First-publication metadata therefore needs explicit writes on final.
        asset.node_types = dict(original.node_types)
        asset.import_lod = original.import_lod
        return asset

    def save(self, asset):
        self.save_attempts.append(asset.path)
        if self.stage_save_fails and asset.path.endswith('_FinalStage'):
            return False
        if self.fail_next_target_save and asset.path == self.target:
            self.fail_next_target_save = False
            return False
        self.saved[asset.path] = self.snapshot(asset)
        return True

    def dump(self, path):
        binding = path if path in (self.old_binding, self.new_binding) else self.assets[path].binding
        if self.bad_stage and path.endswith('_FinalStage'):
            binding = 'invalid-stage-collection'
        return json.dumps({'collections': [{'binding_revision': binding}]})

    def graph(self, unreal, asset):
        nodes = {name: SimpleNamespace(name=name) for name in asset.node_types}
        infos = {name: {'type': typ, 'inputPins': [{'name': 'CollectionLods[0]'}]
                        if name == 'ClothAssetTerminal' else []}
                 for name, typ in asset.node_types.items()}
        for name, info in infos.items():
            if info['type'] == 'FChaosClothAssetImportNode':
                info['properties'] = {
                    'ClothAsset': "/Script/ChaosClothAsset.ChaosClothAsset'" + asset.binding + '.' + asset.binding.rsplit('/', 1)[-1] + "'",
                    'ImportLod': str(asset.import_lod)}

        def call(operation, *args):
            if operation == 'RemoveNode':
                del asset.node_types[args[1].name]
            elif operation == 'AddNode':
                _, typ, name, props, _, _ = args
                asset.node_types[name] = typ
                asset.binding = json.loads(props)['ClothAsset']
                return SimpleNamespace(name=name)
            elif operation == 'UpdateNode':
                props = json.loads(args[1]); asset.binding = props['ClothAsset']
                asset.import_lod = props['ImportLod']
                self.updates.append((asset.path, asset.binding))
            elif operation == 'ConnectNodePins':
                return True
            else:
                raise AssertionError('Unexpected graph operation: ' + operation)
            return True

        return asset, call, {}, nodes, infos

    def publish(self):
        with patch.object(M, '_graph', side_effect=self.graph):
            return M._publish(self.unreal, self.source, self.new_binding,
                              self.target, self.owner, 'new-content')


class PublicationTests(unittest.TestCase):
    def test_first_publication_is_explicitly_saved_without_source_control(self):
        h = PublishHarness(existing=False)
        h.publish()
        self.assertIn(h.target, h.saved, 'DuplicateAsset alone is not a disk save in the cold worker')
        self.assertEqual(h.saved[h.target]['binding'], h.new_binding)
        self.assertEqual(h.saved[h.target]['metadata'][M.CONTENT], 'new-content')
        self.assertEqual(h.saved[h.target]['metadata'][M.OWNER], h.owner)
        self.assertEqual(h.saved[h.target]['metadata'][h.binding_key], h.new_binding)

    def test_reexport_updates_same_owned_asset_after_stage_verification(self):
        h = PublishHarness()
        existing = h.assets[h.target]
        h.publish()
        self.assertIs(h.assets[h.target], existing)
        self.assertEqual(h.saved[h.target]['binding'], h.new_binding)
        self.assertEqual(h.saved[h.target]['metadata'][h.binding_key], h.new_binding)
        self.assertEqual(h.updates, [(h.target, h.new_binding)])

    def test_reexport_keeps_ordinary_unicode_names_and_mounts(self):
        class OrdinaryNamedHarness(PublishHarness):
            old_binding = '/CharacterContent/앞머리/이전바인딩'
            new_binding = '/CharacterContent/앞머리/새바인딩'
        h = OrdinaryNamedHarness()
        h.publish()
        self.assertEqual(h.saved[h.target]['binding'], h.new_binding)
        self.assertEqual(h.updates, [(h.target, h.new_binding)])

    def test_stage_collection_mismatch_leaves_previous_disk_and_memory_unchanged(self):
        h = PublishHarness(); before = copy.deepcopy(h.saved[h.target]); h.bad_stage = True
        with self.assertRaisesRegex(RuntimeError, 'Final import changed'):
            h.publish()
        self.assertEqual(h.saved[h.target], before)
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [])

    def test_stage_save_failure_leaves_previous_disk_and_memory_unchanged(self):
        h = PublishHarness(); before = copy.deepcopy(h.saved[h.target]); h.stage_save_fails = True
        with self.assertRaisesRegex(RuntimeError, 'staging asset could not be saved'):
            h.publish()
        self.assertEqual(h.saved[h.target], before)
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [])

    def test_failed_reexport_save_rolls_back_binding_metadata_and_previous_collection(self):
        h = PublishHarness(); before = copy.deepcopy(h.saved[h.target]); h.fail_next_target_save = True
        with self.assertRaisesRegex(RuntimeError, 'Updated cloth could not be saved'):
            h.publish()
        self.assertEqual(h.saved[h.target], before)
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [(h.target, h.new_binding), (h.target, h.old_binding)])

    def test_user_edited_graph_is_not_replaced(self):
        h = PublishHarness()
        h.assets[h.target].node_types['ArtistAdjustment'] = 'FChaosClothAssetWeightMapNode'
        before = h.snapshot(h.assets[h.target])
        with self.assertRaisesRegex(ValueError, 'user changes'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [])

    def test_unrelated_target_is_not_replaced(self):
        h = PublishHarness()
        h.assets[h.target].metadata[M.OWNER] = 'someone-else'
        before = h.snapshot(h.assets[h.target])
        with self.assertRaisesRegex(ValueError, 'another asset'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [])

    def test_saved_user_changed_import_asset_with_same_two_nodes_is_untouched(self):
        h = PublishHarness()
        h.assets[h.target].binding = '/Game/Hair/ArtistBindings'
        before = h.snapshot(h.assets[h.target]); h.saved[h.target] = copy.deepcopy(before)
        with self.assertRaisesRegex(ValueError, 'Import values have user changes'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.saved[h.target], before)
        self.assertEqual(h.updates, [])

    def test_saved_user_changed_import_lod_is_untouched(self):
        h = PublishHarness()
        h.assets[h.target].import_lod = 1
        before = h.snapshot(h.assets[h.target]); h.saved[h.target] = copy.deepcopy(before)
        with self.assertRaisesRegex(ValueError, 'Import values have user changes'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.saved[h.target], before)
        self.assertEqual(h.updates, [])


if __name__ == '__main__':
    unittest.main()
