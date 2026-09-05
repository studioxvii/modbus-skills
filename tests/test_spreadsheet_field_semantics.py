from pathlib import Path
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.parsers import parse_csv, parse_json, parse_xlsx
from modbus_skills.map_workflows import normalize_map
from tests.test_xlsx_header_coverage import workbook


class SpreadsheetFieldSemanticsTests(unittest.TestCase):
    def test_xlsx_fields_have_the_same_source_preserving_meaning(self):
        parsed = parse_xlsx(workbook([([
            ['Name', 'MB Address', 'Acquisuite MB Address', 'Type', 'Slope', 'R/W'],
            ['Pump power', 40104, 40105, 'F32', 0, 'R'],
            ['Pump state', 1, 1, 'U16', 0, 'R'],
        ], set())]))
        normalized = normalize_map(parsed)
        self.assertEqual(2, len(normalized['points']))
        for row, point, datatype, width in zip(parsed['records'], normalized['points'], ('float32', 'uint16'), (2, 1)):
            self.assertEqual(datatype, point['datatype'])
            self.assertEqual(width, point['word_span'])
            self.assertEqual(0, point['scale'])
            self.assertEqual('read-only', point['access'])
            self.assertEqual('unknown', point['source_address']['convention'])
            self.assertIsNone(point['protocol_offset'])
            self.assertEqual(row['_source'], point['source_location'])

    def test_mb_and_tool_addresses_are_raw_not_an_implicit_notation(self):
        parsed = parse_csv('Name,MB Address,Acquisuite MB Address,Type,Slope,R/W\nPump power,40104,40105,F32,0,R\n')
        row = parsed['records'][0]
        self.assertEqual('40104', str(row['address']))
        self.assertEqual('40105', str(row['address_2']))
        self.assertNotIn('display_address', row)
        point = normalize_map(parsed)['points'][0]
        self.assertEqual('40104', str(point['source_address']['raw']))
        self.assertEqual('unknown', point['source_address']['convention'])
        self.assertIsNone(point['protocol_offset'])
        self.assertIsNone(point['area'])
        self.assertEqual('40105', str(point['unmapped_fields']['address_2']))

    def test_either_address_header_alone_stays_parseable_and_held(self):
        for header in ('MB Address', 'Acquisuite MB Address'):
            for address in (0, 1, 40001, 65535):
                with self.subTest(header=header, address=address):
                    parsed = parse_csv(f'Name,{header},Type\nPump,{address},U16\n')
                    point = normalize_map(parsed)['points'][0]
                    self.assertEqual(str(address), str(point['source_address']['raw']))
                    self.assertEqual('unknown', point['source_address']['convention'])
                    self.assertIsNone(point['protocol_offset'])

    def test_explicit_notation_and_protocol_headers_keep_their_semantics(self):
        for header, address, convention, offset in (
            ('Protocol Offset', 40001, 'protocol-offset', 40001),
            ('Holding Register # (1 indexed)', 40001, 'modicon-reference', 0),
        ):
            parsed = parse_csv(f'Name,{header},Area,Type\nPump,{address},holding-register,U16\n')
            point = normalize_map(parsed)['points'][0]
            self.assertEqual(convention, point['source_address']['convention'])
            self.assertEqual(offset, point['protocol_offset'])
        parsed = parse_csv('Name,MB Address,Area,Address Convention,Type\nPump,17,holding-register,protocol-offset,U16\n')
        self.assertEqual(17, normalize_map(parsed)['points'][0]['protocol_offset'])

    def test_f32_preserves_width_and_requires_multiword_layout(self):
        for label in ('F32', 'f32', ' F32 '):
            parsed = parse_json(json.dumps([{'Name': 'Pump', 'MB Address': 17, 'Type': label}]))
            normalized = normalize_map(parsed)
            point = normalized['points'][0]
            self.assertEqual('float32', point['datatype'])
            self.assertEqual(2, point['word_span'])
            self.assertIsNone(point['byte_order'])
            self.assertIn('point.byte-order-unresolved', {h['code'] for h in normalized['holds']})

    def test_slope_zero_negative_and_fractional_values_are_not_defaults(self):
        for value in ('0', '-2', '0.125'):
            parsed = parse_csv(f'Name,MB Address,Type,Slope\nPump,17,U16,{value}\n')
            point = normalize_map(parsed, defaults={'scale': 1})['points'][0]
            self.assertEqual(float(value), point['scale'])
            evidence = next(e for e in point['source_evidence'] if e['field'] == 'scale')
            self.assertEqual('scale', evidence['source_field'])
            self.assertEqual(float(value), float(evidence['source_value']))

    def test_unknown_type_and_invalid_slope_remain_held(self):
        normalized = normalize_map(parse_csv('Name,MB Address,Type,Slope\nPump,17,F32-custom,NaN\n'))
        point = normalized['points'][0]
        self.assertIsNone(point['datatype'])
        self.assertIsNone(point['scale'])
        self.assertTrue(any(h.get('field') == 'scale' for h in normalized['holds']))
        self.assertTrue(any(h.get('field') == 'datatype' for h in normalized['holds']))


if __name__ == '__main__':
    unittest.main()
