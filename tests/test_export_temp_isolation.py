"""A different Blender's cleanup must not delete an in-flight handoff."""
import ast
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


class ExportTempIsolationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'src/addons/send2ue/core/utilities.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'get_temp_folder', 'remove_temp_data'}]
        self.namespace = {'os': os, 'tempfile': tempfile, 'shutil': shutil}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), self.namespace)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_patch = patch.object(tempfile, 'gettempdir', return_value=self.temp.name)
        self.temp_patch.start()
        self.addCleanup(self.temp_patch.stop)

    def folder(self, pid):
        with patch.object(os, 'getpid', return_value=pid):
            return Path(self.namespace['get_temp_folder']())

    def test_unregistering_another_process_preserves_both_handoff_files(self):
        first, second = self.folder(101), self.folder(202)
        for folder in (first, second):
            folder.mkdir(parents=True)
            (folder/'hair.fbx').write_bytes(b'actual mesh payload')
            (folder/'hair.hair_guide_cloth.json').write_text('{"source":"Guide"}')
        with patch.object(os, 'getpid', return_value=202):
            self.namespace['remove_temp_data']()
        self.assertFalse(second.exists())
        self.assertEqual((first/'hair.fbx').read_bytes(), b'actual mesh payload')
        self.assertEqual((first/'hair.hair_guide_cloth.json').read_text(), '{"source":"Guide"}')

    def test_legacy_addon_cleanup_cannot_remove_new_process_files(self):
        current = self.folder(101)
        current.mkdir(parents=True)
        (current/'hair.fbx').write_bytes(b'verified input')
        legacy = Path(self.temp.name)/'blender'/'send2ue'/'data'
        legacy.mkdir(parents=True)
        shutil.rmtree(legacy)
        self.assertEqual((current/'hair.fbx').read_bytes(), b'verified input')

    def test_same_process_path_is_stable_between_export_and_ingest(self):
        self.assertEqual(self.folder(101), self.folder(101))
        self.assertNotEqual(self.folder(101), self.folder(202))


if __name__ == '__main__':
    unittest.main()
