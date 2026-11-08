"""A simulation-only R mask must preserve G, source identity and old exports.

These are handoff contract checks, not proof of a native Chaos simulation.
"""
import copy
import importlib.util
import json
import hashlib
import struct
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


REPO = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UE = load_module('tip_response_receiver',
    'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py')


def contract():
    return dict(version=1, policy='sim_only_R', channel='R',
                attribute='HairPixelDepthOffset', weight_map='HairTipResponse',
                precision='unorm8', render_channel_fallback=1.0)


def packet_and_built():
    mesh = dict(vertices=[[0, 0, 0], [.01, 0, 0], [0, .01, 0]],
                triangles=[[0, 1, 2]], guide_ids=[7, 7, 7], weights=[0, .4, 1])
    packet = {'meshes': {'sim': copy.deepcopy(mesh), 'render': copy.deepcopy(mesh)}}
    packet['response_mask'] = dict(contract(), present=True,
        values=[0, .4, 1], authored_values=[0, .4, 1])
    built = {role + '_positions': [[0, 0, 0], [1, 0, 0], [0, -1, 0]]
             for role in ('sim', 'render')}
    built.update({role + '_triangles': [[0, 1, 2]] for role in ('sim', 'render')})
    built['sim_weights'] = [0, .4, 1]
    return packet, built


class ResponseManifestTests(unittest.TestCase):
    def test_old_packet_remains_opted_out_and_unchanged(self):
        packet, built = packet_and_built()
        del packet['response_mask']
        before = copy.deepcopy((packet, built))
        self.assertIsNone(UE.validate_response_mask(packet))
        result = UE.normalize_ownership(built, packet)
        self.assertNotIn('sim_response_mask', result)
        self.assertEqual(result['sim_weights'], [0, .4, 1])
        self.assertEqual((packet, built), before)

    def test_mask_does_not_replace_g_or_modify_geometry(self):
        packet, built = packet_and_built()
        packet['response_mask']['values'] = [1, .4, 0]
        packet['response_mask']['authored_values'] = [1, .4, 0]
        before = copy.deepcopy((packet, built))
        result = UE.normalize_ownership(built, packet)
        self.assertEqual(result['sim_response_mask']['values'], [1, .4, 0])
        self.assertEqual(result['sim_weights'], [0, .4, 1])
        for role in ('sim', 'render'):
            self.assertEqual(result[role + '_positions'], built[role + '_positions'])
            self.assertEqual(result[role + '_triangles'], built[role + '_triangles'])
        self.assertEqual((packet, built), before)

    def test_import_reordering_and_splits_use_exact_source_identity(self):
        packet, built = packet_and_built()
        built['sim_positions'] = [built['sim_positions'][i] for i in (2, 0, 1, 2)]
        built['sim_weights'] = [1, 0, .4, 1]
        built['sim_triangles'] = [[1, 2, 0]]
        result = UE.normalize_ownership(built, packet)
        self.assertEqual(result['sim_source_indices'], [2, 0, 1, 2])
        self.assertEqual(result['sim_response_mask']['values'], [1, 0, .4, 1])
        self.assertEqual(result['sim_response_mask']['authored_values'], [1, 0, .4, 1])
        self.assertEqual(result['sim_weights'], [1, 0, .4, 1])

    def test_coincident_same_guide_with_conflicting_r_is_rejected(self):
        packet, built = packet_and_built()
        sim = packet['meshes']['sim']
        sim['vertices'].append([0, 0, 0])
        sim['guide_ids'].append(7)
        sim['weights'].append(0)
        packet['response_mask']['values'].append(1)
        packet['response_mask']['authored_values'].append(1)
        with self.assertRaises(ValueError):
            UE.normalize_ownership(built, packet)

    def test_nonfinite_out_of_range_and_malformed_lengths_fail_closed(self):
        for field, value in (('values', [float('nan'), .4, 1]),
                             ('values', [float('inf'), .4, 1]),
                             ('values', [-.1, .4, 1]),
                             ('authored_values', [0, .4, 1.1]),
                             ('values', [0, .4]),
                             ('authored_values', [0, .4])):
            with self.subTest(field=field, value=value):
                packet, _ = packet_and_built()
                packet['response_mask'][field] = value
                with self.assertRaises(ValueError):
                    UE.validate_response_mask(packet)

    def test_quantized_values_must_match_authored_r(self):
        packet, _ = packet_and_built()
        packet['response_mask']['authored_values'][1] = .5
        packet['response_mask']['values'][1] = 128 / 255
        self.assertEqual(UE.validate_response_mask(packet)['values'][1], 128 / 255)
        packet['response_mask']['values'][1] = .5
        with self.assertRaises(ValueError):
            UE.validate_response_mask(packet)

    def test_unrecognized_semantics_cannot_silently_repurpose_another_channel(self):
        mutations = dict(version=2, policy='all_R', channel='B',
                         attribute='ChaosWeight', weight_map='MaxDistance',
                         precision='float16', present=False,
                         render_channel_fallback=0.0)
        for field, value in mutations.items():
            with self.subTest(field=field):
                packet, _ = packet_and_built()
                packet['response_mask'][field] = value
                with self.assertRaises(ValueError):
                    UE.validate_response_mask(packet)

    def test_real_render_payload_keeps_r_neutral_after_simulation_only_export(self):
        packet, _ = packet_and_built()
        colors = [[1, 0, .2, 1], [1, .4, .6, 1], [1, 1, .9, 1]]
        packet['meshes']['render']['rgba_srgb'] = colors
        before = copy.deepcopy(packet)
        UE.validate_response_mask(packet)
        self.assertEqual(packet, before)
        packet['meshes']['render']['rgba_srgb'][1][0] = .4
        with self.assertRaises(ValueError):
            UE.validate_response_mask(packet)


