import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'bevel_modifier_export', ROOT / 'src/addons/send2ue/core/bevel_modifier_export.py')
bevel_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bevel_export)


def mesh(name='Mesh', *, render=True, viewport=False, kind='BEVEL', library=None):
    return NS(type='MESH', name=name, library=library,
              modifiers=[NS(type=kind, show_render=render, show_viewport=viewport)])


class BevelExportTests(unittest.TestCase):
    def test_existing_scene_and_skeletal_export_are_unchanged(self):
        self.assertFalse(bevel_export.requested_for_export(NS(), is_static_mesh=True))
        self.assertFalse(bevel_export.requested_for_export(
            NS(export_render_bevels=True), is_static_mesh=False))
        obj = mesh()
        with bevel_export.enabled_for_export([obj]):
            self.assertFalse(obj.modifiers[0].show_viewport)

    def test_both_fbx_modifier_options_are_required(self):
        for viewport, render in ((False, False), (False, True), (True, False), (True, True)):
            props = NS(export_render_bevels=True, blender=NS(export_method=NS(fbx=NS(
                geometry=NS(use_mesh_modifiers=viewport, use_mesh_modifiers_render=render)))))
            self.assertEqual(bevel_export.requested_for_export(props, is_static_mesh=True),
                             viewport and render)

    def test_explicit_export_preserves_disabled_linked_collision_and_other_modifiers(self):
        objs = [mesh(), mesh(render=False), mesh(viewport=True), mesh(kind='SUBSURF'),
                mesh('UCX_Mesh_00'), mesh(library=object()),
                NS(type='EMPTY', name='Pivot', modifiers=[])]
        calls = []
        with bevel_export.enabled_for_export(objs, enabled=True,
                                            update=lambda: calls.append(objs[0].modifiers[0].show_viewport)):
            self.assertEqual([obj.modifiers[0].show_viewport for obj in objs[:-1]],
                             [True, False, True, False, False, False])
        self.assertEqual([obj.modifiers[0].show_viewport for obj in objs[:-1]],
                         [False, False, True, False, False, False])
        self.assertEqual(calls, [True, False])

    def test_export_failure_restores_all_changed_states(self):
        objs = [mesh('A'), mesh('B')]
        with self.assertRaisesRegex(RuntimeError, 'export failed'):
            with bevel_export.enabled_for_export(objs, enabled=True):
                self.assertTrue(all(obj.modifiers[0].show_viewport for obj in objs))
                raise RuntimeError('export failed')
        self.assertTrue(all(not obj.modifiers[0].show_viewport for obj in objs))

    def test_evaluation_failure_also_restores_flags(self):
        obj = mesh()
        calls = []
        def update():
            calls.append(obj.modifiers[0].show_viewport)
            if len(calls) == 1:
                raise RuntimeError('evaluation failed')
        with self.assertRaisesRegex(RuntimeError, 'evaluation failed'):
            with bevel_export.enabled_for_export([obj], enabled=True, update=update):
                self.fail('evaluation failed before export')
        self.assertFalse(obj.modifiers[0].show_viewport)
        self.assertEqual(calls, [True, False])


if __name__ == '__main__':
    unittest.main()
