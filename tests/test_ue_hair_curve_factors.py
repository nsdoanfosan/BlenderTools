import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from test_ue_hair_guide_cloth import M, file_record, fixture, render_only_packet


class GuideParameterTests(unittest.TestCase):
    def test_import_splits_use_the_same_original_parameter(self):
        built, packet = fixture()
        built['render_positions'].append(built['render_positions'][0])
        packet['meshes']['sim']['curve_factors'] = [0., .5, 1.]
        packet['meshes']['render']['curve_factors'] = [.1, .6, .9]
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['sim_curve_factors'], [0., .5, 1.])
        self.assertEqual(result['render_curve_factors'], [.1, .6, .9, .1])

    def test_same_parent_coincident_points_cannot_silently_pick_distinct_parameters(self):
        built, packet = fixture()
        render = packet['meshes']['render']
        render['vertices'].append(list(render['vertices'][0]))
        render['guide_ids'].append(render['guide_ids'][0])
        render['curve_factors'] = [.1, .6, .9, .2]
        packet['meshes']['sim']['curve_factors'] = [0., .5, 1.]
        # Both original faces match this imported corner, including its edges.
        render['triangles'].append([3, 1, 2])
        for second_t in (.2, .1000001):
            with self.subTest(second_t=second_t):
                render['curve_factors'][3] = second_t
                with self.assertRaisesRegex(ValueError, 'ambiguous guide parameters'):
                    M.normalize_ownership(built, packet)
        render['triangles'].pop()
        self.assertEqual(M.normalize_ownership(built, packet)['render_curve_factors'], [.1, .6, .9])

    def test_missing_parameters_preserve_legacy_packets_and_render_only_skinning(self):
        built, packet = render_only_packet(mixed=True)
        self.assertNotIn('sim_curve_factors', M.normalize_ownership(built, packet))
        packet['meshes']['sim']['curve_factors'] = [0., .5, 1.]
        packet['meshes']['render']['curve_factors'] = [.1, .6, .9, -1., -1., -1.]
        result = M.normalize_ownership(built, packet)
        self.assertTrue(all(result['render_curve_factors'][i] == -1 for i in result['render_only_vertex_indices']))
        packet['meshes']['render']['curve_factors'][3] = 0.
        with self.assertRaisesRegex(ValueError, 'Render-only'):
            M.normalize_ownership(built, packet)

    def test_coincident_tips_use_neighbor_identity_across_two_triangle_fans(self):
        built, packet = fixture()
        render = packet['meshes']['render']
        # A/B are coincident tips. C/D are their coincident neighbors at the
        # same parameter, distinguished only by the remote E/F positions.
        positions = [(0., 0.), (0., 0.), (1., 0.), (1., 0.),
                     (0., 1.), (1., 1.), (2., 1.), (1., -1.)]
        render['vertices'] = [[x, y, 0.] for x, y in positions]
        render['triangles'] = [[0, 2, 4], [1, 3, 4], [2, 5, 7], [3, 6, 7]]
        render['guide_ids'] = [0] * len(positions)
        render['curve_factors'] = [.1, .9, .5, .5, .2, .3, .4, .8]
        packet['meshes']['sim']['curve_factors'] = [0., .5, 1.]
        built['render_positions'] = [[x * 100., -y * 100., 0.] for x, y in positions]
        built['render_triangles'] = render['triangles']
        result = M.normalize_ownership(built, packet)
        self.assertEqual(result['render_source_indices'], list(range(len(positions))))
        self.assertEqual(result['render_curve_factors'], render['curve_factors'])

    def test_full_build_requires_current_native_receipt(self):
        with self.assertRaisesRegex(RuntimeError, 'Native helper'):
            M._verify_curve_binding({}, 15)
        preview = {'source_curve_binding_version': 2, 'source_curve_bound_vertices': 15,
                   'source_curve_edge_coherent': True}
        for built in (False, True):
            with self.subTest(built=built), self.assertRaisesRegex(RuntimeError, 'version 2 is required'):
                M._verify_curve_binding(dict(preview, source_curve_binding_version=1,
                                             compiled_source_curve_storage_verified=True), 15, built=built)
            for invalid in (None, False, 1, 'true'):
                with self.subTest(built=built, invalid=invalid), self.assertRaisesRegex(RuntimeError, 'connected-edge'):
                    M._verify_curve_binding(dict(preview, source_curve_edge_coherent=invalid,
                                                 compiled_source_curve_storage_verified=True), 15, built=built)
        M._verify_curve_binding(preview, 15)
        with self.assertRaisesRegex(RuntimeError, 'Native helper'):
            M._verify_curve_binding(preview, 16)
        with self.assertRaisesRegex(RuntimeError, 'Native helper'):
            M._verify_curve_binding(preview, 15, built=True)
        M._verify_curve_binding(dict(preview, compiled_source_curve_storage_verified=True), 15, built=True)
        M._verify_curve_binding({}, 0, built=True)

    def test_stale_native_preview_stops_before_binding_creation_or_publication(self):
        for version, error in ((1, 'version 2 is required'), (2, 'connected-edge')):
            with self.subTest(version=version):
                self.assert_stale_preview_stops(version, error)

    def assert_stale_preview_stops(self, version, error):
        built, packet = fixture()
        for mesh in packet['meshes'].values():
            mesh['curve_factors'] = [0., .5, 1.]
        unreal = MagicMock()
        request_assets = set()
        library = unreal.EditorAssetLibrary
        library.does_asset_exist.side_effect = request_assets.__contains__
        library.delete_asset.side_effect = lambda path: request_assets.remove(path) or True

        def build_source(_unreal, _record, source_path, _packet):
            request_assets.add(source_path)
            return object(), {'source_request_assets': [source_path], 'collections': [{'built_mapping': built}]}

        repair = unreal.CodexClothToolsLibrary.repair_cloth_proxy_bindings
        stale = {'success': True, 'source_curve_binding_version': version, 'source_curve_bound_vertices': 3}
        repair.return_value = json.dumps(stale)
        with tempfile.TemporaryDirectory() as root, patch.dict('sys.modules', unreal=unreal), \
                patch.object(M, '_build_source', side_effect=build_source), patch.object(M, '_publish') as publish:
            with self.assertRaisesRegex(RuntimeError, error):
                M.build_in_editor(file_record(root, packet), root)
            self.assertEqual(json.loads((Path(root) / 'binding_preview.json').read_text()), stale)
            self.assertFalse((Path(root) / 'receipt.json').exists())
        repair.assert_called_once()
        source, binding, _, write = repair.call_args.args
        self.assertFalse(write)
        self.assertTrue(binding.endswith('/CA_BindingData'))
        library.delete_asset.assert_called_once_with(source)
        self.assertFalse(request_assets)
        library.save_loaded_asset.assert_not_called()
        publish.assert_not_called()


