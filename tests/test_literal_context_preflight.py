"""Small prospective guards: reject before hashing/serializing added context."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import user_map
from modbus_skills.user_map import UserMapError

def entry():
    return {'field':'notes','literal':'small note','oem_point_id':'point',
            'source_field':'notes','source_ref':{'record_id':'csv:2'}}

def registry(literal,field='notes'):
    return {'points':[{'oem_point_id':'point','source_refs':[{'record_id':'csv:2'}]}],
            'assumptions':[{'code':'source-literal-context','context_id':'not-trusted',
                'status':'source-context-only','field':field,'literal':literal,
                'bindings':[{'oem_point_id':'point','source_field':'notes','source_ref':{'record_id':'csv:2'}}]}]}

class LiteralContextPreflightTests(unittest.TestCase):
    def test_oversized_metadata_rejected_before_binding_hash_or_json_serialization(self):
        variants=[{'oem_point_id':'p'*5000},
                  {'source_ref':{'record_id':'r'*5000}},
                  {'source_ref':{'k'*5000:'v'}},
                  {'source_ref':{f'k{i}':'x'*50 for i in range(100)}},
                  {'source_ref':{'record_id':'\n'*1200}}]
        original_hash=user_map.stable_input_hash
        original_dumps=json.dumps
        for mutation in variants:
            hashed=[];serialized=[]
            def hash_spy(value):
                if isinstance(value,dict) and 'source_ref' in value:hashed.append(True)
                return original_hash(value)
            def dumps_spy(value,*args,**kwargs):
                if isinstance(value,dict) and 'source_ref' in value:serialized.append(True)
                return original_dumps(value,*args,**kwargs)
            with self.subTest(keys=list(mutation)),patch.object(user_map,'_LITERAL_CONTEXT_BYTES',4096), \
                 patch.object(user_map,'stable_input_hash',side_effect=hash_spy),patch.object(json,'dumps',side_effect=dumps_spy):
                with self.assertRaisesRegex(UserMapError,'budget'):
                    user_map.build_literal_source_context([{**entry(),**mutation}])
            self.assertEqual([],hashed,'oversized added binding was hashed before its budget gate')
            self.assertEqual([],serialized,'oversized added binding was serialized before its budget gate')

    def test_imported_invalid_literals_raise_clean_error_before_identity_hash(self):
        original_hash=user_map.stable_input_hash
        for literal in (float('nan'),float('inf'),True,[],{'nested':'not a literal'},'x'*33):
            calls=[]
            def spy(value):
                calls.append(True)
                return original_hash(value)
            with self.subTest(kind=type(literal).__name__),patch.object(user_map,'_LITERAL_CONTEXT_LITERAL_BYTES',32), \
                 patch.object(user_map,'stable_input_hash',side_effect=spy):
                with self.assertRaises(UserMapError):
                    user_map._selected_literal_source_context(registry(literal),{'point'})
            self.assertEqual([],calls,'malformed imported literal reached identity hashing')

    def test_imported_unsupported_field_rejected_before_identity_hash(self):
        with patch.object(user_map,'stable_input_hash',wraps=user_map.stable_input_hash) as hashed:
            with self.assertRaises(UserMapError):
                user_map._selected_literal_source_context(registry('note',field=[]),{'point'})
        hashed.assert_not_called()

if __name__=='__main__':unittest.main()