class NativeResponseHarness:
    """Model the imported-R boundary; preserve existing physics as opaque data."""
    def __init__(self, packet, built, native_values):
        self.packet = packet
        self.audit = {'collections': [{'built_mapping': copy.deepcopy(built), 'weight_maps': []}]}
        self.updated = copy.deepcopy(self.audit)
        self.updated['collections'][0]['weight_maps'].append({'name': 'HairTipResponse'})
        self.native_values = native_values
        self.calls = []
        self.physics = {'Mass': {'DensityWeighted': {'Low': .12, 'High': .12}},
                        'Damping': {'DampingCoefficientWeighted': {'Low': .04, 'High': .04}}}
        self.nodes = {'GuideOwnG': object(), 'Mass': object()}
        self.graph = SimpleNamespace(get_path_name=lambda: '/Game/GeneratedGraph')
        self.asset = SimpleNamespace(get_path_name=lambda: '/Game/GeneratedSource')
        self.receipt = dict(apply_changes=True, color_channel='Red',
            source_kind='SkeletalMeshImportedModel', active_snapshot_deactivated=True,
            unmatched_sim_vertices=0, sim_vertices_3d=len(native_values))
        self.save = Mock()
        self.bake = Mock(side_effect=lambda *args: json.dumps(self.receipt))
        self.collection_read = Mock(side_effect=lambda path: json.dumps(self.updated))
        self.unreal = SimpleNamespace(
            EditorAssetLibrary=SimpleNamespace(save_loaded_asset=self.save),
            CodexGarmentWindAuditLibrary=SimpleNamespace(audit_asset=self.physics_audit),
            CodexClothToolsLibrary=SimpleNamespace(
                bake_vertex_color_weight_map=self.bake,
                dump_dataflow_nodes=lambda path: json.dumps({'nodes': [dict(
                    name=UE.RESPONSE_NODE_NAME, type='FChaosClothAssetWeightMapNode',
                    props={'VertexWeights': self.native_values})]}),
                dump_cloth_collection_colors=self.collection_read))

    def physics_audit(self, asset):
        raw = struct.pack('<i', len(self.native_values)) + b''.join(struct.pack('<f', v) for v in self.native_values)
        entry = dict(count=len(self.native_values), non_finite_count=0, sha256=hashlib.sha256(raw).hexdigest())
        row = dict(weight_maps={'HairTipResponse': entry})
        return json.dumps(dict(success=True, models=[dict(lods=[dict(row, built_simulation=row)])]))

    def call(self, operation, *args):
        self.calls.append((operation, args))
        if operation == 'AddNode':
            self.nodes[args[2]] = object()
            return self.nodes[args[2]]
        if operation in ('DisconnectNodePins', 'ConnectNodePins'):
            return True
        raise AssertionError('Unexpected write to existing graph or physics: ' + operation)

    def install(self, destination='Collection', destination_type=None, extra_destinations=()):
        structure = {'connections': [dict(fromNode='GuideOwnG', fromPin='Collection',
                                         toNode='Mass', toPin=destination)]}
        structure['connections'].extend(dict(fromNode='GuideOwnG', fromPin='Collection',
            toNode='Mass', toPin=pin) for pin in extra_destinations)
        infos = {'Mass': {'type': destination_type}} if destination_type else {}
        graph = (self.graph, self.call, structure, self.nodes, infos)
        with patch.object(UE, '_graph', return_value=graph), \
                patch.object(UE, '_asset_weight_map_names', return_value={'GuideOwnG': 'MaxDistance'}):
            return UE._install_response_map(self.unreal, self.asset,
                {'sim_asset_path': '/Game/ImportedSim'}, self.packet, self.audit)