class ArchivedBindingRebuildTests(unittest.TestCase):
    def packet(self):
        _, packet = fixture()
        packet['parts'] = [dict(gid=7, source_guide='Guide', source_render='Strands',
                                simulation_mesh=dict(native_island_to_spline={'0': 0}))]
        return packet

    def test_replays_reject_missing_or_disabled_curve_correspondence_before_writes(self):
        with tempfile.TemporaryDirectory() as root, patch.dict('sys.modules', unreal=MagicMock()):
            for value in (None, [-1., -1., -1.], [0., -1., 1.]):
                packet = self.packet()
                if value is not None:
                    for mesh in packet['meshes'].values():
                        mesh['curve_factors'] = value
                for mode in (None, 'simulation_only'):
                    record = file_record(root, packet, update_mode=mode)
                    with self.subTest(value=value, mode=mode):
                        if value != [0., -1., 1.]:
                            self.assertEqual(M.validate_record(record)[1], packet)
                        with self.assertRaisesRegex(ValueError, 'Re-export|Incomplete curve parameter'):
                            M._apply_hair_guide_cloth(record)
                        with self.assertRaisesRegex(ValueError, 'Re-export|Incomplete curve parameter'):
                            M.build_in_editor(record, root)

    def test_only_represented_child_guides_with_recorded_spline_identity_need_factors(self):
        packet = self.packet()
        mesh = packet['parts'][0]['simulation_mesh']
        mesh.clear()
        mesh.update(requested_mode='RIBBON', effective_mode='ORIGINAL', profile='UNKNOWN')
        with tempfile.TemporaryDirectory() as root:
            M.validate_record(file_record(root, packet), for_import=True)
            packet['parts'].append(dict(gid=8, source_guide='Unused', source_render='UnusedRender',
                                        simulation_mesh=dict(native_island_to_spline={'0': 0})))
            sim = packet['meshes']['sim']
            sim['vertices'] += [[.1, 0, 0], [.11, 0, 0], [.1, .01, 0]]
            sim['triangles'].append([3, 4, 5])
            sim['guide_ids'] += [8, 8, 8]
            sim['weights'] += [0, 0, 1]
            M.validate_record(file_record(root, packet), for_import=True)
            packet['sources'] = [dict(status='resolved', guide='Guide', render='Strands',
                render_ownership={'basis': 'independent_source_face_and_curve_stamps'})]
            with self.assertRaisesRegex(ValueError, 'Re-export Guide'):
                M.validate_record(file_record(root, packet), for_import=True)
            for m in packet['meshes'].values():
                m['curve_factors'] = [0., .5, 1.]
            sim['curve_factors'] += [-1., -1., -1.]
            M.validate_record(file_record(root, packet), for_import=True)

    def test_response_only_preserves_existing_binding_for_both_public_entries(self):
        packet = self.packet()
        packet['response_mask'] = dict(M.RESPONSE_MASK_CONTRACT, present=True,
                                       values=[0., 0., 1.], authored_values=[0., 0., 1.])
        unreal = MagicMock()
        graph = unreal.load_asset.return_value.get_editor_property.return_value.get_editor_property.return_value
        graph.get_path_name.return_value = '/Game/Hair/Graph.Graph'
        unreal.EditorAssetLibrary.get_metadata_tag.return_value = M.VERSION + ':/Game/Hair/SK_Render'
        receipt = {'verified': True, 'binding_asset_path': '/Game/Hair/CurrentBinding'}
        with tempfile.TemporaryDirectory() as root, patch.dict('sys.modules', unreal=unreal), \
                patch.object(M, '_apply_response_only', return_value=receipt) as update:
            record = file_record(root, packet, update_mode='response_only')
            self.assertEqual(M.apply_hair_guide_cloth(record), receipt)
            self.assertEqual(M.build_in_editor(record, root), receipt)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(json.loads((Path(root)/'receipt.json').read_text()), receipt)
            unreal.CodexClothToolsLibrary.repair_cloth_proxy_bindings.assert_not_called()
