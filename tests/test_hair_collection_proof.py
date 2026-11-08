"""App-free comparison contracts for opaque native collection fingerprints.

Fixture digests stand for native results; these tests do not implement BLAKE3
or pretend to evaluate a Dataflow graph.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


PIPELINE = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PIPELINE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('collection_proof_contract', 'ue_hair_collection_proof.py')
H = load('collection_publication_contract', 'ue_hair_guide_cloth.py')


def attribute(name, count, type_name='float', type_id=1):
    return {'name': name, 'type_id': type_id, 'type_name': type_name, 'count': count,
            'dependency': '', 'persistent': True, 'codec': 'managed-array',
            'blake3': '1' * 64, 'complete': True, 'group_count': count}


def group(name, count, *attributes):
    return {'name': name, 'count': count,
            'attributes': sorted(attributes, key=lambda row: row['name'])}


def collection():
    groups = [
        group('Lods', 1, attribute('PhysicsAssetSoftObjectPathName', 1, 'FString', 2)),
        group('Property', 2,
              attribute('KeyName', 2, 'FName', 3), attribute('LowValue', 2),
              attribute('HighValue', 2), attribute('StringValue', 2, 'FString', 2),
              attribute('Flags', 2, 'int32', 4)),
        group('PropertyGlobal', 1, attribute('Dirty', 1, 'bool', 5)),
        group('RenderVertices', 3, attribute('Position', 3, 'FVector3f', 6),
              attribute('ProxyDeformerData', 3, 'TArray<int32>', 7)),
        group('Selection', 2, attribute('KinematicVertices3D', 2, 'TSet<int32>', 8)),
        group('SimVertices2D', 3, attribute('UV', 3, 'FVector2f', 9)),
        group('SimVertices3D', 3, attribute('ArtistPaint', 3), attribute('MaxDistance', 3),
              attribute('Color', 3, 'FLinearColor', 10), attribute('Position', 3, 'FVector3f', 6)),
    ]
    return {
        'lod': 0, 'non_uv_collection_md5': 'old-serialization-order',
        'weight_maps': [{'name': 'MaxDistance', 'avg': 1}, {'name': 'ArtistPaint', 'avg': 0.4}],
        'sim_float_attributes': [{'name': 'MaxDistance'}, {'name': 'ArtistPaint'}],
        'render_float_attributes': [{'name': 'Z'}, {'name': 'A'}],
        'built_mapping': {'sim_positions': [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                          'sim_uvs': [[0, 0], [1, 0], [0, 1]], 'sim_colors': [[1, 0, 0, 1]] * 3,
                          'proxy_indices': [0, 1, 2]},
        C.KEY: {'complete': True, 'unsupported': [], 'algorithm': 'blake3-256',
                'blake3': 'a' * 64, 'contract': 'managed_array_collection.semantic.v1',
                'includes_uv': True, 'groups': sorted(groups, key=lambda row: row['name'])},
    }


def audit(row=None):
    return {'asset': 'same', 'collections': [collection() if row is None else row]}


def find_group(row, name):
    return next(item for item in row[C.KEY]['groups'] if item['name'] == name)


def find_attribute(row, group_name, name):
    return next(item for item in find_group(row, group_name)['attributes'] if item['name'] == name)


def change_hash(row, group_name, name):
    find_attribute(row, group_name, name)['blake3'] = '2' * 64
    row[C.KEY]['blake3'] = 'b' * 64


def resize_group(row, group_name, count):
    target = find_group(row, group_name)
    target['count'] = count
    for attr in target['attributes']:
        attr['count'] = attr['group_count'] = count
    row[C.KEY]['blake3'] = 'b' * 64


def add_attribute(row, group_name, name):
    target = find_group(row, group_name)
    target['attributes'].append(attribute(name, target['count']))
    target['attributes'].sort(key=lambda item: item['name'])
    row[C.KEY]['blake3'] = 'b' * 64


class CollectionProofTests(unittest.TestCase):
    def physics_collection(self):
        row = collection()
        row['built_mapping'].update(kinematic_sim_indices=[0], sim_tether_reference_lengths=[[], [1.]],
                                    sim_tether_kinematic_indices=[[], [0]])
        attrs = find_group(row, 'SimVertices3D')['attributes']
        attrs.extend([attribute('TetherKinematicIndex', 3, 'TArray<int32>', 7),
                      attribute('TetherReferenceLength', 3, 'TArray<float>', 11)])
        attrs.sort(key=lambda value: value['name'])
        return row

    def test_artist_physics_derivatives_are_validated_in_stage_and_strict_at_publication(self):
        binding = self.physics_collection()
        expected = copy.deepcopy(binding)
        for field in ('kinematic_sim_indices', 'sim_tether_reference_lengths', 'sim_tether_kinematic_indices'):
            expected['built_mapping'][field] = [0, 1]
        for group_name, name in [('Selection', 'KinematicVertices3D'),
                                 ('SimVertices3D', 'TetherKinematicIndex'),
                                 ('SimVertices3D', 'TetherReferenceLength')]:
            change_hash(expected, group_name, name)
        H._verify_physics_derivation(audit(expected), binding)
        H._verify_published_collection(audit(expected), expected, set())
        with self.assertRaises(RuntimeError):
            H._verify_published_collection(audit(binding), expected, set())

    def test_artist_physics_stage_still_rejects_geometry_uv_color_paint_and_proxy_changes(self):
        binding = self.physics_collection()
        for group_name, name in [('SimVertices3D', 'MaxDistance'), ('SimVertices3D', 'ArtistPaint'),
                                 ('SimVertices3D', 'Color'), ('SimVertices3D', 'Position'),
                                 ('SimVertices2D', 'UV'), ('RenderVertices', 'ProxyDeformerData')]:
            with self.subTest(attribute=name):
                changed = copy.deepcopy(binding)
                change_hash(changed, group_name, name)
                with self.assertRaises(RuntimeError):
                    H._verify_physics_derivation(audit(changed), binding)

    def test_artist_physics_derivation_requires_complete_evidence(self):
        binding = self.physics_collection()
        changed = copy.deepcopy(binding)
        changed['built_mapping'].pop('sim_tether_reference_lengths')
        with self.assertRaises(ValueError):
            H._verify_physics_derivation(audit(changed), binding)
        changed = copy.deepcopy(binding)
        changed.pop(C.KEY)
        with self.assertRaises(ValueError):
            H._verify_physics_derivation(audit(changed), changed)

    def test_transferred_paint_is_allowed_in_stage_and_fully_covered_at_publication(self):
        binding = self.physics_collection()
        expected = copy.deepcopy(binding)
        add_attribute(expected, 'SimVertices3D', 'SideRenderClearance')
        expected['weight_maps'].append({'name': 'SideRenderClearance', 'avg': 0.75})
        expected['sim_float_attributes'].append({'name': 'SideRenderClearance'})
        H._verify_physics_derivation(audit(expected), binding, added_names={'SideRenderClearance'})
        changed = copy.deepcopy(expected)
        change_hash(changed, 'SimVertices3D', 'SideRenderClearance')
        changed[C.KEY]['blake3'] = 'c' * 64
        with self.assertRaises(RuntimeError):
            H._verify_published_collection(audit(changed), expected, {'SideRenderClearance'})

    def test_only_serialization_and_exposed_name_order_are_normalized(self):
        before, after = audit(), audit()
        after['collections'][0]['non_uv_collection_md5'] = 'new-serialization-order'
        for key in ('weight_maps', 'sim_float_attributes', 'render_float_attributes'):
            after['collections'][0][key].reverse()
        original = copy.deepcopy((before, after))
        self.assertTrue(C.assert_equal(before, after))
        self.assertEqual(C.proof_view(before), C.proof_view(after))
        self.assertEqual((before, after), original)

    def test_root_value_change_is_rejected(self):
        before, after = audit(), audit()
        after['collections'][0][C.KEY]['blake3'] = 'b' * 64
        with self.assertRaises(ValueError):
            C.assert_equal(before, after)

    def test_attribute_change_is_rejected_even_with_an_unchanged_root(self):
        before, after = audit(), audit()
        find_attribute(after['collections'][0], 'SimVertices3D', 'ArtistPaint')['blake3'] = '2' * 64
        with self.assertRaises(ValueError):
            C.assert_equal(before, after)

    def test_incomplete_or_unsupported_proof_cannot_even_equal_itself(self):
        for key, value in (('complete', False), ('unsupported', ['pointer']),
                           ('algorithm', 'unknown'), ('includes_uv', False),
                           ('contract', 'unknown'), ('blake3', 'invalid')):
            with self.subTest(key=key):
                current = audit()
                current['collections'][0][C.KEY][key] = value
                with self.assertRaises(ValueError):
                    C.assert_equal(current, current)

    def test_old_native_output_remains_strict_and_is_not_mutated(self):
        before = audit()
        before['collections'][0].pop(C.KEY)
        after = copy.deepcopy(before)
        self.assertTrue(C.assert_equal(before, after))
        self.assertEqual(C.proof_view(before), before)
        after['collections'][0]['non_uv_collection_md5'] = 'new'
        with self.assertRaises(ValueError):
            C.assert_equal(before, after)

    def test_probe_ignores_only_explicitly_allowed_value_hashes(self):
        before, after = audit(), audit()
        change_hash(after['collections'][0], 'SimVertices3D', 'MaxDistance')
        self.assertEqual(C.probe_semantics(before, {'MaxDistance'}),
                         C.probe_semantics(after, {'MaxDistance'}))
        change_hash(after['collections'][0], 'SimVertices3D', 'ArtistPaint')
        self.assertNotEqual(C.probe_semantics(before, {'MaxDistance'}),
                            C.probe_semantics(after, {'MaxDistance'}))

    def test_probe_retains_metadata_on_a_mutable_map(self):
        before, after = audit(), audit()
        find_attribute(after['collections'][0], 'SimVertices3D', 'MaxDistance')['persistent'] = False
        self.assertNotEqual(C.probe_semantics(before, {'MaxDistance'}),
                            C.probe_semantics(after, {'MaxDistance'}))

    def test_duplicate_or_unsorted_inventory_is_rejected(self):
        for kind in ('groups-order', 'groups-duplicate', 'attribute-order', 'attribute-duplicate'):
            with self.subTest(kind=kind):
                current = audit()
                groups = current['collections'][0][C.KEY]['groups']
                attrs = groups[-1]['attributes']
                if kind == 'groups-order':
                    groups.reverse()
                elif kind == 'groups-duplicate':
                    groups.append(copy.deepcopy(groups[-1]))
                elif kind == 'attribute-order':
                    attrs.reverse()
                else:
                    attrs.append(copy.deepcopy(attrs[-1]))
                with self.assertRaises(ValueError):
                    C.assert_equal(current, current)

    def test_mixed_semantic_availability_cannot_downgrade_to_old_native_comparison(self):
        before, after = audit(), audit()
        after['collections'][0].pop(C.KEY)
        with self.assertRaises(ValueError):
            C.assert_equal(before, after)
        before['collections'].append(after['collections'][0])
        with self.assertRaises(ValueError):
            C.proof_view(before)

    def test_exposed_name_duplicates_and_incomplete_attribute_values_are_rejected(self):
        current = audit()
        current['collections'][0]['weight_maps'].append(
            copy.deepcopy(current['collections'][0]['weight_maps'][0]))
        with self.assertRaises(ValueError):
            C.proof_view(current)
        for key, value in (('complete', False), ('group_count', 99), ('count', 99),
                           ('type_id', True), ('persistent', 'True'), ('blake3', 'bad')):
            with self.subTest(key=key):
                current = audit()
                find_attribute(current['collections'][0], 'SimVertices3D', 'ArtistPaint')[key] = value
                with self.assertRaises(ValueError):
                    C.proof_view(current)


class PublicationProofTests(unittest.TestCase):
    def views(self, before, after, added=()):
        original = copy.deepcopy((before, after))
        try:
            return C.publication_view(before, before, added), C.publication_view(after, before, added)
        finally:
            self.assertEqual((before, after), original)

    def assert_allowed(self, before, after, added=()):
        left, right = self.views(before, after, added)
        self.assertEqual(left, right)

    def assert_rejected(self, before, after, added=()):
        try:
            left, right = self.views(before, after, added)
        except ValueError:
            return
        self.assertNotEqual(left, right)

    def test_property_values_and_row_count_may_change_under_the_fixed_schema(self):
        before, after = collection(), collection()
        resize_group(after, 'Property', 7)
        for attr in find_group(after, 'Property')['attributes']:
            attr['blake3'] = '2' * 64
        self.assert_allowed(before, after)

    def test_dirty_and_collider_value_hashes_may_change(self):
        before, after = collection(), collection()
        change_hash(after, 'PropertyGlobal', 'Dirty')
        change_hash(after, 'Lods', 'PhysicsAssetSoftObjectPathName')
        self.assert_allowed(before, after)

    def test_property_dirty_and_collider_metadata_are_never_masked(self):
        for group_name, name in (('Property', 'HighValue'), ('PropertyGlobal', 'Dirty'),
                                 ('Lods', 'PhysicsAssetSoftObjectPathName')):
            for key, value in (('type_id', 991), ('type_name', 'OtherType'),
                               ('dependency', 'OtherGroup'), ('persistent', False),
                               ('codec', 'other-codec')):
                with self.subTest(group=group_name, name=name, key=key):
                    before, after = collection(), collection()
                    find_attribute(after, group_name, name)[key] = value
                    after[C.KEY]['blake3'] = 'b' * 64
                    self.assert_rejected(before, after)

    def test_property_schema_must_have_exactly_the_five_reviewed_attributes(self):
        for mutation in ('add', 'remove', 'rename'):
            with self.subTest(mutation=mutation):
                before, after = collection(), collection()
                attrs = find_group(after, 'Property')['attributes']
                if mutation == 'add':
                    add_attribute(after, 'Property', 'UnknownArtistProperty')
                elif mutation == 'remove':
                    attrs.pop()
                else:
                    attrs[0]['name'] = 'OtherFlags'
                    attrs.sort(key=lambda row: row['name'])
                after[C.KEY]['blake3'] = 'b' * 64
                self.assert_rejected(before, after)

    def test_dirty_facade_requires_one_row_and_the_single_dirty_attribute(self):
        for mutation in ('count', 'add', 'rename'):
            with self.subTest(mutation=mutation):
                before, after = collection(), collection()
                if mutation == 'count':
                    resize_group(after, 'PropertyGlobal', 2)
                elif mutation == 'add':
                    add_attribute(after, 'PropertyGlobal', 'OtherDirty')
                else:
                    find_group(after, 'PropertyGlobal')['attributes'][0]['name'] = 'OtherDirty'
                after[C.KEY]['blake3'] = 'b' * 64
                self.assert_rejected(before, after)

    def test_new_named_sim_float_map_is_the_only_inventory_addition_allowed(self):
        before, after = collection(), collection()
        add_attribute(after, 'SimVertices3D', 'GeneratedTravel')
        self.assert_allowed(before, after, {'GeneratedTravel'})
        self.assert_rejected(before, after)
        self.assert_rejected(before, after, {'generatedtravel'})

    def test_generated_float_map_accepts_native_fname_none_without_weakening_its_schema(self):
        for empty_dependency in ('', 'None'):
            with self.subTest(empty_dependency=empty_dependency):
                before, after = collection(), collection()
                for row in (before, after):
                    for group in row[C.KEY]['groups']:
                        for attr in group['attributes']:
                            attr['dependency'] = empty_dependency
                add_attribute(after, 'SimVertices3D', 'GeneratedTravel')
                added = find_attribute(after, 'SimVertices3D', 'GeneratedTravel')
                added['dependency'] = empty_dependency
                self.assert_allowed(before, after, {'GeneratedTravel'})
                for invalid in ('SimVertices2D', 'OtherGroup', 'none', 'NONE', None, False, 0):
                    with self.subTest(invalid=invalid):
                        changed = copy.deepcopy(after)
                        find_attribute(changed, 'SimVertices3D', 'GeneratedTravel')['dependency'] = invalid
                        self.assert_rejected(before, changed, {'GeneratedTravel'})

    def test_added_name_does_not_hide_an_existing_artist_map(self):
        before, after = collection(), collection()
        change_hash(after, 'SimVertices3D', 'ArtistPaint')
        self.assert_rejected(before, after, {'ArtistPaint'})

    def test_added_map_must_be_a_persistent_dependency_free_float_on_the_sim_group(self):
        for key, value in (('type_name', 'double'), ('persistent', False),
                           ('dependency', 'SimVertices2D'), ('type_id', 999),
                           ('codec', 'unreviewed-codec')):
            with self.subTest(key=key):
                before, after = collection(), collection()
                add_attribute(after, 'SimVertices3D', 'GeneratedTravel')
                find_attribute(after, 'SimVertices3D', 'GeneratedTravel')[key] = value
                self.assert_rejected(before, after, {'GeneratedTravel'})
        before, after = collection(), collection()
        add_attribute(after, 'RenderVertices', 'GeneratedTravel')
        self.assert_rejected(before, after, {'GeneratedTravel'})

    def test_geometry_color_uv_paint_selection_and_proxy_values_stay_covered(self):
        for group_name, name in (('SimVertices3D', 'Position'), ('SimVertices3D', 'Color'),
                                 ('SimVertices2D', 'UV'), ('SimVertices3D', 'ArtistPaint'),
                                 ('Selection', 'KinematicVertices3D'),
                                 ('RenderVertices', 'ProxyDeformerData')):
            with self.subTest(group=group_name, name=name):
                before, after = collection(), collection()
                change_hash(after, group_name, name)
                self.assert_rejected(before, after)

    def test_exposed_geometry_and_map_statistics_cannot_be_hidden_by_equal_semantic_proof(self):
        before, after = collection(), collection()
        after['built_mapping']['sim_positions'][0][0] = 9
        self.assert_rejected(before, after)
        before, after = collection(), collection()
        after['weight_maps'][0]['avg'] = 7
        self.assert_rejected(before, after)

    def test_inventory_and_root_disagreement_is_rejected_as_stale_evidence(self):
        before, after = collection(), collection()
        after[C.KEY]['blake3'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            C.publication_view(after, before)
        before, after = collection(), collection()
        find_attribute(after, 'Property', 'HighValue')['blake3'] = '2' * 64
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            C.publication_view(after, before)

    def test_publication_requires_complete_consistent_semantic_availability(self):
        for mutation in ('incomplete', 'unsupported', 'missing-current', 'missing-baseline'):
            with self.subTest(mutation=mutation):
                before, after = collection(), collection()
                if mutation == 'incomplete':
                    after[C.KEY]['complete'] = False
                elif mutation == 'unsupported':
                    after[C.KEY]['unsupported'] = ['pointer']
                elif mutation == 'missing-current':
                    after.pop(C.KEY)
                else:
                    before.pop(C.KEY)
                with self.assertRaises(ValueError):
                    C.publication_view(after, before)

    def test_old_native_publication_view_is_strict(self):
        before, after = collection(), collection()
        before.pop(C.KEY)
        after.pop(C.KEY)
        self.assert_allowed(before, after)
        after['non_uv_collection_md5'] = 'changed'
        self.assert_rejected(before, after)

    def test_guide_publication_filters_only_new_exposed_maps_and_preserves_inputs(self):
        before, after = collection(), collection()
        add_attribute(after, 'SimVertices3D', 'GeneratedTravel')
        change_hash(after, 'Property', 'HighValue')
        after['weight_maps'].append({'name': 'GeneratedTravel', 'avg': 0.5})
        after['sim_float_attributes'].append({'name': 'GeneratedTravel'})
        after['non_uv_collection_md5'] = 'different-raw-order'
        original = copy.deepcopy((before, after))
        H._verify_published_collection({'collections': [after]}, before, {'GeneratedTravel'})
        self.assertEqual((before, after), original)
        change_hash(after, 'SimVertices3D', 'ArtistPaint')
        with self.assertRaises((RuntimeError, ValueError)):
            H._verify_published_collection({'collections': [after]}, before,
                                            {'GeneratedTravel', 'ArtistPaint'})


class PublicationMismatchDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def native_inventory_counts(row):
        proof = row[C.KEY]
        proof['group_count'] = len(proof['groups'])
        proof['attribute_count'] = sum(len(group['attributes']) for group in proof['groups'])
        for group in proof['groups']:
            group['attribute_count'] = len(group['attributes'])
            for attr in group['attributes']:
                attr['serialized_bytes'] = 4 * attr['count']

    def response_pair(self):
        before = collection()
        self.native_inventory_counts(before)
        after = copy.deepcopy(before)
        add_attribute(after, 'SimVertices3D', 'HairTipResponse')
        after['weight_maps'].append({'name': 'HairTipResponse', 'avg': 0.2})
        after['sim_float_attributes'].append({'name': 'HairTipResponse'})
        after['non_uv_collection_md5'] = 'with-native-R'
        self.native_inventory_counts(after)
        return before, after

    def failure_details(self, before, after):
        original = copy.deepcopy((before, after))
        with self.assertRaises(RuntimeError) as error:
            H._verify_published_collection(audit(after), before, {'HairTipResponse'})
        self.assertEqual((before, after), original)
        text = str(error.exception)
        self.assertIn('collection_semantic_v1', text)
        self.assertNotIn('sim_positions', text)
        return json.loads(text.split('; semantic_delta=', 1)[1])

    def test_native_R_inventory_addition_alone_already_passes_without_relaxation(self):
        before, after = self.response_pair()
        original = copy.deepcopy((before, after))
        H._verify_published_collection(audit(after), before, {'HairTipResponse'})
        self.assertEqual((before, after), original)

    def test_failure_names_only_the_normalized_changed_leaf_and_opaque_hashes(self):
        before, after = self.response_pair()
        change_hash(after, 'SimVertices3D', 'ArtistPaint')
        details = self.failure_details(before, after)
        self.assertEqual(details['changed_entries'], 1)
        self.assertEqual(details['changes'], [{'path': 'SimVertices3D.ArtistPaint',
            'expected_hash': '1' * 64, 'actual_hash': '2' * 64}])
        self.assertNotIn('HairTipResponse', json.dumps(details))
        self.assertFalse(details['truncated'])

    def test_metadata_difference_is_reported_and_still_rejected(self):
        before, after = self.response_pair()
        find_attribute(after, 'SimVertices3D', 'ArtistPaint')['persistent'] = False
        details = self.failure_details(before, after)
        leaf = details['changes'][0]
        self.assertEqual(leaf['path'], 'SimVertices3D.ArtistPaint')
        self.assertEqual(leaf['metadata'], {'persistent': {'expected': True, 'actual': False}})
        self.assertEqual(leaf['expected_hash'], leaf['actual_hash'])

    def test_many_changed_leaves_keep_the_failure_diagnostic_small(self):
        before, after = self.response_pair()
        for index in range(40):
            name = 'Paint_%02d' % index
            for row in (before, after):
                add_attribute(row, 'SimVertices3D', name)
                row['weight_maps'].append({'name': name, 'avg': 0.4})
                row['sim_float_attributes'].append({'name': name})
            find_attribute(after, 'SimVertices3D', name)['blake3'] = '2' * 64
        for row in (before, after):
            self.native_inventory_counts(row)
        after[C.KEY]['blake3'] = 'c' * 64
        details = self.failure_details(before, after)
        self.assertEqual(details['changed_entries'], 40)
        self.assertLessEqual(details['shown_entries'], 8)
        self.assertTrue(details['truncated'])
        self.assertLessEqual(len(json.dumps(details, ensure_ascii=False, separators=(',', ':'))), 3072)


if __name__ == '__main__':
    unittest.main()
