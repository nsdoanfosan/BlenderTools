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


class OptionalPhysicsContractTests(unittest.TestCase):
    def test_simulation_without_external_collision_keeps_its_real_guide(self):
        for value in ('', None, 'omitted'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                _, packet = fixture()
                record = file_record(directory, packet, physics_asset_path=value)
                if value == 'omitted':
                    del record['physics_asset_path']
                result, validated = M.validate_record(record)
                self.assertTrue(result['simulation_enabled'])
                self.assertEqual(result['physics_asset_path'], '')
                self.assertEqual(validated['meshes']['sim'], packet['meshes']['sim'])

    def test_collision_path_is_preserved_and_bad_path_is_not_silently_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            _, packet = fixture()
            record = file_record(directory, packet)
            result, _ = M.validate_record(record)
            self.assertEqual(result['physics_asset_path'], '/Game/Body/PA_Body')
            record['physics_asset_path'] = 'PA_Body'
            with self.assertRaisesRegex(ValueError, 'Expected an Unreal asset package path'):
                M.validate_record(record)


class NoGuideContractTests(unittest.TestCase):
    def test_zero_weight_guides_are_render_only_in_pure_and_mixed_exports(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed), tempfile.TemporaryDirectory() as directory:
                built, packet = render_only_packet(mixed=mixed)
                source = packet['render_only_sources'][0]
                source.update(status='guide_weights_all_zero', guide='AuthoredGuide',
                              authored_weight_present=True, excluded_sim_vertices=80)
                record, validated = M.validate_record(file_record(directory, packet))
                self.assertEqual(record['simulation_enabled'], mixed)
                if mixed:
                    mapped = M.normalize_ownership(built, validated)
                    self.assertEqual(mapped['sim_guide_ids'], [7, 7, 7])
                    self.assertEqual(mapped['sim_weights'], [0, 0, 1])
                    self.assertEqual(mapped['render_only_vertex_indices'], [3, 4, 5])
                else:
                    receipt = M._render_only_receipt(record, validated)
                    self.assertEqual(receipt['static_guide_sources'], [source])
                    self.assertEqual(receipt['sim_vertices'], 0)

    def test_unweighted_guide_never_loads_or_builds_a_simulation_asset(self):
        _, packet = render_only_packet()
        packet['render_only_sources'][0].update(
            status='guide_weights_all_zero', guide='UnpaintedGuide', authored_weight_present=False)
        render = object()
        unreal = SimpleNamespace(load_asset=Mock(return_value=render),
            EditorActorSubsystem=object, get_editor_subsystem=lambda cls: SimpleNamespace(get_all_level_actors=lambda: []),
            EditorAssetLibrary=SimpleNamespace(save_loaded_asset=Mock(return_value=True)))
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'unreal': unreal}):
            result = M.apply_hair_guide_cloth(file_record(directory, packet))
        self.assertTrue(result['verified'])
        self.assertFalse(result['simulation_enabled'])
        self.assertIsNone(result['cloth_asset_path'])
        unreal.load_asset.assert_called_once_with('/Game/Hair/SK_Render')

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
        unreal = SimpleNamespace(EditorAssetLibrary=library, load_asset=Mock(return_value=render),
            EditorActorSubsystem=object, get_editor_subsystem=lambda cls: SimpleNamespace(get_all_level_actors=lambda: []))
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

    def test_native_failure_receipt_preserves_imported_mesh_without_claiming_success(self):
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
    """Exercise the current editor handoff without launching another editor."""
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
            instance = SimpleNamespace(get_editor_property=lambda name: {'dataflow_asset': None}[name])
            self.assets[path] = SimpleNamespace(get_outer=lambda p=package: p,
                get_path_name=lambda p=path: p, metadata={},
                get_editor_property=lambda name, i=instance: {'dataflow_instance': i}[name])
        self.final = self.assets[self.target]
        self.final.metadata = {M.OWNER: M.VERSION + ':' + self.record['render_asset_path'],
                               M.CONTENT: 'old-content', PublishHarness.binding_key: '/Game/Hair/BindingsOld'}
        self.library = SimpleNamespace(does_asset_exist=Mock(return_value=True),
            get_metadata_tag=lambda asset, key: asset.metadata.get(key, ''),
            save_loaded_asset=Mock(return_value=True))
        self.loading = SimpleNamespace(get_dirty_content_packages=Mock(return_value=[]),
                                       fully_load_assets=Mock(), reload_packages=Mock(return_value=True))
        self.unreal = SimpleNamespace(load_asset=lambda path: self.assets[path], EditorAssetLibrary=self.library,
            EditorActorSubsystem=object, get_editor_subsystem=lambda cls: SimpleNamespace(get_all_level_actors=lambda: []),
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
        with patch.dict('sys.modules', {'unreal': self.unreal}), patch.object(M, 'validate_record', return_value=(self.record, {})), patch.object(M, 'build_in_editor', side_effect=self.build), redirect_stdout(io.StringIO()):
            result = M.apply_hair_guide_cloth(self.record)
        assert self.record == original
        return result

    def build(self, record, output):
        if getattr(self, 'publish', True):
            self.final.metadata.update({M.CONTENT: 'new-content', PublishHarness.binding_key: self.binding})
        return dict(self.receipt)


class EditorHandoffTests(unittest.TestCase):
    def test_no_external_collision_does_not_load_an_empty_asset_path(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory)
            h.record['physics_asset_path'] = ''
            result = h.apply()
            self.assertTrue(result['verified'])
            self.assertEqual(result['cloth_asset_path'], h.target)

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

    def test_unverified_live_build_is_not_reported_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory); h.receipt['verified'] = False
            result = h.apply()
            self.assertFalse(result['verified'])
            self.assertEqual(result['status'], 'cloth_not_updated')
            self.assertIn('verified receipt', result['detail'])
            h.loading.reload_packages.assert_not_called()

    def test_successful_reload_return_with_stale_metadata_is_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            h = ApplyHarness(directory); h.publish = False
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
            self.assertEqual(result, dict(h.receipt, live_components=[]))
            saved = [call.args[0] for call in h.library.save_loaded_asset.call_args_list]
            self.assertEqual(saved, [h.assets[h.record['sim_asset_path']], h.assets[h.record['render_asset_path']]])
            h.loading.reload_packages.assert_not_called()
            h.loading.fully_load_assets.assert_not_called()


