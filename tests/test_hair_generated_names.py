"""Generated guide pairs retain their distinct names under parent naming."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

FILE=Path(__file__).resolve().parents[1]/'src/addons/send2ue/resources/extensions/use_immediate_parent_name.py'
tree=ast.parse(FILE.read_text('utf8'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='get_full_asset_name')
namespace={'utilities':SimpleNamespace(get_asset_name=lambda name,props:name)}
exec(compile(ast.Module(body=[method],type_ignores=[]),str(FILE),'exec'),namespace)

class Object(dict):
    name='GuideSim';parent=None

class Names(unittest.TestCase):
    def name(self,obj):
        instance=SimpleNamespace(get_parent_collection_name=lambda name,props:'ordinary_collection')
        return namespace['get_full_asset_name'](instance,obj,None)

    def test_owned_generated_members_keep_distinct_resolved_names(self):
        for role,suffix in [('sim','_GuideSim'),('render','')]:
            obj=Object(_send2ue_hair_tool_temp=True,_send2ue_guide_cloth_role=role,
                _send2ue_guide_cloth_package='f'*64,_send2ue_hair_tool_source_name='Back'+suffix)
            self.assertEqual(self.name(obj),'Back'+suffix)

    def test_ordinary_artist_mesh_still_uses_parent(self):
        obj=Object(_send2ue_hair_tool_source_name='ignored');obj.parent=SimpleNamespace(type='EMPTY',name='ArtistUnit')
        self.assertEqual(self.name(obj),'ArtistUnit')

    def test_incomplete_generated_marker_cannot_override_ordinary_naming(self):
        original=dict(_send2ue_hair_tool_temp=True,_send2ue_guide_cloth_role='sim',
            _send2ue_guide_cloth_package='f'*64,_send2ue_hair_tool_source_name='Back_GuideSim')
        for key in original:
            obj=Object(original);del obj[key]
            self.assertEqual(self.name(obj),'ordinary_collection')
        obj=Object(original);obj['_send2ue_guide_cloth_package']='invalid'
        self.assertEqual(self.name(obj),'ordinary_collection')

if __name__=='__main__':unittest.main()
