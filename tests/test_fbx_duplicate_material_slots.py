import ast,re
from pathlib import Path
import unittest


class DuplicateSlotTests(unittest.TestCase):
    def test_only_known_fbx_alias_uses_authored_material(self):
        path=Path(__file__).parents[1]/'src/addons/send2ue/resources/pipeline/ue_material_setup.py'
        tree=ast.parse(path.read_text(encoding='utf8'));node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_sync_fbx_duplicate_material_slots')
        class Slot:
            def __init__(self,name,mat):self.values={'imported_material_slot_name':name,'material_interface':mat}
            def get_editor_property(self,key):return self.values[key]
        slots=[Slot('M_Wood','wood'),Slot('M_Wood_ncl1_1','wrong'),Slot('M_Unknown_ncl1_1','keep'),Slot('M_Wood_ncl1_2','explicit')]
        def assign(mesh,index,material):
            old=mesh[index].values['material_interface'];mesh[index].values['material_interface']=material;return old!=material
        env={'re':re,'_mesh_material_entries':lambda mesh:('static_materials',mesh),'_slot_index_for_entry':lambda mesh,entry,name:0,'_set_material_interface':assign}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
        data={'materials':[{'name':'M_Wood'},{'name':'M_Wood_ncl1_2'}]}
        self.assertTrue(env['_sync_fbx_duplicate_material_slots'](slots,data))
        self.assertEqual([s.values['material_interface'] for s in slots],['wood','wood','keep','explicit'])
        self.assertFalse(env['_sync_fbx_duplicate_material_slots'](slots,data))


if __name__=='__main__':unittest.main()
