"""Artist map identity survives display-name changes and rejects copied keys."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).parents[1]/'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
SPEC=importlib.util.spec_from_file_location('stable_guide_identity',PATH)
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

def fixture(names):
    vertices=[];triangles=[];parts=[];gids=[]
    for i,name in enumerate(names):
        first=len(vertices);gid=i+1
        vertices.extend([[i*3,0,0],[i*3+1,0,0],[i*3,1,0]])
        triangles.append([first,first+1,first+2]);gids.extend([gid]*3)
        parts.append({'gid':gid,'source_guide':name,'source_vertex_indices':[0,1,2],
                      'sim_vertex_indices':[first,first+1,first+2],
                      'simulation_mesh':{'effective_mode':'ORIGINAL','status':'flat_preserved'},
                      'provenance':'hair_tool_grid_src_island_index'})
    packet={'meshes':{'sim':{'vertices':vertices,'triangles':triangles,'guide_ids':gids}},'parts':parts}
    owner={'sim_source_indices':list(range(len(vertices))),
           'sim_source_identity_unique':[True]*len(vertices),'sim_guide_ids':gids}
    return packet,owner

class StableGuideIdentityTests(unittest.TestCase):
    def test_explicit_original_mesh_uses_verified_source_island_identity(self):
        packet,owner=fixture(['Guide'])
        expected=M._guide_identity_rows(packet,owner)[0]
        receipt=packet['parts'][0]['simulation_mesh']
        receipt.update(requested_mode='ORIGINAL',status='original_requested',
                       source_counts={'vertices':3,'triangles':1},
                       simulation_counts={'vertices':3,'triangles':1})
        self.assertEqual(M._guide_identity_rows(packet,owner)[0],expected)

    def test_original_request_rejects_changed_or_unverified_topology_receipts(self):
        for changed in ({'source_counts':{'vertices':4,'triangles':1}},
                        {'simulation_counts':{'vertices':3,'triangles':2}},
                        {'requested_mode':'RIBBON'}, {'source_counts':None}):
            with self.subTest(changed=changed):
                packet,owner=fixture(['Guide'])
                receipt=packet['parts'][0]['simulation_mesh']
                receipt.update(requested_mode='ORIGINAL',status='original_requested',
                               source_counts={'vertices':3,'triangles':1},
                               simulation_counts={'vertices':3,'triangles':1})
                receipt.update(changed)
                with self.assertRaisesRegex(ValueError,'identity mapping is missing'):
                    M._guide_identity_rows(packet,owner)

    def test_original_request_does_not_accept_unknown_guide_provenance(self):
        packet,owner=fixture(['Guide'])
        packet['parts'][0]['provenance']='guessed'
        packet['parts'][0]['simulation_mesh'].update(
            requested_mode='ORIGINAL',status='original_requested',
            source_counts={'vertices':3,'triangles':1},
            simulation_counts={'vertices':3,'triangles':1})
        with self.assertRaisesRegex(ValueError,'identity mapping is missing'):
            M._guide_identity_rows(packet,owner)

    def test_pinned_legacy_token_keeps_keys_after_rename(self):
        before,owner=fixture(['Legacy guide'])
        expected,old_rows=M._guide_identity_rows(before,owner)
        after=copy.deepcopy(before);after['parts'][0].update(source_guide='Renamed guide',source_identity='Legacy guide')
        actual,rows=M._guide_identity_rows(after,owner)
        self.assertEqual(actual,expected)
        self.assertEqual(rows[1]['source'],'Renamed guide')
        self.assertEqual(rows[1]['key'],old_rows[1]['key'])

    def test_unpinned_legacy_packet_keeps_name_based_behavior(self):
        packet,owner=fixture(['Guide'])
        before=M._guide_identity_rows(packet,owner)[0]
        packet['parts'][0]['source_guide']='Different guide'
        self.assertNotEqual(before,M._guide_identity_rows(packet,owner)[0])

    def test_duplicate_token_on_distinct_guides_is_rejected(self):
        packet,owner=fixture(['Guide A','Guide B'])
        for p in packet['parts']:p['source_identity']='Copied identity'
        with self.assertRaisesRegex(ValueError,'share one parent-spline identity'):
            M._guide_identity_rows(packet,owner)

    def test_invalid_tokens_are_rejected(self):
        for identity in ('', '  ', 'bad\0identity', [], None):
            with self.subTest(identity=identity):
                packet,owner=fixture(['Guide']);packet['parts'][0]['source_identity']=identity
                with self.assertRaisesRegex(ValueError,'stable token'):
                    M._guide_identity_rows(packet,owner)

if __name__=='__main__':unittest.main()
