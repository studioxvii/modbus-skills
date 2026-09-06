"""Public literal-only row associations; no new engineering or output columns."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from tests.test_literal_source_context import source, bundle, contexts
from modbus_skills import user_map
from modbus_skills.user_map import UserMapError

def extras(value):
    return [a for a in value['assumptions'] if a.get('code') == 'source-uninterpreted-fields']

def entry(fields=None, point='p', ref=None):
    return {'fields':fields or {'raw_count':8}, 'oem_point_id':point,
            'source_ref':ref or {'record_id':'csv:2'}}

class SourceUninterpretedFieldsTests(unittest.TestCase):
    def test_raw_columns_preserve_literal_types_without_engineering_promotion(self):
        oem = source(['Count Text','Resolution Text','Offset','Limits','Model Flag','Notes'],
                     [('8','1 mA / bit','-20','0...255','X','Existing note')])
        original = copy.deepcopy(oem)
        result = bundle(oem)
        extra, = extras(result['user_map'])
        self.assertEqual({'count_text':'8','resolution_text':'1 mA / bit','source_offset':'-20',
                          'limits':'0...255','model_flag':'X'}, extra['fields'])
        self.assertEqual('source-context-only', extra['status'])
        self.assertEqual({'record_id':'csv:2'}, extra['bindings'][0]['source_ref'])
        self.assertEqual(['Existing note'], [n['literal'] for n in contexts(result['user_map'])])
        self.assertIsNone(result['user_map']['points'][0]['engineering_unit'])
        self.assertIsNone(result['user_map']['points'][0]['engineering_offset'])
        self.assertNotIn('resolution_text', result['csv'].splitlines()[0])
        self.assertNotIn('1 mA / bit', result['human_summary'])
        self.assertEqual(original, oem)
        self.assertEqual(result, bundle(oem))

    def test_identical_fields_share_payload_but_keep_each_exact_row_binding(self):
        oem = source(['Raw Mode','Raw Count'], [('alpha',8),('alpha',8),('beta',16)],
                     names=['One','Two','Three'])
        self.assertEqual(2,len(extras(oem)))
        shared = next(g for g in extras(oem) if g['fields']['raw_mode']=='alpha')
        self.assertEqual(2,len(shared['bindings']))
        result = bundle(oem, {'One'})
        selected, = extras(result['user_map'])
        self.assertEqual(1,len(selected['bindings']))
        self.assertEqual('alpha',selected['fields']['raw_mode'])
        self.assertNotIn('beta', result['json'])

    def test_source_field_duplicates_and_conflicting_literals_remain_separate(self):
        result = bundle(source(['Raw Count','Raw Count'],[(8,16)]))
        group, = extras(result['user_map'])
        self.assertEqual({'raw_count':'8','raw_count_2':'16'}, group['fields'])

    def test_combined_group_binding_and_byte_limits_fail_closed(self):
        old = user_map.build_literal_source_context([{'field':'notes','literal':'existing',
            'source_field':'notes','oem_point_id':'p','source_ref':{'record_id':'csv:2'}}])
        for constant, maximum in [('_LITERAL_CONTEXT_GROUPS',1),('_LITERAL_CONTEXT_BINDINGS',1),
                                  ('_LITERAL_CONTEXT_BYTES',1)]:
            with self.subTest(constant=constant), patch.object(user_map,constant,maximum):
                with self.assertRaisesRegex(UserMapError,'limit|budget'):
                    user_map.build_uninterpreted_source_context([entry()], existing_context=old)

    def test_scalar_limits_and_malformed_fields_keep_existing_strict_guards(self):
        for literal in (True,{},[],float('nan'),float('inf'),'x'*(16384+1)):
            with self.subTest(kind=type(literal).__name__), self.assertRaises(UserMapError):
                user_map.build_uninterpreted_source_context([entry({'raw':literal})])
        accepted = user_map.build_uninterpreted_source_context([entry({'raw':'é'*8192})])
        self.assertEqual('é'*8192, accepted[0]['fields']['raw'])
        for field in ('', '_claims', 'notes', 'source_register'):
            with self.subTest(field=field), self.assertRaises(UserMapError):
                user_map.build_uninterpreted_source_context([entry({field:'literal'})])

    def test_imported_registry_rejects_identity_membership_and_payload_mutations(self):
        original = source(['Raw Value'], [('literal',)])
        for mutation in ('point','reference','identity','status','payload'):
            oem = copy.deepcopy(original)
            group, = extras(oem)
            if mutation=='point': group['bindings'][0]['oem_point_id']='not-an-oem-point'
            elif mutation=='reference': group['bindings'][0]['source_ref']={'record_id':'csv:999'}
            elif mutation=='identity': group['context_id']='source-fields-forged'
            elif mutation=='status': group['status']='approved'
            else: group['fields']['raw_value']='changed'
            with self.subTest(mutation=mutation), self.assertRaises(UserMapError): bundle(oem)

    def test_signed_zero_and_type_identity_are_not_collapsed(self):
        values = [0, 0.0, -0.0, '0']
        rows = [entry({'raw':value},point=str(i)) for i,value in enumerate(values)]
        result = user_map.build_uninterpreted_source_context(rows)
        self.assertEqual(4,len(result))
        self.assertEqual(json.dumps(result,sort_keys=True),
                         json.dumps(user_map.build_uninterpreted_source_context(reversed(rows)),sort_keys=True))

    def test_ordinary_existing_four_role_registry_outputs_stay_unchanged(self):
        oem = source(['Notes','Min','Max'],[('same literal',0,10)])
        self.assertEqual([],extras(oem))
        self.assertEqual({'notes','minimum','maximum'},{g['field'] for g in contexts(oem)})
        self.assertNotIn('Additional source fields',bundle(oem)['human_summary'])

if __name__ == '__main__': unittest.main()