class AuthoredMapCorrespondenceTests(unittest.TestCase):
    def test_diagonal_flip_keeps_the_same_painted_quad_but_rejects_a_new_vertex(self):
        from collections import Counter
        a=Counter({(0,1,3):1,(0,2,3):1})
        self.assertTrue(M._same_painted_vertex_support(a,Counter({(0,1,2):1,(1,2,3):1})))
        self.assertFalse(M._same_painted_vertex_support(a,Counter({(0,1,2):1,(1,2,4):1})))

    def test_rooted_ribbon_resampling_preserves_both_rails_and_endpoints(self):
        def strip(ts):
            return {'sim_positions':[[x,t,0] for t in ts for x in (0,1)],
                'sim_triangles':[list(tri) for i in range(len(ts)-1)
                    for tri in ((2*i,2*i+1,2*i+3),(2*i,2*i+3,2*i+2))],
                'kinematic_sim_indices':[0,1]}
        old,new=strip([0,.5,1]),strip([0,.25,.75,1])
        match=M._ribbon_paint_correspondence(old,new,list(range(6)),list(range(8)),list(range(6)),list(range(8)))
        paint=[0,1,2,3,4,5]
        self.assertEqual([paint[a]*(1-t)+paint[b]*t for a,b,t in (match[i] for i in range(8))],
                         [0,1,1,2,3,4,4,5])
        new['sim_positions'][0][2]=1
        with self.assertRaisesRegex(ValueError,'attachment changed'):
            M._ribbon_paint_correspondence(old,new,list(range(6)),list(range(8)),list(range(6)),list(range(8)))

    def identity_case(self):
        props = {'MeshTarget':'Simulation','MapOverrideType':'ReplaceAll',
                 'Snapshots':'(ActiveSnapshot=-1)','bIsFrozen':'False'}
        nodes = [{'name':name,'type':'FChaosClothAssetWeightMapNode',
                  'props':dict(props,OutputName='(StringValue="'+name+'")',
                               VertexWeights='('+','.join(map(str,values))+')')}
                 for name,values in [('CodexParentForceKey',[.125]*3),('SideRenderClearance',[.15,.6,3.17])]]
        old={'built_mapping':{'sim_positions':[[0,0,0],[1,0,0],[0,1,0]],
             'sim_triangles':[[0,1,2]],'kinematic_sim_indices':[0],
             'sim_import_vertex_ids_2d':[0,1,2]}}
        new={'built_mapping':{'sim_positions':[[0,1.5,0],[0,0,0],[1,0,0]],
             'sim_triangles':[[1,2,0]],'kinematic_sim_indices':[1]}}
        owner={'sim_source_indices':[2,0,1],'sim_source_identity_unique':[True]*3}
        return {'nodes':nodes},old,new,owner

    def test_parent_and_vertex_identity_preserve_paint_after_guide_position_edit(self):
        graph,old,new,owner=self.identity_case()
        with patch.object(M,'_guide_identity_rows',return_value=([.125]*3,{1:{'weight':.125}})):
            maps=M._weight_map_updates(graph,old,new,{'packet':True},owner)
        self.assertEqual(maps['SideRenderClearance'][2],[3.17,.15,.6])

    def test_changed_parent_identity_cannot_reuse_same_numbered_vertex(self):
        graph,old,new,owner=self.identity_case()
        with patch.object(M,'_guide_identity_rows',return_value=([.25]*3,{1:{'weight':.25}})):
            with self.assertRaisesRegex(ValueError,'geometry changed'):
                M._weight_map_updates(graph,old,new,{'packet':True},owner)

    def test_changed_triangle_support_cannot_reuse_vertex_ids(self):
        graph,old,new,owner=self.identity_case();new['built_mapping']['sim_triangles']=[[0,1,1]]
        with patch.object(M,'_guide_identity_rows',return_value=([.125]*3,{1:{'weight':.125}})):
            with self.assertRaisesRegex(ValueError,'geometry changed'):
                M._weight_map_updates(graph,old,new,{'packet':True},owner)

    def test_vertex_reorder_retains_clearance_paint(self):
        old = {'sim_positions': [[0,0,0],[1,0,0],[0,1,0]], 'sim_triangles': [[0,1,2]]}
        new = {'sim_positions': [[0,1,0],[0,0,0],[1,0,0]], 'sim_triangles': [[1,2,0]]}
        self.assertEqual(M._transfer_unchanged_simulation_map(old,new,[.15,3.17,.6]),[.6,.15,3.17])

    def test_omitted_guide_leaves_retained_guide_paint_unchanged(self):
        old = {'sim_positions': [[0,0,0],[1,0,0],[0,1,0],[10,0,0],[11,0,0],[10,1,0]], 'sim_triangles': [[0,1,2],[3,4,5]]}
        new = {'sim_positions': old['sim_positions'][3:], 'sim_triangles': [[0,1,2]]}
        self.assertEqual(M._transfer_unchanged_simulation_map(old,new,[1,2,3,4,5,6]),[4,5,6])

    def test_edited_geometry_is_not_nearest_mapped(self):
        old = {'sim_positions': [[0,0,0],[1,0,0],[0,1,0]], 'sim_triangles': [[0,1,2]]}
        new = copy.deepcopy(old); new['sim_positions'][0][0] = .01
        with self.assertRaisesRegex(ValueError,'geometry changed'):
            M._transfer_unchanged_simulation_map(old,new,[1,2,3])

    def test_coincident_different_paint_is_rejected(self):
        old = {'sim_positions': [[0,0,0],[1,0,0],[0,1,0]]*2, 'sim_triangles': [[0,1,2],[3,4,5]]}
        new = {'sim_positions': old['sim_positions'][:3], 'sim_triangles': [[0,1,2]]}
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            M._transfer_unchanged_simulation_map(old,new,[1,2,3,4,5,6])


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
        self.bad_target_config = False
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
                save_loaded_asset=self.save, delete_asset=self.delete),
            CodexClothToolsLibrary=SimpleNamespace(dump_cloth_collection_colors=self.dump),
            DataflowBlueprintLibrary=SimpleNamespace(evaluate_terminal_node_by_name=lambda *args: None))

    def asset(self, path, binding, source=False):
        node_types = {'ClothAssetTerminal': 'FChaosClothAssetTerminalNode',
                      ('SourceInput' if source else 'GeneratorBindings'):
                      ('FChaosClothAssetSkeletalMeshImportNode_v2' if source else 'FChaosClothAssetImportNode')}
        asset = SimpleNamespace(path=path, binding=binding, metadata={}, node_types=node_types, import_lod=0,
            config_properties={}, connections=[dict(fromNode='SourceInput' if source else 'GeneratorBindings',
                fromPin='Collection',toNode='ClothAssetTerminal',toPin='CollectionLods[0]')])
        asset.get_path_name = lambda: path + '.' + path.rsplit('/', 1)[-1]
        self.assets[path] = asset
        return asset

    def snapshot(self, asset):
        return {'binding': asset.binding, 'metadata': copy.deepcopy(asset.metadata),
                'nodes': dict(asset.node_types), 'import_lod': asset.import_lod,
                'config_properties': copy.deepcopy(asset.config_properties),
                'connections': copy.deepcopy(asset.connections)}

    def duplicate(self, source, destination):
        original = self.assets[source.split('.', 1)[0]]
        asset = self.asset(destination, original.binding)
        # Actual UE DuplicateAsset does not copy per-object package UMetaData.
        # First-publication metadata therefore needs explicit writes on final.
        asset.node_types = dict(original.node_types)
        asset.import_lod = original.import_lod
        asset.config_properties = copy.deepcopy(original.config_properties)
        asset.connections = copy.deepcopy(original.connections)
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

    def delete(self, path):
        path = path.split('.', 1)[0]
        if path not in self.assets:
            return False
        del self.assets[path]
        self.saved.pop(path, None)
        return True

    def dump(self, path):
        binding = path if path in (self.old_binding, self.new_binding) else self.assets[path].binding
        if self.bad_stage and path.endswith('_FinalStage'):
            binding = 'invalid-stage-collection'
        if self.bad_target_config and path == self.target and binding == self.new_binding:
            binding = 'config-changed-geometry-or-weights'
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
                asset.connections = [e for e in asset.connections
                    if args[1].name not in (e['fromNode'], e['toNode'])]
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
                asset.connections.append(dict(fromNode=args[0].name,fromPin=args[1],
                    toNode=args[2].name,toPin=args[3]))
                return True
            else:
                raise AssertionError('Unexpected graph operation: ' + operation)
            return True

        return asset, call, {'connections': asset.connections}, nodes, infos

    def add_solver(self):
        final = self.assets[self.target]
        final.node_types['ExtraSolverStability'] = 'FChaosClothAssetSimulationSolverConfigNode'
        final.config_properties['ExtraSolverStability'] = {'NumIterations': 8, 'MaxNumIterations': 12, 'NumSubsteps': 8}
        final.connections = [
            dict(fromNode='GeneratorBindings',fromPin='Collection',toNode='ExtraSolverStability',toPin='Collection'),
            dict(fromNode='ExtraSolverStability',fromPin='Collection',toNode='ClothAssetTerminal',toPin='CollectionLods[0]')]
        self.saved[self.target] = self.snapshot(final)

    def publish(self):
        with patch.object(M, '_graph', side_effect=self.graph):
            return M._publish(self.unreal, self.source, self.new_binding,
                              self.target, self.owner, 'new-content')

    def add_capsule_collision(self):
        final = self.assets[self.target]
        final.node_types['HairBodyCapsules'] = 'FChaosClothAssetSetPhysicsAssetNode'
        final.node_types['HairCapsuleCollision'] = 'FChaosClothAssetSimulationCollisionConfigNode'
        final.config_properties['HairBodyCapsules'] = {'PhysicsAsset': '/Game/Body/PA_Capsules'}
        final.config_properties['HairCapsuleCollision'] = {
            'bEnableSkinnedTriangleMeshCollisions': False, 'bEnableSimpleColliders': True,
            'bUseCCD': False, 'FrictionCoefficientWeighted': {'Low': .65, 'High': .8}}
        for edge in final.connections:
            if edge['fromNode'] == 'GeneratorBindings':
                edge['fromNode'] = 'HairCapsuleCollision'
        final.connections += [
            dict(fromNode='GeneratorBindings', fromPin='Collection',
                 toNode='HairBodyCapsules', toPin='Collection'),
            dict(fromNode='HairBodyCapsules', fromPin='Collection',
                 toNode='HairCapsuleCollision', toPin='Collection')]
        self.saved[self.target] = self.snapshot(final)


