"""A literal area-bearing address header is not an address-basis approval."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from tests.test_literal_modbus_headers import encoded, parse
from tests.test_literal_source_context import bundle
from modbus_skills import parsers
from modbus_skills.map_workflows import normalize_map
from modbus_skills.source_intake import compile_source_descriptor

class HoldingRegisterHeaderAreaTests(unittest.TestCase):
    def test_exact_header_preserves_area_and_unknown_engineering(self):
        for kind in ('csv','xlsx'):
            for header in ('Holding Register',' Holding   Registers '):
                with self.subTest(kind=kind,header=header):
                    parsed = parse(kind,[[header,'Name','R/W'],[123,'Reading','R']])
                    point, = normalize_map(parsed)['points']
                    self.assertEqual('holding-register',point['area'])
                    self.assertEqual(3,point['function_code'])
                    self.assertIsNone(point['protocol_offset'])
                    for field in ('datatype','word_span','byte_order','engineering_unit','scale','engineering_offset'):
                        self.assertIsNone(point[field])
                    claim, = parsed['records'][0]['_claims']
                    self.assertEqual(header,claim['raw_value'])
                    self.assertEqual(1,claim['source_locator']['row'])
                    self.assertEqual(1,claim['source_locator']['column'])
                    self.assertEqual('123' if kind=='csv' else 123,parsed['records'][0]['address'])

    def test_generic_numeric_and_prose_headers_do_not_establish_area(self):
        for header in ('Register','Address','Holding Register or Input Register','Holding Register (example)'):
            parsed = parse('csv',[[header,'Name'],[40001,'Reading']])
            self.assertTrue(all(r.get('area') is None for r in parsed['records']))

    def test_explicit_other_area_is_preserved_and_conflict_held(self):
        for kind in ('csv','xlsx'):
            parsed = parse(kind,[['Holding Register','Name','Area','Access'],[1,'Reading','Input Registers','R']])
            self.assertEqual('Input Registers',parsed['records'][0]['area'])
            self.assertIn('source.area-columns-conflict',{h['code'] for h in parsed['source_holds']})
            self.assertIn('source.area-columns-conflict',{h['code'] for h in normalize_map(parsed)['holds']})

    def test_write_only_remains_write_only_without_read_function(self):
        for kind in ('csv','xlsx'):
            point, = normalize_map(parse(kind,[['Holding Register','Name','R/W'],[1,'Command','W']]))['points']
            self.assertEqual('holding-register',point['area'])
            self.assertEqual('write-only',point['access'])
            self.assertIsNone(point['function_code'])
            self.assertIsNone(point['protocol_offset'])

    def test_oem_header_evidence_keeps_actual_header_row_and_source_literal(self):
        for kind in ('csv','xlsx'):
            with tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/('public.'+kind)
                path.write_bytes(encoded(kind,[['Holding Register','Name','R/W'],[1,'Reading','R']]))
                oem,_=compile_source_descriptor({'path':str(path)})
            point,=oem['points']
            proof,=[e for e in point['source_field_evidence'] if e['field']=='area']
            self.assertEqual('Holding Register',proof['raw_value'])
            self.assertEqual('holding-register',proof['normalized_value'])
            self.assertEqual('confirmed',proof['status'])
            self.assertIn('row:1' if kind=='xlsx' else 'csv:1',proof['source_ref'])
            self.assertEqual('holding-register',bundle(oem)['user_map']['points'][0]['area'])

if __name__ == '__main__': unittest.main()
