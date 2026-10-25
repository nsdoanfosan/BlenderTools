"""Exercise the real export boundary; ordinary color exports keep their preset."""
import ast
import os
import tempfile
import types
import unittest
from pathlib import Path


class NumericColorExportTests(unittest.TestCase):
    def export(self, data_flag, file_type='fbx', sibling_flags=()):
        source=Path(__file__).parents[1]/'src/addons/send2ue/core/export.py'
        tree=ast.parse(source.read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='export_file')
        ftypes=types.SimpleNamespace(FBX='fbx',ABC='abc')
        called=[]
        with tempfile.TemporaryDirectory() as folder:
            class Mesh(dict):
                type='MESH'
            mesh=Mesh(send2ue_vertex_colors_are_data=data_flag, present=True)
            selected=[mesh]+[Mesh(send2ue_vertex_colors_are_data=flag) for flag in sibling_flags]
            asset={'file_path':str(Path(folder)/'mesh.fbx'),'_mesh_object_name':'mesh'}
            settings=types.SimpleNamespace(
                get_settings_by_path=lambda *a:{'geometry':{'colors_type':None}},
                get_generated_prefix=lambda *a:'geometry',
                get_property_by_path=lambda *a:'SRGB')
            env={'bpy':types.SimpleNamespace(
                    context=types.SimpleNamespace(selected_objects=selected,window_manager=types.SimpleNamespace(
                        send2ue=types.SimpleNamespace(asset_id='one',asset_data={'one':asset}))),
                    data=types.SimpleNamespace(objects={'mesh':mesh})),
                 'os':os,'settings':settings,'FileTypes':ftypes,
                 'hair_tool_export':types.SimpleNamespace(TEMP_PROPERTY='temporary_hair'),
                 'export_fbx_file':lambda path,values:called.append(('fbx',values)),
                 'export_alembic_file':lambda path,values:called.append(('abc',values))}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
            env['export_file'](types.SimpleNamespace(skip_animation_export=True),file_type=file_type)
        return called

    def test_numerical_weights_use_linear_fbx_bytes(self):
        self.assertEqual(self.export(True)[0][1]['colors_type'],'LINEAR')

    def test_ordinary_render_colors_keep_the_authored_preset(self):
        self.assertEqual(self.export(False)[0][1]['colors_type'],'SRGB')

    def test_alembic_does_not_inherit_the_fbx_override(self):
        kind,settings=self.export(True,'abc')[0]
        self.assertEqual(kind,'abc')
        self.assertEqual(settings['colors_type'],'SRGB')

    def test_marked_sibling_cannot_be_silently_encoded_by_unmarked_representative(self):
        with self.assertRaisesRegex(RuntimeError,'separate asset groups'):
            self.export(False,sibling_flags=(True,))

    def test_unmarked_sibling_cannot_be_silently_encoded_by_marked_representative(self):
        with self.assertRaisesRegex(RuntimeError,'separate asset groups'):
            self.export(True,sibling_flags=(False,))

    def test_homogeneous_combined_groups_preserve_their_encoding(self):
        self.assertEqual(self.export(True,sibling_flags=(True,))[0][1]['colors_type'],'LINEAR')
        self.assertEqual(self.export(False,sibling_flags=(False,))[0][1]['colors_type'],'SRGB')


if __name__=='__main__':unittest.main()
