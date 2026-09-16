"""Source ownership and optional-cloth policy, independent of Blender rendering."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'src/addons/send2ue/core/hair_guide_cloth.py'


class Object:
    def __init__(self, name, parent=None, kind='MESH'):
        self.name, self.parent, self.type = name, parent, kind
        self.children, self.modifiers = [], []
        if parent:
            parent.children.append(self)


class GuidePolicyTests(unittest.TestCase):
    def setUp(self):
        self.bpy = SimpleNamespace(data=SimpleNamespace(objects={}),
                                  app=SimpleNamespace(driver_namespace={}),
                                  context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)))
        with patch.dict(sys.modules, {'bpy': self.bpy, 'mathutils': SimpleNamespace(Matrix=None)}):
            spec = importlib.util.spec_from_file_location('producer_fixture.hair_guide_cloth', SOURCE)
            self.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.api)

    def source_input(self, source):
        return (SimpleNamespace(name='Setup', node_group=SimpleNamespace(name='Hair_System_Setup')),
                SimpleNamespace(name='Source Surface', identifier='Input_3'), source)

    def test_two_stage_uses_first_generator(self):
        first, second = Object('first'), Object('second')
        self.api._object_inputs = lambda obj: iter([self.source_input(first)])
        guide, sockets, receipt = self.api.find_guide(second)
        self.assertIs(guide, first)
        self.assertEqual(sockets, [('Setup', 'Input_3')])
        self.assertEqual(receipt['status'], 'resolved')

    def test_three_stage_uses_immediate_second_not_ancestor(self):
        first = Object('first')
        second, third = Object('second', first), Object('third', first)
        self.api._object_inputs = lambda obj: iter([self.source_input(second)])
        guide, _, _ = self.api.find_guide(third)
        self.assertIs(guide, second)

    def test_missing_weight_is_static_and_distinct_from_authored_zero(self):
        mesh = SimpleNamespace(vertices=[None] * 3, attributes={})
        values, metadata = self.api.own_weights(mesh)
        self.assertEqual(values, [0., 0., 0.])
        self.assertFalse(metadata['authored_weight_present'])
        self.assertEqual(metadata['weight_source'], 'absent_intentionally_static')
        mesh.attributes['ChaosWeight'] = SimpleNamespace(domain='POINT', data_type='FLOAT_COLOR',
            data=[SimpleNamespace(color=(0, 0, 0, 1)) for _ in mesh.vertices])
        values, metadata = self.api.own_weights(mesh)
        self.assertEqual(values, [0., 0., 0.])
        self.assertTrue(metadata['authored_weight_present'])

    def test_only_absent_guide_gets_exhausted_search_receipt(self):
        render = Object('render', Object('export', kind='EMPTY'))
        self.api._object_inputs = lambda obj: iter([])
        guide, _, receipt = self.api.find_guide(render)
        self.assertIsNone(guide)
        self.assertEqual(receipt['status'], 'no_guide_after_search')
        self.assertEqual(len(receipt['search_stages']), 3)

    def test_absent_guide_keeps_complete_ordinary_render_and_never_reads_render_g(self):
        render = Object('render', Object('export', kind='EMPTY'))
        objects = [SimpleNamespace(data=SimpleNamespace(vertices=[None] * count)) for count in (3, 6)]
        hair = SimpleNamespace(_evaluated_mesh_objects=Mock(return_value=objects))
        package = SimpleNamespace(hair_tool_export=hair)
        stamps = []
        self.api._int_attribute = lambda mesh, name, values: stamps.append((mesh, name, values))
        self.api.own_weights = Mock(side_effect=AssertionError('Render G is never simulation G'))
        self.api._object_inputs = lambda obj: iter([])
        export_state, ao_settings = {}, {'distance': .25}
        with patch.dict(sys.modules, {'producer_fixture': package}):
            result, packet = self.api.capture_source(render, export_state, True, ao_settings)
        self.assertIs(result, objects)
        hair._evaluated_mesh_objects.assert_called_once_with(render, export_state,
            include_system_ao=True, ao_settings=ao_settings)
        self.assertEqual([row[2] for row in stamps], [[-1] * 3, [-1] * 6])
        for key in ('vertices', 'triangles', 'guide_ids', 'weights', 'authored_weights', 'parts'):
            self.assertEqual(packet[key], [])
        self.assertFalse(packet['simulation_enabled'])
        self.assertEqual(packet['render_vertex_count'], 9)
        self.assertEqual(packet['source']['status'], 'no_guide_after_search')
        self.assertEqual(len(packet['source']['search_stages']), 3)
        self.assertFalse(self.api.state()['diagnostics'])

    def test_render_only_record_does_not_require_or_emit_sim_fbx(self):
        package = {'packet': {'simulation_enabled': False}, 'manifest_path': 'fixture.json',
                   'manifest_sha256': 'abc', 'exports': {'render':
                   {'asset_path': '/Game/Render', 'fbx': {'path': 'render.fbx', 'sha256': 'def'}}}}
        record = self.api._package_record(package, 'key', None)
        self.assertEqual(record['version'], 2)
        self.assertFalse(record['simulation_enabled'])
        self.assertEqual(record['render_asset_path'], '/Game/Render')
        self.assertNotIn('sim_asset_path', record)
        self.assertNotIn('sim_fbx', record)

    def test_whole_static_guide_uses_ordinary_render_before_ownership_validation(self):
        for raw in (None, [0., 0., 0.], [-.1, 0., 0.]):
            with self.subTest(raw=raw):
                guide, source = Mock(), Mock()
                guide.name, source.name = 'ActualGuide', 'Render'
                gm = SimpleNamespace(vertices=[None] * 3, attributes={}, users=0)
                if raw is not None:
                    gm.attributes['ChaosWeight'] = SimpleNamespace(domain='POINT', data_type='FLOAT',
                        data=[SimpleNamespace(value=w) for w in raw])
                self.bpy.context.scene = SimpleNamespace(collection=SimpleNamespace(objects=Mock()))
                self.bpy.data.objects = Mock()
                self.bpy.data.meshes = Mock()
                search = {'status': 'resolved', 'guide': guide.name, 'search_stages': ['all stages']}
                self.api.find_guide = Mock(return_value=(guide, [], search))
                self.api._stamp_group = Mock(return_value=SimpleNamespace(users=1))
                self.api._copy_mesh = Mock(return_value=gm)
                render_only = self.api._capture_render_only = Mock(return_value='ordinary render')
                with patch.dict(sys.modules, {'producer_fixture': SimpleNamespace(hair_tool_export=Mock())}):
                    self.assertEqual(self.api.capture_source(source, {}), 'ordinary render')
                self.api._copy_mesh.assert_called_once_with(guide.copy.return_value)
                receipt = render_only.call_args.args[2]
                self.assertEqual(receipt['status'], 'guide_weights_all_zero')
                self.assertEqual(receipt['guide'], 'ActualGuide')
                self.assertEqual(receipt['guide_search_status'], 'resolved')
                self.assertEqual(receipt['excluded_sim_vertices'], 3)
                self.assertEqual(receipt['authored_weight_present'], raw is not None)
                self.assertFalse(self.api.state()['diagnostics'])

    def test_mixed_record_keeps_guided_sim_and_full_render_exports(self):
        package = {'packet': {'simulation_enabled': True}, 'manifest_path': 'fixture.json',
                   'manifest_sha256': 'abc', 'exports': {
                   role: {'asset_path': '/Game/' + role, 'fbx': {'path': role + '.fbx'}}
                   for role in ('sim', 'render')}}
        record = self.api._package_record(package, 'key', None)
        self.assertTrue(record['simulation_enabled'])
        self.assertEqual(record['sim_asset_path'], '/Game/sim')
        self.assertEqual(record['render_asset_path'], '/Game/render')

    def test_unresolved_mesh_parent_prevents_render_fallback(self):
        render = Object('render', Object('possible_guide'))
        self.api._object_inputs = lambda obj: iter([])
        guide, _, receipt = self.api.find_guide(render)
        self.assertIsNone(guide)
        self.assertEqual(receipt['status'], 'unresolved')

    def test_conflicting_generators_do_not_guess(self):
        render = Object('render')
        a, b = Object('a'), Object('b')
        self.api._object_inputs = lambda obj: iter([self.source_input(a), self.source_input(b)])
        self.assertEqual(self.api.find_guide(render)[2]['status'], 'unresolved')

    def test_registry_source_prevents_lazy_fallback(self):
        render = Object('render')
        render.ht_props = SimpleNamespace(hair_nodes=SimpleNamespace(hair_base_mesh='registry_guide', hair_systems=[]))
        self.bpy.data.objects['registry_guide'] = Object('registry_guide')
        self.api._object_inputs = lambda obj: iter([])
        self.assertEqual(self.api.find_guide(render)[2]['status'], 'unresolved')

    def test_clamp_does_not_mutate_artist_data(self):
        raw = [SimpleNamespace(color=(w, w, w, 1)) for w in (-.003, .37, 1.002)]
        mesh = SimpleNamespace(vertices=[None] * 3, attributes={'ChaosWeight':
            SimpleNamespace(domain='POINT', data_type='FLOAT_COLOR', data=raw)})
        values, metadata = self.api.own_weights(mesh)
        self.assertEqual(values, [0, .37, 1])
        self.assertEqual(raw[0].color[1], -.003)
        self.assertEqual(metadata['clamped_values'], 2)

    def test_portable_post_import_record_survives_cleanup(self):
        record = {'version': 1, 'manifest_path': 'fixture.json'}
        self.assertIs(self.api.import_record({'_hair_guide_cloth_record': record}, None), record)
        self.assertIsNone(self.api.import_record({'_hair_guide_cloth_record': record, 'skip': True}, None))


if __name__ == '__main__':
    unittest.main()
