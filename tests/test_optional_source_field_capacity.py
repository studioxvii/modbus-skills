"""Capacity-limited new annotations must not erase an existing useful bundle."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from tests.test_literal_source_context import source,bundle,contexts
from tests.test_source_uninterpreted_fields import extras,entry
from modbus_skills import user_map
from modbus_skills.source_intake import SourceIntakeError
from modbus_skills.user_map import UserMapError

class OptionalSourceFieldCapacityTests(unittest.TestCase):
    def test_byte_group_binding_capacity_omits_entire_new_registry_and_preserves_bundle(self):
        plain=source(extra_rows=[()]*3)
        prior=bundle(plain)
        for constant,maximum in [('_LITERAL_CONTEXT_BYTES',1),('_LITERAL_CONTEXT_GROUPS',1),('_LITERAL_CONTEXT_BINDINGS',2)]:
            with self.subTest(constant=constant),patch.object(user_map,constant,maximum):
                oem=source(['Raw Count','Raw Mode'],[(8,'a'),(16,'b'),(32,'c')])
                result=bundle(oem)
            self.assertEqual([],extras(oem))
            self.assertEqual([],extras(result['user_map']))
            hold,=[h for h in oem['holds'] if h['code']=='source.uninterpreted-fields-incomplete']
            self.assertTrue(hold['blocking'])
            self.assertEqual(oem['source_sha256'],hold['details']['source_sha256'])
            self.assertEqual(3,hold['details']['omitted_source_rows'])
            self.assertEqual(6,hold['details']['omitted_scalar_field_associations'])
            counts='6 scalar field associations across 3 source rows'
            self.assertIn(counts,hold['message'])
            grouped,=[h for h in result['user_map']['holds'] if h['code']=='source.uninterpreted-fields-incomplete']
            self.assertIn(counts,grouped['message'])
            self.assertIn(counts,result['human_summary'])
            self.assertEqual(prior['csv'],result['csv'])
            self.assertEqual(plain['points'],oem['points'])
            self.assertEqual(prior['user_map']['points'],result['user_map']['points'])
            self.assertIn('source.uninterpreted-fields-incomplete',result['human_summary'])

    def test_old_registry_capacity_still_aborts_instead_of_fallback(self):
        for constant,maximum in [('_LITERAL_CONTEXT_BYTES',1),('_LITERAL_CONTEXT_GROUPS',1),('_LITERAL_CONTEXT_BINDINGS',1)]:
            with self.subTest(constant=constant),patch.object(user_map,constant,maximum):
                with self.assertRaises(SourceIntakeError):
                    source(['Notes','Raw Value'],[('one',1),('two',2)])

    def test_old_notes_remain_exact_when_only_optional_role_overflows(self):
        old=source(['Notes'],[('existing',)]*2)
        with patch.object(user_map,'_LITERAL_CONTEXT_BINDINGS',2):
            actual=source(['Notes','Raw Value'],[('existing',8),('existing',16)])
        self.assertEqual(contexts(old),contexts(actual))
        self.assertEqual([],extras(actual))
        self.assertEqual(1,len([h for h in actual['holds'] if h['code']=='source.uninterpreted-fields-incomplete']))

    def test_malformed_new_scalar_is_not_capacity_fallback(self):
        with self.assertRaises(SourceIntakeError):
            source(['Raw Value'],[('x'*(16384+1),)])
        with self.assertRaises(UserMapError):
            user_map.build_uninterpreted_source_context([entry({'raw':float('nan')})])

    def test_imported_registry_overflow_remains_a_hard_failure(self):
        oem=source(['Raw Value'],[(1,),(2,)])
        unchanged=copy.deepcopy(oem)
        with patch.object(user_map,'_LITERAL_CONTEXT_BINDINGS',1):
            with self.assertRaises(UserMapError):bundle(oem)
        self.assertEqual(unchanged,oem)

    def test_imported_invalid_source_binding_is_not_capacity_fallback(self):
        oem=source(['Raw Value'],[(1,)])
        extras(oem)[0]['bindings'][0]['source_ref']={'record_id':'csv:999'}
        with self.assertRaisesRegex(UserMapError,'actual OEM point/source'):bundle(oem)

    def test_capacity_before_later_malformed_literal_cannot_hide_the_error(self):
        with patch.object(user_map,'_LITERAL_CONTEXT_GROUPS',0):
            with self.assertRaisesRegex(SourceIntakeError,'16 KiB'):
                source(['Raw Value'],[('ordinary',),('x'*(16384+1),)])

    def test_direct_old_builder_remains_hard_failure_with_same_base_type_and_text(self):
        row={'field':'notes','literal':'old','source_field':'notes',
             'oem_point_id':'p','source_ref':{'record_id':'csv:2'}}
        with patch.object(user_map,'_LITERAL_CONTEXT_BYTES',1):
            with self.assertRaisesRegex(UserMapError,'literal source context exceeds the 4 MiB evidence budget'):
                user_map.build_literal_source_context([row])

if __name__=='__main__':unittest.main()