class ImportedResponseBoundaryTests(unittest.TestCase):
    def test_graph_text_rounding_does_not_replace_native_float32_evidence(self):
        packet, built = packet_and_built()
        packet['response_mask']['authored_values'][1] = .5
        packet['response_mask']['values'][1] = 128/255
        exact = [0, 128/255, 1]
        harness = NativeResponseHarness(packet, built, exact)
        harness.unreal.CodexClothToolsLibrary.dump_dataflow_nodes = lambda _: json.dumps({'nodes': [dict(
            name=UE.RESPONSE_NODE_NAME, type='FChaosClothAssetWeightMapNode',
            props={'VertexWeights': '(0,0.501961,1)'})]})
        evidence = harness.install()['response_mask_import']
        self.assertEqual(evidence['expected_float32_sha256'], evidence['imported_float32_sha256'])
        self.assertGreater(evidence['graph_text_maximum_rounding_error'], 0)
        self.assertEqual(evidence['maximum_error'], 0)

    def test_actual_native_array_mismatch_is_rejected_even_when_text_matches(self):
        packet, built = packet_and_built()
        harness = NativeResponseHarness(packet, built, [0, .4, 1])
        harness.unreal.CodexGarmentWindAuditLibrary.audit_asset = lambda _: json.dumps(dict(
            models=[dict(lods=[dict(weight_maps={'HairTipResponse': dict(count=3, non_finite_count=0, sha256='wrong')})])]))
        with self.assertRaisesRegex(RuntimeError, 'float32 array differs'):
            harness.install()
        harness.save.assert_not_called()

    def test_all_contiguous_lod_pins_on_one_terminal_receive_R_without_a_G_bypass(self):
        packet, built = packet_and_built()
        harness = NativeResponseHarness(packet, built, [0, .4, 1])
        result = harness.install('CollectionLods[0]', 'FChaosClothAssetTerminalNode_v2',
            ('CollectionLods[1]', 'CollectionLods[2]'))
        self.assertTrue(result['response_mask_import']['full_values_verified'])
        links = [args for op, args in harness.calls if op == 'ConnectNodePins']
        self.assertEqual([row[-1] for row in links], ['Collection', 'CollectionLods[0]',
            'CollectionLods[1]', 'CollectionLods[2]'])
        self.assertEqual(sum(op=='DisconnectNodePins' for op, _ in harness.calls), 3)
        harness.bake.assert_called_once()

    def test_immutable_import_routes_R_to_the_exact_single_lod_terminal_pin(self):
        packet, built = packet_and_built()
        harness = NativeResponseHarness(packet, built, [0, .4, 1])
        result = harness.install('CollectionLods[0]', 'FChaosClothAssetTerminalNode_v2')
        self.assertTrue(result['response_mask_import']['full_values_verified'])
        links = [args for op, args in harness.calls if op == 'ConnectNodePins']
        self.assertEqual(links[-1][-1], 'CollectionLods[0]')
        harness.save.assert_not_called()

    def test_extra_lod_and_nonterminal_pins_are_rejected_before_native_R_bake(self):
        for pin, node_type in [('CollectionLods[1]', 'FChaosClothAssetTerminalNode_v2'),
                               ('CollectionLods[0]', 'UnrelatedNode')]:
            packet, built = packet_and_built()
            harness = NativeResponseHarness(packet, built, [0, .4, 1])
            with self.subTest(pin=pin, node_type=node_type), self.assertRaises(ValueError):
                harness.install(pin, node_type)
            harness.bake.assert_not_called()
            harness.save.assert_not_called()

    def test_actual_imported_r_full_array_matches_reordered_source_and_keeps_physics(self):
        packet, built = packet_and_built()
        built['sim_positions'] = [built['sim_positions'][i] for i in (2, 0, 1, 2)]
        built['sim_weights'] = [1, 0, .4, 1]
        built['sim_triangles'] = [[1, 2, 0]]
        harness = NativeResponseHarness(packet, built, [1, 0, .4, 1])
        before = copy.deepcopy((harness.physics, harness.audit, packet))
        result = harness.install()
        evidence = result['response_mask_import']
        self.assertTrue(evidence['full_values_verified'])
        self.assertEqual(evidence['vertex_count'], 4)
        self.assertEqual(evidence['expected_float32_sha256'], evidence['imported_float32_sha256'])
        self.assertEqual((harness.physics, harness.audit, packet), before)
        harness.bake.assert_called_once_with('/Game/GeneratedSource', '/Game/ImportedSim',
            UE.RESPONSE_NODE_NAME, 'Red', 1.0, False, True)
        add = next(args for op, args in harness.calls if op == 'AddNode')
        self.assertEqual(add[1], 'FChaosClothAssetWeightMapNode')
        payload = json.loads(add[3])
        self.assertEqual(payload['OutputName'], {'StringValue': 'HairTipResponse'})
        self.assertFalse(any(op == 'UpdateNode' for op, _ in harness.calls))
        harness.save.assert_not_called()

    def test_wrong_imported_r_is_rejected_before_collection_publication_or_save(self):
        packet, built = packet_and_built()
        harness = NativeResponseHarness(packet, built, [0, 0, 1])
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            harness.install()
        harness.collection_read.assert_not_called()
        harness.save.assert_not_called()

    def test_nonfinite_native_r_never_becomes_verified_evidence(self):
        for bad in (float('nan'), float('inf')):
            packet, built = packet_and_built()
            harness = NativeResponseHarness(packet, built, [0, bad, 1])
            with self.subTest(value=bad), self.assertRaises((ValueError, RuntimeError)):
                harness.install()
            harness.collection_read.assert_not_called()
            harness.save.assert_not_called()

    def test_incomplete_native_import_receipt_is_not_success(self):
        packet, built = packet_and_built()
        harness = NativeResponseHarness(packet, built, [0, .4, 1])
        harness.receipt['unmatched_sim_vertices'] = 1
        with self.assertRaisesRegex(RuntimeError, 'complete verified write'):
            harness.install()
        harness.collection_read.assert_not_called()
        harness.save.assert_not_called()

    def test_native_full_value_text_and_structural_evidence(self):
        row = dict(name=UE.RESPONSE_NODE_NAME, type='FChaosClothAssetWeightMapNode',
                   props={'VertexWeights': '(0,0.4,1)'})
        self.assertEqual(UE._native_weight_values({'nodes': [row]}, UE.RESPONSE_NODE_NAME), [0, .4, 1])
        for nodes in ([], [row, row], [dict(row, type='SomeOtherNode')],
                      [dict(row, props={'VertexWeights': object()})],
                      [dict(row, props={'VertexWeights': '(0,garbage,1)'})],
                      [dict(row, props={'VertexWeights': [0, 'garbage', 1]})],
                      [dict(row, props={'VertexWeights': [0, float('nan'), 1]})]):
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                UE._native_weight_values({'nodes': nodes}, UE.RESPONSE_NODE_NAME)


