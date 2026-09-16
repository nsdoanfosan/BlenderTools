import ast
from pathlib import Path
import types
import unittest


class CombinedControlParentTests(unittest.TestCase):
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
