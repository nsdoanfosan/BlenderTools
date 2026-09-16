"""Explicit per-file destinations must survive automatic path synchronization."""
import ast
from pathlib import Path
import types
import unittest


class MeshFolderSyncTests(unittest.TestCase):
    def test_manual_folder_is_preserved_while_default_scene_tracks_blend(self):
        source = Path(__file__).parents[1] / 'src/addons/send2ue/core/utilities.py'
        tree = ast.parse(source.read_text(encoding='utf8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'sync_unreal_mesh_folder_path')
        automatic = types.SimpleNamespace(unreal_mesh_folder_path='/Game/Old/')
        manual = types.SimpleNamespace(auto_sync_unreal_mesh_folder=False, unreal_mesh_folder_path='/Game/Meshes/windows/type_04/')
        wm = types.SimpleNamespace(path_validation=True)
        bpy = types.SimpleNamespace(
            data=types.SimpleNamespace(filepath='D:/Forestportfolio/00_common/window/window_04.blend', scenes=[
                types.SimpleNamespace(send2ue=automatic), types.SimpleNamespace(send2ue=manual)]),
            context=types.SimpleNamespace(window_manager=types.SimpleNamespace(send2ue=wm)))
        namespace = {'bpy': bpy, 'ToolInfo': types.SimpleNamespace(NAME=types.SimpleNamespace(value='send2ue')),
                     'unreal_path_mapping': lambda: {}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        namespace['sync_unreal_mesh_folder_path']()
        self.assertEqual(automatic.unreal_mesh_folder_path, '/Game/Meshes/00_common/window/')
        self.assertEqual(manual.unreal_mesh_folder_path, '/Game/Meshes/windows/type_04/')
        self.assertTrue(wm.path_validation)


if __name__ == '__main__':
    unittest.main()