class ProducerMaskTests(unittest.TestCase):
    def setUp(self):
        bpy = SimpleNamespace(data=SimpleNamespace(objects={}),
                              app=SimpleNamespace(driver_namespace={}),
                              context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)))
        with patch.dict(sys.modules, {'bpy': bpy, 'mathutils': SimpleNamespace(Matrix=None)}):
            self.core = load_module('tip_mask_fixture.hair_guide_cloth',
                'src/addons/send2ue/core/hair_guide_cloth.py')

    def mesh(self, values, kind='FLOAT'):
        items = ([SimpleNamespace(value=value) for value in values] if kind == 'FLOAT'
                 else [SimpleNamespace(color=(value, 1 - value, .25, 1)) for value in values])
        return SimpleNamespace(vertices=[None] * len(values), attributes={
            'HairPixelDepthOffset': SimpleNamespace(domain='POINT', data_type=kind, data=items),
            'ChaosWeight': SimpleNamespace(domain='POINT', data_type='FLOAT',
                data=[SimpleNamespace(value=v) for v in (0, .6, 1)])})

    def test_response_only_skips_exact_render_but_retains_its_post_import_record(self):
        record = dict(update_mode='response_only', render_asset_path='/Game/Render')
        data = dict(asset_path='/Game/Render', _hair_guide_cloth={'role': 'render'},
                    _hair_guide_cloth_record=record)
        self.core.prepare_response_only_import(data)
        self.core.prepare_response_only_import(data)
        self.assertTrue(data['skip'])
        self.assertIs(self.core.import_record(data, None), record)
        ordinary_skip = dict(skip=True, _hair_guide_cloth_record=record)
        self.assertIsNone(self.core.import_record(ordinary_skip, None))

    def test_other_units_and_ordinary_imports_cannot_be_skipped_by_response_mode(self):
        for data in ({'asset_path': '/Game/Other'},
                     {'_hair_guide_cloth_record': {'render_asset_path': '/Game/Render'}}):
            old = copy.deepcopy(data)
            self.core.prepare_response_only_import(data)
            self.assertEqual(data, old)
        for role, path, skip in [('sim','/Game/Render',False), ('render','/Game/Other',False),
                                  ('render','/Game/Render',True)]:
            data = dict(skip=skip, asset_path=path, _hair_guide_cloth={'role':role},
                _hair_guide_cloth_record=dict(update_mode='response_only',render_asset_path='/Game/Render'))
            with self.subTest(role=role,path=path,skip=skip), self.assertRaises(ValueError):
                self.core.prepare_response_only_import(data)

    def test_response_mode_requires_explicit_unit_and_authored_simulation_mask(self):
        packet, _ = packet_and_built()
        packet['simulation_enabled'] = True
        package = dict(packet=packet, asset_name='Back', manifest_path='manifest.json',
            manifest_sha256='hash', exports={role:dict(asset_path='/Game/'+role,fbx={}) for role in ('sim','render')})
        normal = self.core._package_record(package,'id',None)
        self.assertNotIn('update_mode',normal)
        self.assertNotIn('update_mode',self.core._package_record(package,'id',None,['Other']))
        self.assertEqual(self.core._package_record(package,'id',None,['Back'])['update_mode'],'response_only')
        del packet['response_mask']
        with self.assertRaises(ValueError):self.core._package_record(package,'id',None,['Back'])

    def test_only_the_exact_root_policy_enables_response_mask(self):
        root = {'send2ue_cloth_response_mask': 'sim_only_R'}
        before = dict(root)
        self.assertEqual(self.core.response_mask_contract(root), contract())
        self.assertEqual(root, before)

    def test_added_guides_can_update_simulation_without_reimporting_render(self):
        packet, _ = packet_and_built()
        packet['simulation_enabled'] = True
        del packet['response_mask']
        package = dict(packet=packet, asset_name='Back', manifest_path='manifest.json',
            manifest_sha256='hash', exports={role:dict(asset_path='/Game/'+role,fbx={}) for role in ('sim','render')})
        record = self.core._package_record(package,'id',None,[],['Back'])
        self.assertEqual(record['update_mode'],'simulation_only')
        data = dict(asset_path='/Game/render',_hair_guide_cloth={'role':'render'},_hair_guide_cloth_record=record)
        self.core.prepare_response_only_import(data)
        self.assertTrue(data['skip'])
        self.assertIs(self.core.import_record(data,None),record)
        self.assertNotIn('update_mode',self.core._package_record(package,'id',None,[],['Other']))
        with self.assertRaises(ValueError):
            self.core._package_record(package,'id',None,[],True)

    def test_absent_root_policy_keeps_old_exports_opted_out(self):
        root = {'unrelated_property': 'preserve'}
        self.assertIsNone(self.core.response_mask_contract(root))
        self.assertEqual(root, {'unrelated_property': 'preserve'})

    def test_invalid_root_policy_is_rejected_without_guessing(self):
        for value in ('sim_only_B', 'all_R', True, 1, {}, []):
            root = {'send2ue_cloth_response_mask': value}
            before = copy.deepcopy(root)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.core.response_mask_contract(root)
            self.assertEqual(root, before)

    def test_opt_out_does_not_read_or_rewrite_source_channels(self):
        mesh = self.mesh([0, .5, 1])
        before = copy.deepcopy(mesh)
        self.assertEqual(self.core.own_response_mask(mesh, None), (None, None))
        self.assertEqual(mesh, before)

    def test_own_mask_reads_authored_r_without_borrowing_g(self):
        for kind in ('FLOAT', 'FLOAT_COLOR'):
            with self.subTest(kind=kind):
                mesh = self.mesh([1, .2, 0], kind)
                before = copy.deepcopy(mesh)
                values, metadata = self.core.own_response_mask(mesh, contract())
                self.assertEqual(values, [1, .2, 0])
                self.assertIsInstance(metadata, dict)
                self.assertEqual(mesh, before)

    def test_opt_in_without_authored_mask_cannot_use_neutral_pdo_as_tip_one(self):
        mesh = self.mesh([0, .5, 1])
        del mesh.attributes['HairPixelDepthOffset']
        with self.assertRaises(ValueError):
            self.core.own_response_mask(mesh, contract())

    def test_nonfinite_and_nonpoint_authoring_are_rejected(self):
        nonpoint = self.mesh([0, .5, 1])
        nonpoint.attributes['HairPixelDepthOffset'].domain = 'CORNER'
        for mesh in (self.mesh([0, float('nan'), 1]), nonpoint):
            with self.subTest(mesh=mesh), self.assertRaises(ValueError):
                self.core.own_response_mask(mesh, contract())


