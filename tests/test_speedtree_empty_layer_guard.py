import unittest
import test_ue_material_setup as fixtures

class TestSpeedTreeEmptyLayerGuard(unittest.TestCase):
    def setUp(self):
        self.runtime=fixtures.FakeRuntime()
        self.module=fixtures._load_module(self.runtime)

    def test_empty_foliage_rejected_before_any_native_mutation(self):
        entry={'name':'M_cluster_leaf','tree_shading':'foliage','speedtree_intent':{'contract_version':3},'textures':[],'layers':[]}
        with self.assertRaisesRegex(RuntimeError,'existing MYI preserved'):
            self.module._assign_material_layer_instance(None,'cluster_leaf',[],{},entry,True)
        self.assertEqual(self.runtime.save_calls,[])
        self.assertEqual(self.runtime.created_assets,[])

    def test_empty_foliage_is_blocked_in_mesh_preflight_before_creation(self):
        entry={'name':'M_cluster_leaf','tree_shading':'foliage','speedtree_intent':{'contract_version':3},'textures':[],'layers':[]}
        with self.assertRaisesRegex(RuntimeError,'blocked before mutation'):
            self.module._validate_speedtree_handoff_contract({'materials':[entry]},'SK_Tree')
        self.assertEqual(self.runtime.created_assets,[])
        self.assertEqual(self.runtime.save_calls,[])

    def test_failed_declared_role_cannot_be_pruned_as_intentional_omission(self):
        entry={'name':'M_cluster_leaf','tree_shading':'foliage','speedtree_intent':{'contract_version':3},'layers':[{'textures':[{'param':'Albedo'},{'param':'Subsurface'}]}]}
        with self.assertRaisesRegex(RuntimeError,'Subsurface'):
            self.module._assign_material_layer_instance(None,'cluster_leaf',[{'textures':{'Albedo':'/Game/T_Color'}}],{},entry,True)
        self.assertEqual(self.runtime.save_calls,[])

if __name__=='__main__':unittest.main()
