import ast
from pathlib import Path
import types
import unittest


class CombinedControlParentTests(unittest.TestCase):
    def test_curve_controls_share_the_existing_export_empty(self):
        path = Path(__file__).parents[1] / 'src/addons/send2ue/core/utilities.py'
        tree = ast.parse(path.read_text(encoding='utf8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'get_combined_export_parent', 'get_unique_parent_mesh_objects'}]

        class Obj:
            def __init__(self, kind, parent=None, collections=()):
                self.type = kind
                self.parent = parent
                self.users_collection = collections

        export = types.SimpleNamespace(all_objects=[])
        owner = Obj('EMPTY', collections=[export])
        left = Obj('CURVE', owner, [export])
        right = Obj('CURVE', owner, [export])
        meshes = [Obj('MESH', left, [export]), Obj('MESH', right, [export])]
        export.all_objects = [owner, left, right, *meshes]
        env = {
            'bpy': types.SimpleNamespace(data=types.SimpleNamespace(collections={'Export': export})),
            'ToolInfo': types.SimpleNamespace(EXPORT_COLLECTION=types.SimpleNamespace(value='Export')),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), env)
        resolve = env['get_combined_export_parent']
        self.assertTrue(all(resolve(mesh) is owner for mesh in meshes))
        self.assertEqual(env['get_unique_parent_mesh_objects']([], meshes), [meshes[0]])
        # A curve outside the export scope must not silently absorb a mesh.
        export.all_objects.remove(left)
        self.assertIs(resolve(meshes[0]), left)
        # Keep standalone curve behavior when there is no existing Empty owner.
        export.all_objects.append(left)
        left.parent = None
        self.assertIs(resolve(meshes[0]), left)

    def test_indirect_control_joins_owner_but_direct_pivot_stays_separate(self):
        path=Path(__file__).parents[1]/'src/addons/send2ue/core/utilities.py'
        tree=ast.parse(path.read_text(encoding='utf8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_combined_export_parent')
        class Obj:
            type='EMPTY'
            def __init__(self,parent=None,collections=()):self.parent=parent;self.users_collection=collections
        export=types.SimpleNamespace(all_objects=[]);owner=Obj(collections=[export]);helper=Obj(owner);mesh=Obj(helper);mesh.type='MESH';export.all_objects=[owner,helper,mesh]
        env={'bpy':types.SimpleNamespace(data=types.SimpleNamespace(collections={'Export':export})), 'ToolInfo':types.SimpleNamespace(EXPORT_COLLECTION=types.SimpleNamespace(value='Export'))}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
        resolve=env['get_combined_export_parent'];self.assertIs(resolve(mesh),owner)
        helper.users_collection=[export];self.assertIs(resolve(mesh),helper)
        helper.users_collection=[];export.all_objects.remove(helper);self.assertIs(resolve(mesh),helper)
        helper.type='ARMATURE';self.assertIs(resolve(mesh),helper)


if __name__=='__main__':unittest.main()