class ResponseOnlyGraphTests(unittest.TestCase):
    def graph(self):
        nodes=[dict(name='GeneratorBindings',guid='stable-input'),dict(name='Hair_Mass',guid='stable-mass')]
        return dict(nodes=nodes, native_nodes=copy.deepcopy(nodes),infos={n['name']:dict(n) for n in nodes},
            variables=[dict(name='artist',value='keep')],connections=[dict(fromNode='GeneratorBindings',fromPin='Collection',
                toNode='Hair_Mass',toPin='Collection',fromPinGuid='old-output',toPinGuid='old-input')])

    def with_response(self, before):
        after=copy.deepcopy(before);node=dict(name=UE.RESPONSE_NODE_NAME,guid='new-R')
        after['nodes'].append(node);after['native_nodes'].append(dict(node));after['infos'][node['name']]=dict(node)
        after['connections']=[dict(fromNode='GeneratorBindings',fromPin='Collection',toNode=node['name'],toPin='Collection'),
            dict(fromNode=node['name'],fromPin='Collection',toNode='Hair_Mass',toPin='Collection')]
        return after

    def test_only_generated_R_node_and_its_insertion_are_permitted(self):
        before=self.graph();after=self.with_response(before)
        UE._response_graph_unchanged(before,after,'GeneratorBindings',False)
        for key in ('nodes','native_nodes','infos','variables'):
            changed=copy.deepcopy(after)
            if key=='infos':changed[key]['Hair_Mass']['guid']='changed'
            else:changed[key][0]['guid' if key!='variables' else 'value']='changed'
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                UE._response_graph_unchanged(before,changed,'GeneratorBindings',False)

    def test_extra_connection_or_missing_R_route_cannot_pass(self):
        before=self.graph()
        for mode in ('duplicate','missing'):
            after=self.with_response(before)
            if mode=='duplicate':after['connections'].append(dict(after['connections'][0]))
            else:after['connections'].pop()
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):
                UE._response_graph_unchanged(before,after,'GeneratorBindings',False)


if __name__ == '__main__':
    unittest.main()