class PublicationTests(unittest.TestCase):
    def test_reexport_preserves_asset_owned_hair_response_across_revisions(self):
        h = PublishHarness(); h.add_solver()
        final = h.assets[h.target]
        final.node_types['ExtraSolverStability'] = 'FCodexHairResponseConfigNode'
        final.config_properties['ExtraSolverStability'] = {
            'bEnableHairResponse': True, 'bEnableHairParentResponse': True,
            'HairGustTimeScale': 3., 'HairParentForceRatio': 1.4,
            'HairTimingVarianceSeconds': .75}
        before = h.snapshot(final)
        for next_binding in ('/Game/Hair/BindingsNew', '/Game/Hair/BindingsNext'):
            h.new_binding = next_binding
            h.publish()
            saved = h.saved[h.target]
            self.assertEqual(saved['config_properties'], before['config_properties'])
            self.assertEqual(saved['nodes'], before['nodes'])
            self.assertEqual(saved['connections'], before['connections'])
            h.old_binding = next_binding

    def test_hair_response_lookalike_node_is_rejected_without_changing_final_asset(self):
        h = PublishHarness(); h.add_solver()
        final = h.assets[h.target]
        final.node_types['ExtraSolverStability'] = 'FCodexHairResponseGeometryConfigNode'
        before = h.snapshot(final)
        with self.assertRaisesRegex(ValueError, 'user changes'):
            h.publish()
        self.assertEqual(h.snapshot(final), before)
        self.assertEqual(h.updates, [])

    def test_template_and_publication_accept_only_registered_hair_response_type(self):
        self.assertTrue(M._is_simulation_config_node('FCodexHairResponseConfigNode'))
        self.assertTrue(M._is_simulation_config_node('FChaosClothAssetSimulationSolverConfigNode'))
        self.assertTrue(M._is_simulation_config_node('FChaosClothAssetSimulationAerodynamicsConfigNode_v2'))
        for node_type in ('FCodexHairResponseConfigNode_v2', 'FCodexHairResponseGeometryConfigNode',
                          'FChaosClothAssetImportNode', 'FChaosClothAssetProxyDeformerNode_v3'):
            self.assertFalse(M._is_simulation_config_node(node_type))

    def test_full_collection_digest_can_differ_for_preserved_artist_configuration(self):
        h = PublishHarness(); h.add_solver(); h.add_capsule_collision()
        original = h.snapshot(h.assets[h.target]); dump = h.dump
        def with_fingerprint(path):
            result = json.loads(dump(path))
            result['collections'][0]['non_uv_collection_md5'] = (
                'artist-config-and-paint' if path == h.target else 'bare-binding')
            return json.dumps(result)
        h.unreal.CodexClothToolsLibrary.dump_cloth_collection_colors = with_fingerprint
        h.publish()
        self.assertEqual(h.assets[h.target].binding, h.new_binding)
        self.assertEqual(h.assets[h.target].config_properties, original['config_properties'])
        self.assertEqual(h.assets[h.target].connections, original['connections'])

    def test_digest_compatibility_still_rejects_changed_binding_and_restores_previous_state(self):
        h = PublishHarness(); h.add_solver(); h.bad_target_config = True
        original = h.snapshot(h.assets[h.target]); dump = h.dump
        def with_fingerprint(path):
            result = json.loads(dump(path))
            result['collections'][0]['non_uv_collection_md5'] = 'full-state'
            return json.dumps(result)
        h.unreal.CodexClothToolsLibrary.dump_cloth_collection_colors = with_fingerprint
        with self.assertRaisesRegex(RuntimeError, 'differs from its verified collection'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), original)
        self.assertEqual(h.saved[h.target], original)

    def test_reexport_saves_capsule_collision_and_artist_solver_across_revisions(self):
        h = PublishHarness(); h.add_solver(); h.add_capsule_collision()
        before = h.snapshot(h.assets[h.target])
        for next_binding in ('/Game/Hair/BindingsNew', '/Game/Hair/BindingsNext'):
            h.new_binding = next_binding
            h.publish()
            saved = h.saved[h.target]
            self.assertEqual(saved['binding'], next_binding)
            self.assertEqual(saved['config_properties'], before['config_properties'])
            self.assertEqual(saved['nodes'], before['nodes'])
            self.assertEqual(saved['connections'], before['connections'])
            h.old_binding = next_binding

    def test_disconnected_body_override_is_not_a_valid_published_graph(self):
        h = PublishHarness(); h.add_capsule_collision()
        final = h.assets[h.target]
        final.connections = [edge for edge in final.connections
                             if edge['toNode'] != 'HairBodyCapsules']
        before = h.snapshot(final)
        with self.assertRaisesRegex(ValueError, 'user changes'):
            h.publish()
        self.assertEqual(h.snapshot(final), before)
        self.assertEqual(h.updates, [])

    def test_parked_native_config_leaf_is_preserved_across_reimports(self):
        h = PublishHarness(); h.add_capsule_collision()
        final = h.assets[h.target]
        final.node_types['ParkedSelf'] = 'FChaosClothAssetSimulationSelfCollisionSpheresConfigNode'
        final.config_properties['ParkedSelf'] = {'Radius':.5,'Stiffness':2.}
        final.connections.append(dict(fromNode='HairBodyCapsules',fromPin='Collection',
                                      toNode='ParkedSelf',toPin='Collection'))
        before = h.snapshot(final)
        h.publish()
        self.assertEqual(final.config_properties,before['config_properties'])
        self.assertEqual(final.connections,before['connections'])

    def test_unconnected_config_subgraph_is_not_treated_as_a_parked_leaf(self):
        h = PublishHarness(); h.add_solver()
        final = h.assets[h.target]
        for name in ('ParkedA','ParkedB'):
            final.node_types[name] = 'FChaosClothAssetSimulationSolverConfigNode'
            final.config_properties[name] = {'NumSubsteps':8}
        final.connections.extend([
            dict(fromNode='GeneratorBindings',fromPin='Collection',toNode='ParkedA',toPin='Collection'),
            dict(fromNode='ParkedA',fromPin='Collection',toNode='ParkedB',toPin='Collection')])
        before = h.snapshot(final)
        with self.assertRaisesRegex(ValueError,'disconnected configuration branch'):
            h.publish()
        self.assertEqual(h.snapshot(final),before)

    def test_reexports_preserve_downstream_solver_configuration_and_connections(self):
        h = PublishHarness(); h.add_solver()
        final = h.assets[h.target]
        original = h.snapshot(final)
        for revision in ('new-content', 'next-content'):
            h.publish()
            self.assertEqual(final.binding, h.new_binding)
            self.assertEqual(final.config_properties, original['config_properties'])
            self.assertEqual(final.node_types, original['nodes'])
            self.assertEqual(final.connections, original['connections'])
            h.old_binding = h.new_binding
            h.new_binding = '/Game/Hair/BindingsNext'

    def test_configuration_that_changes_verified_geometry_or_weights_rolls_back(self):
        h = PublishHarness(); h.add_solver(); h.bad_target_config = True
        before = h.snapshot(h.assets[h.target])
        with self.assertRaisesRegex(RuntimeError, 'differs from its verified collection'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.saved[h.target], before)

    def test_config_not_downstream_of_owned_import_is_not_accepted(self):
        h = PublishHarness(); h.add_solver()
        h.assets[h.target].connections[0]['fromNode'] = 'ExtraSolverStability'
        before = h.snapshot(h.assets[h.target])
        with self.assertRaisesRegex(ValueError, 'user changes'):
            h.publish()
        self.assertEqual(h.snapshot(h.assets[h.target]), before)
        self.assertEqual(h.updates, [])

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


