"""Legacy children re-linked by hooks must never re-enter a declared assembly."""
import ast,importlib.util,sys,types,unittest
from pathlib import Path

ADDON=Path(__file__).resolve().parents[1]/'src/addons/send2ue'

class Obj(dict):
    def __init__(self,name,parent=None,kind='MESH'):
        super().__init__();self.name=name;self.parent=parent;self.type=kind
        self.children=[];self.selected=False
        if parent is not None:parent.children.append(self)
    __hash__=object.__hash__
    def __eq__(self,other):return self is other
    def select_set(self,v):self.selected=v
    def __bool__(self):return True

class SourceSelectionTests(unittest.TestCase):
    def test_recursive_selection_rejects_relinked_legacy_child_and_keeps_rig(self):
        unit=Obj('Ornament',kind='EMPTY')
        current=Obj('MeshyLow',unit);legacy=Obj('OldLow',unit)
        rig=Obj('Armature',kind='ARMATURE');temporary=Obj('PreparedHair',unit)
        temporary['_send2ue_hair_tool_temp']=True
        bpy=types.SimpleNamespace(context=types.SimpleNamespace(view_layer=types.SimpleNamespace(
            objects=[unit,current,legacy,rig,temporary])))
        saved=sys.modules.get('bpy');sys.modules['bpy']=bpy
        try:
            spec=importlib.util.spec_from_file_location('source_rules',ADDON/'core/export_selection.py')
            rules=importlib.util.module_from_spec(spec);spec.loader.exec_module(rules)
        finally:
            if saved is None:sys.modules.pop('bpy',None)
            else:sys.modules['bpy']=saved
        rules._rules=({'Ornament':unit},{'Ornament'},{'Ornament':{current}})
        self.assertTrue(rules.includes(rig))
        tree=ast.parse((ADDON/'core/utilities.py').read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='select_all_children')
        package=types.ModuleType('_selection_regression');package.__path__=[]
        package.export_selection=rules
        package.hair_tool_export=types.SimpleNamespace(is_prepared_source=lambda _:False)
        sys.modules[package.__name__]=package
        context={'__package__':package.__name__,'bpy':bpy,'PreFixToken':[],
                 'get_meshes_using_armature_modifier':lambda _:[ ]}
        try:
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'<recursive-selector>','exec'),context)
            # Both children are members: a hook has re-linked the legacy mesh.
            context['select_all_children'](unit,'MESH',required_collection=
                types.SimpleNamespace(all_objects=[current,legacy,temporary]))
            self.assertTrue(current.selected);self.assertTrue(temporary.selected)
            self.assertFalse(legacy.selected)
        finally:sys.modules.pop(package.__name__,None)

if __name__=='__main__':unittest.main()
