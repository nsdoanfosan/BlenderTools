"""Exercise the real importer method without loading Blender or Unreal."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = (Path(__file__).resolve().parents[1]
          / 'src/addons/send2ue/dependencies/unreal.py')


def load_run_import(unreal):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    importer = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'UnrealImportAsset')
    method = next(node for node in importer.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'run_import')
    namespace = {'unreal': unreal}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['run_import']


class StaticMesh:
    pass


class TestNestedPivotImportSuccess(unittest.TestCase):
    expected = '/Game/Window/window_glass'

    def run_import(self, paths, loaded, *, nested=True, asset_type='StaticMesh', expected=None):
        self.calls = []
        self.loads = []
        task = SimpleNamespace(get_editor_property=lambda name: list(paths))
        asset_tools = SimpleNamespace(import_asset_tasks=lambda tasks: self.calls.append(('import', tasks)))
        unreal = SimpleNamespace(
            AssetToolsHelpers=SimpleNamespace(get_asset_tools=lambda: asset_tools),
            StaticMesh=StaticMesh,
            load_asset=lambda path: self.loads.append(path) or loaded,
        )
        asset_data = {'asset_path': self.expected if expected is None else expected,
                      '_asset_type': asset_type}
        if nested:
            asset_data['_nested_pivot_component'] = {'root': 'window', 'name': 'window_glass'}
        instance = SimpleNamespace(
            _import_task=task, _options=object(), _asset_data=asset_data,
            ensure_hair_tool_uv_precision=lambda values: self.calls.append(('uv', values)),
            ensure_hair_tool_nanite=lambda values: self.calls.append(('nanite', values)),
            audit_hair_tool_payload=lambda values: self.calls.append(('audit', values)),
        )
        return load_run_import(unreal)(instance)

    def assert_stopped_before_post_import_work(self):
        self.assertEqual([name for name, _ in self.calls], ['import'])

    def test_empty_result_cannot_reuse_a_stale_static_mesh(self):
        with self.assertRaisesRegex(RuntimeError, 'did not produce the expected'):
            self.run_import([], StaticMesh())
        self.assert_stopped_before_post_import_work()
        self.assertEqual(self.loads, [])

    def test_wrong_mesh_result_cannot_reuse_expected_stale_asset(self):
        with self.assertRaisesRegex(RuntimeError, 'did not produce the expected'):
            self.run_import(['/Game/Other/window_glass.window_glass'], StaticMesh())
        self.assert_stopped_before_post_import_work()
        self.assertEqual(self.loads, [])

    def test_exact_result_must_load_as_static_mesh(self):
        for invalid_asset in (None, object()):
            with self.subTest(invalid_asset=invalid_asset):
                with self.assertRaisesRegex(RuntimeError, 'missing or is not a StaticMesh'):
                    self.run_import([self.expected + '.window_glass'], invalid_asset)
                self.assert_stopped_before_post_import_work()

    def test_missing_expected_path_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'did not produce the expected'):
            self.run_import([self.expected], StaticMesh(), expected='')
        self.assert_stopped_before_post_import_work()

    def test_exact_object_or_package_result_reaches_existing_post_functions(self):
        for result_path in (self.expected, self.expected + '.window_glass'):
            with self.subTest(result_path=result_path):
                paths = [result_path, '/Game/Window/unrelated_material']
                self.assertEqual(self.run_import(paths, StaticMesh()), paths)
                self.assertEqual(self.loads, [self.expected])
                self.assertEqual([name for name, _ in self.calls], ['import', 'uv', 'nanite', 'audit'])

    def test_ordinary_mesh_retains_previous_behavior_even_for_empty_result(self):
        self.assertEqual(self.run_import([], None, nested=False), [])
        self.assertEqual(self.loads, [])
        self.assertEqual([name for name, _ in self.calls], ['import', 'uv', 'nanite', 'audit'])

    def test_skeletal_path_is_unchanged(self):
        self.assertEqual(self.run_import([], None, asset_type='SkeletalMesh'), [])
        self.assertEqual(self.loads, [])
        self.assertEqual([name for name, _ in self.calls], ['import', 'uv', 'nanite', 'audit'])


if __name__ == '__main__':
    unittest.main()