# BEGIN garment wind config preservation regression
def garment_physics_configuration():
    # Nondefault wind8 includes the intentional disabled value. Native physics
    # and weighted-map bindings remain separate artist-owned configuration.
    weighted = lambda low, high: {
        'bIsAnimatable': True, 'Low': low, 'High': high, 'WeightMap': 'MaxDistance'}
    return {
        'ArtistMaxDistance': ('FChaosClothAssetSimulationMaxDistanceConfigNode', {
            'MaxDistance': weighted(8., 61.)}),
        'ArtistAerodynamics': ('FChaosClothAssetSimulationAerodynamicsConfigNode_v2', {
            'Drag': weighted(.113, .671), 'Lift': weighted(.037, .283),
            'bEnableOuterDrag': False, 'bEnableOuterLift': True,
            'WindVelocity': {'X': 17., 'Y': -31., 'Z': 7.}, 'WindVelocitySpace': 'WorldSpace'}),
        'ArtistDamping': ('FChaosClothAssetSimulationDampingConfigNode', {
            'DampingCoefficientWeighted': weighted(.071, .219),
            'LocalDampingCoefficient': .123}),
        'ArtistSolver': ('FChaosClothAssetSimulationSolverConfigNode', {
            'NumIterations': 16, 'MaxNumIterations': 23, 'NumSubsteps': 3}),
        'ArtistHairResponse': ('FCodexHairResponseConfigNode', {
            'bEnableHairResponse': True, 'bEnableHairParentResponse': False,
            'HairGustTimeScale': 2.7, 'HairParentForceRatio': 1.3}),
        'ArtistGarmentWind': ('FCodexGarmentWindConfigNode', {
            'bEnabled': False, 'AirSpeedAtUnitAmplitude': 405.,
            'ResponseExponent': .77, 'MaxAirSpeed': 1370., 'Gust': .83,
            'Flutter': .44, 'MeanSoftCap': 575., 'HighFlutter': .22}),
    }


