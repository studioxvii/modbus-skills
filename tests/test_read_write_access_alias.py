"""The literal access value Read/write describes readable data, not a write request."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.map_workflows import normalize_map
from modbus_skills.read_plan import compile_read_plan


class ReadWriteAccessAliasTests(unittest.TestCase):
    def record(self, access):
        return {'protocol_offset': 12, 'area': 'holding-register', 'unit_id': 1,
                'route_id': 'synthetic', 'name': 'Synthetic setting',
                'datatype': 'uint16', 'access': access}

    def test_literal_read_write_is_readable_with_original_evidence(self):
        for literal in ('Read/write', 'read/write', 'READ/WRITE', ' Read/write '):
            with self.subTest(literal=literal):
                result = normalize_map([self.record(literal)])
                point = result['points'][0]
                self.assertEqual('read-write', point['access'])
                self.assertEqual([], result['holds'])
                self.assertEqual(3, point['function_code'])
                evidence = next(item for item in point['source_evidence'] if item['field'] == 'access')
                self.assertEqual(literal, evidence['source_value']['access'])
                plan = compile_read_plan(result['points'])
                self.assertEqual(1, len(plan.requests))
                self.assertEqual(3, plan.requests[0].function_code)

    def test_existing_explicit_spellings_retain_their_meaning(self):
        for literal, expected in (('R/W', 'read-write'), ('Read write', 'read-write'),
                                  ('read-write', 'read-write'), ('Read only', 'read-only')):
            with self.subTest(literal=literal):
                result = normalize_map([self.record(literal)])
                self.assertEqual(expected, result['points'][0]['access'])
                self.assertEqual([], result['holds'])

    def test_write_only_and_conflicting_flags_still_prevent_read_plans(self):
        records = [self.record('Write only'),
                   {**self.record('Read/write'), 'access_readable': False, 'access_writable': True}]
        for record in records:
            with self.subTest(record=record):
                result = normalize_map([record])
                self.assertTrue(result['holds'])
                self.assertIsNone(result['points'][0]['function_code'])
                self.assertEqual((), compile_read_plan(result['points']).requests)

    def test_qualified_prose_is_not_silently_shortened_into_access(self):
        for literal in ('Read/write as configured', 'Read/write (see notes)', 'Not Read/write'):
            with self.subTest(literal=literal):
                result = normalize_map([self.record(literal)])
                self.assertIn('point.access-unrecognized', {item['code'] for item in result['holds']})
                self.assertIsNone(result['points'][0]['access'])

    def test_recognized_access_does_not_supply_missing_engineering_facts(self):
        result = normalize_map([{'name': 'Synthetic setting', 'address': 12, 'access': 'Read/write'}])
        point = result['points'][0]
        self.assertEqual('read-write', point['access'])
        for field in ('area', 'protocol_offset', 'unit_id', 'datatype', 'word_span', 'byte_order'):
            self.assertIsNone(point[field], field)
        self.assertEqual('pending', point['normalization_status'])
        self.assertTrue(result['holds'])


if __name__ == '__main__':
    unittest.main()
