"""Combined skeletal accessories keep their cards without widening rig scope."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

class Object(dict):
    def __init__(self,name,kind='MESH',parent=None):
        super().__init__();self.name=name;self.type=kind;self.parent=parent
    def __eq__(self,other):return self is other

class CombinedHairScope(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'src/addons/send2ue/core/export_selection.py'
        old=sys.modules.get('bpy');sys.modules['bpy']=types.SimpleNamespace()
        try:
            spec=importlib.util.spec_from_file_location('_combined_hair_scope',path)
            self.rules=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.rules)
        finally:
            if old is None:sys.modules.pop('bpy',None)
            else:sys.modules['bpy']=old
        self.unit=Object('Ornament',kind='EMPTY')
        self.anchor=Object('BeadLow',parent=self.unit)
        self.cards=Object('PreparedCards',parent=self.unit)
        self.cards['_send2ue_hair_tool_temp']=True
        self.cards['_send2ue_hair_tool_source_name']='Ornament'

    def test_same_combined_empty_keeps_its_generated_cards(self):
        self.assertTrue(self.rules.keep_prepared_hair_in_mesh_export(self.cards,self.anchor,self.unit))

    def test_other_empty_and_shared_rig_do_not_merge_peer_hair(self):
        other=Object('Other',kind='EMPTY')
        self.assertFalse(self.rules.keep_prepared_hair_in_mesh_export(self.cards,self.anchor,other))
        rig=Object('Body',kind='ARMATURE');self.cards.parent=rig;self.anchor.parent=rig
        self.assertFalse(self.rules.keep_prepared_hair_in_mesh_export(self.cards,self.anchor,rig))

    def test_standalone_hair_keeps_itself_and_ordinary_meshes_are_unchanged(self):
        self.assertTrue(self.rules.keep_prepared_hair_in_mesh_export(self.cards,self.cards))
        self.assertTrue(self.rules.keep_prepared_hair_in_mesh_export(self.anchor,self.cards))

if __name__=='__main__':unittest.main()
