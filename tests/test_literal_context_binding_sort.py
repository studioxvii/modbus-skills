"""Exact binding order, fallback validation and bounded hash-work controls."""
import hashlib
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import user_map
from modbus_skills.artifacts import ArtifactContractError

def entry(point='point', ref=None, source_field='notes'):
    return {'field':'notes','literal':'Literal note','source_field':source_field,
            'oem_point_id':point,'source_ref':ref if ref is not None else {'record_id':'csv:2'}}

def digest(reference):
    return hashlib.sha256(json.dumps(reference,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()

def bindings(entries):
    unique=[]
    for item in entries:
        value={key:item[key] for key in ('oem_point_id','source_field','source_ref')}
        if value not in unique: unique.append(value)
    return sorted(unique,key=lambda b:(b['oem_point_id'],b['source_field'],digest(b['source_ref'])))

class LiteralContextBindingSortTests(unittest.TestCase):
    def test_unique_safe_pairs_skip_only_reference_hashes(self):
        original=user_map.stable_input_hash
        for count in (1,20):
            refs=[]
            entries=[entry('point-'+str(i),{'record_id':'csv:'+str(i+2),'row':i+2}) for i in reversed(range(count))]
            def spy(value):
                if 'record_id' in value:refs.append(value)
                return original(value)
            with self.subTest(count=count),patch.object(user_map,'stable_input_hash',side_effect=spy):
                group,=user_map.build_literal_source_context(entries)
            self.assertEqual(bindings(entries),group['bindings'])
            self.assertEqual([],refs)

    def test_tied_primary_references_keep_hash_order(self):
        entries=[entry('same',{'record_id':value}) for value in ('csv:9','csv:2','csv:100')]
        original=user_map.stable_input_hash;seen=[]
        def spy(value):
            if 'record_id' in value:seen.append(value)
            return original(value)
        with patch.object(user_map,'stable_input_hash',side_effect=spy):
            group,=user_map.build_literal_source_context(entries)
        self.assertEqual(bindings(entries),group['bindings'])
        self.assertEqual(3,len(seen))

    def test_permutations_duplicates_and_primary_field_order(self):
        original=[entry('a',{'record_id':'csv:9'}),entry('a',{'record_id':'csv:2'}),entry('a',{'record_id':'csv:1'},'notes_2'),entry('b',{'record_id':'csv:3'})]
        expected=bindings(original)
        for permutation in itertools.permutations(original):
            with self.subTest(order=[e['source_ref'] for e in permutation]):
                group,=user_map.build_literal_source_context([*permutation,permutation[0]])
                self.assertEqual(expected,group['bindings'])

    def test_hash_collision_preserves_original_tie_stability(self):
        entries=[entry(ref={'record_id':value}) for value in ('csv:9','csv:2','csv:8')]
        original=user_map.stable_input_hash
        def collision(value):return '0'*64 if 'record_id' in value else original(value)
        with patch.object(user_map,'stable_input_hash',side_effect=collision):
            group,=user_map.build_literal_source_context(entries)
        self.assertEqual([e['source_ref'] for e in entries],[b['source_ref'] for b in group['bindings']])

    def test_nonascii_custom_and_large_integer_refs_keep_hash_validation(self):
        class Text(str):pass
        class Number(int):pass
        original=user_map.stable_input_hash
        for reference in ({'record_id':'\u00e9'},{'\u00e9':'row'},{'record_id':Text('csv:2')},{Text('record_id'):'csv:2'},{'row':2**80},{'row':Number(2)}):
            seen=[]
            def spy(value):
                if 'field' not in value:seen.append(value)
                return original(value)
            with self.subTest(reference=reference),patch.object(user_map,'stable_input_hash',side_effect=spy):
                group,=user_map.build_literal_source_context([entry(ref=reference)])
            self.assertEqual(reference,group['bindings'][0]['source_ref'])
            self.assertEqual([reference],seen)

    def test_surrogate_reference_rejected_direct_and_imported(self):
        for ref in ({'record_id':'\ud800'},{'\udfff':'row'}):
            with self.subTest(ref=repr(ref)),self.assertRaisesRegex(ArtifactContractError,'deterministic JSON forms') as caught:
                user_map.build_literal_source_context([entry(ref=ref)])
            self.assertIsInstance(caught.exception.__cause__,UnicodeEncodeError)
            valid_group,=user_map.build_literal_source_context([entry()])
            valid_group['bindings'][0]['source_ref']=ref
            oem={'points':[{'oem_point_id':'point','source_refs':[ref]}],'assumptions':[valid_group]}
            with self.subTest(imported=repr(ref)),self.assertRaisesRegex(ArtifactContractError,'deterministic JSON forms') as caught:
                user_map._selected_literal_source_context(oem,{'point'})
            self.assertIsInstance(caught.exception.__cause__,UnicodeEncodeError)

    def test_custom_primary_types_retain_original_sort(self):
        class Text(str):pass
        original=user_map.stable_input_hash;seen=[]
        def spy(value):
            if 'record_id' in value:seen.append(value)
            return original(value)
        with patch.object(user_map,'stable_input_hash',side_effect=spy):
            group,=user_map.build_literal_source_context([entry(Text('point'))])
        self.assertEqual(1,len(seen))
        self.assertEqual('point',group['bindings'][0]['oem_point_id'])

    def test_budget_and_malformed_reference_gates_unchanged(self):
        original=user_map.stable_input_hash;refs=[]
        def spy(value):
            if 'record_id' in value:refs.append(value)
            return original(value)
        with patch.object(user_map,'_LITERAL_CONTEXT_BYTES',4096),patch.object(user_map,'stable_input_hash',side_effect=spy):
            with self.assertRaisesRegex(user_map.UserMapError,'budget'):
                user_map.build_literal_source_context([entry(ref={'record_id':'r'*5000})])
        self.assertEqual([],refs)
        for value in (True,None,[],1.5):
            with self.subTest(value=value),self.assertRaisesRegex(user_map.UserMapError,'reference is malformed'):
                user_map.build_literal_source_context([entry(ref={'record_id':value})])

if __name__=='__main__':unittest.main()
