"""Additional prospective association/identity boundaries; tiny public inputs."""
import json
import unittest
from unittest.mock import patch
from test_literal_source_context import source, contexts, bundle
from modbus_skills import user_map
from modbus_skills.user_map import UserMapError


class LiteralSourceContextEdgeTests(unittest.TestCase):
    def test_tenth_and_later_duplicate_source_columns_are_not_dropped(self):
        oem=source(['Notes']*12,[tuple(f'Claim {i}' for i in range(12))])
        notes=contexts(bundle(oem)['user_map'])
        self.assertEqual(12,len(notes))
        self.assertEqual({'notes',*(f'notes_{i}' for i in range(2,13))},
                         {b['source_field'] for n in notes for b in n['bindings']})

    def test_exact_lowered_evidence_budget_boundary(self):
        entry={'field':'notes','literal':'A <literal> annotation','oem_point_id':'point',
               'source_field':'notes','source_ref':{'record_id':'csv:2'}}
        group,=user_map.build_literal_source_context([entry])
        binding,=group['bindings']
        payload={**group,'bindings':[]}
        def size(value):
            text=json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2,allow_nan=False)
            return len(text.encode())+32*(text.count('\n')+1)+256
        budget=2*(size(payload)+size(binding))
        with patch.object(user_map,'_LITERAL_CONTEXT_BYTES',budget):
            self.assertEqual([group],user_map.build_literal_source_context([entry,entry]))
        with patch.object(user_map,'_LITERAL_CONTEXT_BYTES',budget-1):
            with self.assertRaisesRegex(UserMapError,'budget'):
                user_map.build_literal_source_context([entry])

    def test_malformed_field_and_nonfinite_numeric_context_reject_cleanly(self):
        entry={'field':'notes','literal':'note','oem_point_id':'point',
               'source_field':'notes','source_ref':{'record_id':'csv:2'}}
        for mutation in ({'field':[]},{'literal':True},{'literal':float('nan')},
                         {'literal':float('inf')},{'literal':{'nested':'not-literal'}}):
            with self.subTest(mutation=mutation),self.assertRaises(UserMapError):
                user_map.build_literal_source_context([{**entry,**mutation}])

    def test_conflicting_range_columns_are_context_not_new_engineering_fields(self):
        oem=source(['Min','Min','Max'],[(0,10,500)])
        result=bundle(oem)
        notes=contexts(result['user_map'])
        self.assertEqual({'0','10'},{n['literal'] for n in notes if n['field']=='minimum'})
        self.assertTrue(all(n['status']=='source-context-only' for n in notes))
        self.assertNotIn('minimum',result['user_map']['points'][0])

if __name__=='__main__':unittest.main()
