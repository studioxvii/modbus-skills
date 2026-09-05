"""Multiple display-like numbers cannot assign headerless address roles by magnitude."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.pdf_extraction import parse_layout_rows


class CompetingNumericAddressTests(unittest.TestCase):
    def test_competing_display_numbers_are_one_localized_exception(self):
        for left, right in ((40002, 38400), (40002, 45000), (30002, 45000), (45000, 40002)):
            for suffix in ('uint16', 'Read/write'):
                with self.subTest(left=left, right=right, suffix=suffix):
                    text = f'{left}  Synthetic setting  {right}  {suffix}'
                    rows, rejected = parse_layout_rows(text, first_page=3)
                    self.assertEqual([], rows)
                    self.assertEqual(1, len(rejected))
                    self.assertEqual('pdf-headerless-address-roles-unresolved', rejected[0]['code'])
                    self.assertEqual((3, 1), (rejected[0]['page'], rejected[0]['line']))
                    self.assertEqual(text, rejected[0]['_source']['excerpt'])
                    self.assertNotIn('source_register', rejected[0])
                    self.assertNotIn('protocol_offset', rejected[0])

    def test_parser_method_and_source_location_are_retained_for_ocr(self):
        text = '40002  Synthetic setting  38400  uint16'
        rows, rejected = parse_layout_rows(text, first_page=4, parser_id='external-ocr-layout/v1')
        self.assertEqual([], rows)
        self.assertEqual('ocr-derived', rejected[0]['_source']['method'])
        self.assertEqual('external-ocr-layout/v1', rejected[0]['parser_id'])
        self.assertEqual('p4:l1', rejected[0]['_source']['region'])

    def test_explicit_address_column_still_governs_other_numeric_cells(self):
        text = ('Holding Address  Name                   Datatype  Factory Setting\n'
                '40002            Synthetic setting      uint16    38400')
        rows, rejected = parse_layout_rows(text)
        self.assertEqual(1, len(rows))
        self.assertEqual('40002', rows[0]['source_register'])
        self.assertEqual([], rejected)

    def test_single_display_address_and_ordinary_number_remain_unchanged(self):
        for text in ('40056  16-bit int  Synthetic flow',
                     '40056  Synthetic flow  uint16  100',
                     '123  Synthetic flow  float32'):
            with self.subTest(text=text):
                rows, rejected = parse_layout_rows(text)
                self.assertEqual(1, len(rows))
                self.assertEqual(text.split()[0], rows[0]['source_register'])
                self.assertEqual([], rejected)

    def test_repeated_same_display_literal_is_not_a_different_address(self):
        rows, rejected = parse_layout_rows('40002  Synthetic setting  40002  uint16')
        self.assertEqual(1, len(rows))
        self.assertEqual('40002', rows[0]['source_register'])
        self.assertEqual([], rejected)

    def test_mixed_valid_and_ambiguous_rows_do_not_hide_each_other(self):
        rows, rejected = parse_layout_rows('40056  uint16  Synthetic flow\n'
                                           '40002  Synthetic setting  38400  uint16')
        self.assertEqual(['40056'], [row['source_register'] for row in rows])
        self.assertEqual(1, len(rejected))
        self.assertEqual(2, rejected[0]['line'])


if __name__ == '__main__':
    unittest.main()