class GarmentPublishHarness(PublishHarness):
    """Exercise real publication/map-transfer code with synthetic collections."""
    def __init__(self):
        super().__init__()
        final = self.assets[self.target]
        configuration = garment_physics_configuration()
        self.paint = [0.125, 0.625, 0.875]
        final.node_types['ArtistPaint'] = 'FChaosClothAssetWeightMapNode'
        final.config_properties['ArtistPaint'] = {
            'MeshTarget': 'Simulation', 'MapOverrideType': 'ReplaceAll',
            'Snapshots': '(ActiveSnapshot=-1)', 'bIsFrozen': 'False',
            'OutputName': '(StringValue="MaxDistance")',
            'VertexWeights': '(' + ','.join(map(str, self.paint)) + ')'}
        for name, (node_type, props) in configuration.items():
            final.node_types[name] = node_type
            final.config_properties[name] = copy.deepcopy(props)
        chain = ['GeneratorBindings', 'ArtistPaint'] + list(configuration) + ['ClothAssetTerminal']
        final.connections = [dict(fromNode=a, fromPin='Collection', toNode=b,
            toPin='CollectionLods[0]' if b == 'ClothAssetTerminal' else 'Collection')
            for a, b in zip(chain, chain[1:])]
        final.connections.append(dict(fromNode='ArtistPaint', fromPin='OutputName.StringValue',
            toNode='ArtistAerodynamics', toPin='Drag.WeightMap'))
        self.saved[self.target] = self.snapshot(final)
        self.map_updates = []
        self.unreal.CodexClothToolsLibrary.dump_dataflow_nodes = self.dump_nodes

    def graph(self, unreal, asset):
        graph, base_call, structure, nodes, infos = super().graph(unreal, asset)
        # Native GetNodeInfo exports authored properties for every node, not
        # just the owned import. Map identity validation uses that real API.
        for name, props in asset.config_properties.items():
            if name in infos:
                infos[name]['properties'] = copy.deepcopy(props)

        def call(operation, *args):
            if operation == 'UpdateNode' and args[0].name == 'ArtistPaint':
                weights = json.loads(args[1])['VertexWeights']
                asset.config_properties['ArtistPaint']['VertexWeights'] = '(' + ','.join(map(str, weights)) + ')'
                self.map_updates.append((asset.path, list(weights)))
                return True
            return base_call(operation, *args)
        return graph, call, structure, nodes, infos

    def dump_nodes(self, path):
        asset = self.assets[path.split('.', 1)[0]]
        return json.dumps({'nodes': [{'name': name, 'type': node_type,
            'props': copy.deepcopy(asset.config_properties.get(name, {}))}
            for name, node_type in asset.node_types.items()]})

    def dump(self, path):
        result = json.loads(super().dump(path))
        collection = result['collections'][0]
        collection['built_mapping'] = {
            'sim_positions': [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
            'sim_triangles': [[0, 1, 2]], 'kinematic_sim_indices': [0]}
        collection['weight_maps'] = []
        if path == self.target:
            props = self.assets[path].config_properties.get('ArtistPaint')
            if props:
                values = [float(v) for v in props['VertexWeights'].strip('()').split(',')]
                collection['weight_maps'] = [{'name': 'MaxDistance', 'values': values}]
        return json.dumps(result)


class GarmentWindConfigurationTests(unittest.TestCase):
    def test_only_exact_registered_garment_type_is_configuration(self):
        self.assertTrue(M._is_simulation_config_node('FCodexGarmentWindConfigNode'))
        self.assertTrue(M._is_simulation_config_node('FCodexHairResponseConfigNode'))
        self.assertTrue(M._is_simulation_config_node('FChaosClothAssetSimulationSolverConfigNode'))
        for node_type in ('FCodexGarmentWindConfigNode_v2', 'FCodexGarmentWindGeometryConfigNode',
                          'FCodexGarmentWindConfigNodeExtra', 'FCodexGarmentWindConfigNode ',
                          'fcodexgarmentwindconfignode'):
            with self.subTest(node_type=node_type):
                self.assertFalse(M._is_simulation_config_node(node_type))

    def test_template_lod0_retains_disabled_wind8_native_physics_and_hair(self):
        configuration = garment_physics_configuration()
        node_types = {'OldImport': 'FChaosClothAssetSkeletalMeshImportNode_v2',
                      'ClothAssetTerminal': 'FChaosClothAssetTerminalNode'}
        node_types.update({name: row[0] for name, row in configuration.items()})
        chain = ['OldImport'] + list(configuration) + ['ClothAssetTerminal']
        template = SimpleNamespace(node_types=node_types,
            properties={name: copy.deepcopy(row[1]) for name, row in configuration.items()},
            connections=[dict(fromNode=a, fromPin='Collection', toNode=b,
                toPin='CollectionLods[0]' if b == 'ClothAssetTerminal' else 'Collection')
                for a, b in zip(chain, chain[1:])])
        before = copy.deepcopy(vars(template))
        built = []
        operations = []
        saved = []
        record = {'cloth_template_asset_path': '/Game/Fixtures/Template',
                  'sim_asset_path': '/Game/Fixtures/Sim', 'render_asset_path': '/Game/Fixtures/Render',
                  'physics_asset_path': '/Game/Fixtures/BodyPhysics'}
        build_path = '/Game/Fixtures/BuildSource'

        def duplicate(source, destination):
            self.assertEqual((source, destination), (record['cloth_template_asset_path'], build_path))
            asset = copy.deepcopy(template)
            asset.get_path_name = lambda: destination
            built.append(asset)
            return asset

        def graph(unreal, asset):
            nodes = {name: SimpleNamespace(name=name) for name in asset.node_types}
            infos = {name: {'type': node_type,
                'inputPins': [{'name': 'CollectionLods[0]'}] if name == 'ClothAssetTerminal' else []}
                for name, node_type in asset.node_types.items()}

            def call(operation, *args):
                operations.append((operation, args))
                if operation == 'RemoveNode':
                    name = args[1].name
                    del asset.node_types[name]
                    asset.properties.pop(name, None)
                    asset.connections = [e for e in asset.connections
                        if name not in (e['fromNode'], e['toNode'])]
                elif operation == 'AddNode':
                    _, node_type, name, props, _, _ = args
                    asset.node_types[name] = node_type
                    asset.properties[name] = json.loads(props)
                    return SimpleNamespace(name=name)
                elif operation == 'ConnectNodePins':
                    a, output, b, input_pin = args
                    asset.connections = [e for e in asset.connections
                        if (e['toNode'], e['toPin']) != (b.name, input_pin)]
                    asset.connections.append(dict(fromNode=a.name, fromPin=output,
                        toNode=b.name, toPin=input_pin))
                    return True
                else:
                    raise AssertionError('Template configuration must not be rewritten: ' + operation)
                return True
            return asset, call, {'connections': asset.connections}, nodes, infos

        bake = Mock(return_value=json.dumps({'success': True, 'unmatched_sim_vertices': 0,
                                             'snap_distance_max_cm': 0.}))
        audit = {'success': True, 'collections': [{'fixture': 'LOD0'}]}
        unreal = SimpleNamespace(
            EditorAssetLibrary=SimpleNamespace(duplicate_asset=duplicate,
                does_asset_exist=lambda path: False,
                save_loaded_asset=lambda asset: saved.append(asset) or True),
            CodexClothToolsLibrary=SimpleNamespace(bake_vertex_color_weight_map=bake,
                dump_cloth_collection_colors=lambda path: json.dumps(audit)),
            DataflowBlueprintLibrary=SimpleNamespace(evaluate_terminal_node_by_name=Mock()))
        with patch.object(M, '_graph', side_effect=graph):
            asset, actual_audit = M._build_source(unreal, record, build_path)
        self.assertEqual(vars(template), before, 'The template itself must remain untouched')
        self.assertIs(asset, built[0])
        self.assertEqual(actual_audit, audit)
        self.assertEqual(saved, [asset])
        bake.assert_not_called()
        self.assertEqual(asset.node_types['GuideOwnG'], 'FCodexClothVertexColorWeightMapNode')
        self.assertEqual(asset.properties['GuideOwnG']['SourceMesh'], record['sim_asset_path'])
        self.assertNotIn('OldImport', asset.node_types)
        self.assertEqual([args[1].name for op, args in operations if op == 'RemoveNode'], ['OldImport'])
        for name, (node_type, props) in configuration.items():
            self.assertEqual(asset.node_types[name], node_type)
            self.assertEqual(asset.properties[name], props)
        self.assertIs(asset.properties['ArtistGarmentWind']['bEnabled'], False)
        for a, b in zip(list(configuration), list(configuration)[1:]):
            self.assertIn(dict(fromNode=a, fromPin='Collection', toNode=b, toPin='Collection'),
                          asset.connections)
        self.assertIn(dict(fromNode='BodyPhysics', fromPin='Collection',
            toNode='ArtistMaxDistance', toPin='Collection'), asset.connections)
        self.assertIn(dict(fromNode='ArtistGarmentWind', fromPin='Collection',
            toNode='EmbeddingInput', toPin='Collection'), asset.connections)
        self.assertIn(dict(fromNode='GuideOwnG', fromPin='WeightMapName',
            toNode='ArtistMaxDistance', toPin='MaxDistance.WeightMap'), asset.connections)
        self.assertIn(dict(fromNode='SimulationKinematicBlend', fromPin='Collection',
            toNode='ClothAssetTerminal', toPin='CollectionLods[0]'), asset.connections)
        unreal.DataflowBlueprintLibrary.evaluate_terminal_node_by_name.assert_called_once_with(
            asset, 'ClothAssetTerminal', asset)

    def test_two_reexports_preserve_disabled_wind8_native_physics_paint_and_connections(self):
        h = GarmentPublishHarness()
        final = h.assets[h.target]
        before = h.snapshot(final)
        revisions = ('/Game/Hair/BindingsNew', '/Game/Hair/BindingsNext')
        for next_binding in revisions:
            h.new_binding = next_binding
            h.publish()
            self.assertIs(h.assets[h.target], final)
            saved = h.saved[h.target]
            for key in ('config_properties', 'nodes', 'connections'):
                self.assertEqual(saved[key], before[key])
                self.assertEqual(h.snapshot(final)[key], before[key])
            self.assertEqual(saved['binding'], next_binding)
            self.assertEqual(saved['metadata'][h.binding_key], next_binding)
            self.assertIs(saved['config_properties']['ArtistGarmentWind']['bEnabled'], False)
            h.old_binding = next_binding
        self.assertEqual(h.updates, [(h.target, binding) for binding in revisions])
        self.assertEqual(h.map_updates, [], 'Unchanged paint must retain native precision without rewrites')

    def test_garment_lookalikes_are_rejected_without_mutating_published_configuration(self):
        for node_type in ('FCodexGarmentWindConfigNode_v2', 'FCodexGarmentWindGeometryConfigNode',
                          'FCodexGarmentWindConfigNodeExtra', 'fcodexgarmentwindconfignode'):
            with self.subTest(node_type=node_type):
                h = GarmentPublishHarness()
                final = h.assets[h.target]
                final.node_types['ArtistGarmentWind'] = node_type
                before = h.snapshot(final)
                h.saved[h.target] = copy.deepcopy(before)
                with self.assertRaisesRegex(ValueError, 'user changes'):
                    h.publish()
                self.assertEqual(h.snapshot(final), before)
                self.assertEqual(h.saved[h.target], before)
                self.assertEqual(h.updates, [])
                self.assertEqual(h.map_updates, [])
# END garment wind config preservation regression


if __name__ == '__main__':
    unittest.main()
