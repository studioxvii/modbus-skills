"""Scoped display exclusions and honest page-text evidence; synthetic only."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf

LCD = '10 Operation of LCD Display\nMain Screen (Press ENTER) -> Menu -> Live values (Press ENTER)'
SAMPLE = 'Output[W]     1200     1200     1200'
TABLE = 'Offset   Name          Data Type   Access\n17       Screen power  uint16      R'


class PdfLcdSourceCoverageTests(unittest.TestCase):
    def test_lcd_sample_and_cross_diagram_rejected_with_context_identity(self):
        cross = 'InputC   420.0   7.0   2900        Output[W]   1200   1200   1200'
        for sample in (SAMPLE, cross):
            rows, rejected = pdf.parse_layout_rows(LCD + '\n' + sample, first_page=7)
            self.assertEqual([], rows)
            evidence = [row for row in rejected if row.get('code') == 'pdf-lcd-display-example']
            self.assertEqual(1, len(evidence))
            self.assertEqual('p7:l3', evidence[0]['_source']['region'])
            self.assertEqual(sample, evidence[0]['_source']['excerpt'])
            self.assertTrue(evidence[0]['_source']['context_refs'])
            self.assertEqual([], pdf.discover_register_pages(LCD + '\n' + sample))

    def test_missing_affirmative_pair_and_unscoped_values_remain_untouched(self):
        for intro in ('LCD power', 'Operation of LCD Display', 'Press ENTER to continue'):
            self.assertFalse(any(pdf._lcd_context_mask((intro + '\n' + SAMPLE).splitlines())))
        for address in ('0000', '1200', '2900'):
            rows, _ = pdf.parse_layout_rows(TABLE.replace('17 ', address + ' '))
            self.assertEqual([address], [row['source_register'] for row in rows])

    def test_explicit_headers_and_supported_headerless_forms_win(self):
        for suffix in (TABLE, '40056  uint16  Pump power', '400056  uint16  LCD power'):
            plain = pdf.parse_layout_rows(suffix)[0]
            self.assertEqual(1, len(plain))
            prefixed = pdf.parse_layout_rows(LCD + '\n' + SAMPLE + '\n' + suffix)[0]
            for key in ('name', 'source_register', 'datatype'):
                self.assertEqual(plain[0][key], prefixed[0][key])
        rows, _ = pdf.parse_layout_rows(TABLE + '\n' + LCD + '\n' + SAMPLE)
        self.assertEqual(['Screen power'], [row['name'] for row in rows])

    def test_context_does_not_cross_page_register_heading_or_bounded_pair(self):
        for separator in ('\f', '\nRegisters\n', '\n3. Input Registers\n'):
            rows, _ = pdf.parse_layout_rows(LCD + '\n' + SAMPLE + separator + '40056  uint16  Pump power')
            self.assertEqual(['40056'], [row['source_register'] for row in rows])
        for between in ('Registers', '\n'.join(['Unrelated explanatory text'] * 9)):
            text = 'Operation of LCD Display\n' + between + '\nPress ENTER to continue\n' + SAMPLE
            self.assertFalse(any(pdf._lcd_context_mask(text.splitlines())))

    def test_excluded_discovery_page_still_retains_scoped_rejection(self):
        rows, rejected = pdf.parse_layout_rows(LCD + '\n' + SAMPLE, pages=set())
        self.assertEqual([], rows)
        self.assertEqual(['pdf-lcd-display-example'], [r['code'] for r in rejected])

    def test_inventory_preserves_unicode_text_and_compact_physical_ranges(self):
        evidence = pdf._page_text_evidence('Header\f\n\f～ ℃\f\f中文\f123\f', first_page=66)
        self.assertEqual(6, evidence['pages_seen'])
        self.assertEqual([[67, 69]], evidence['no_alphanumeric_text_ranges'])
        self.assertEqual([[68, 68]], evidence['symbols_only_ranges'])
        self.assertEqual('unassessed', evidence['visual_content'])
        self.assertIsNone(pdf._page_text_evidence('Text\f中文\f123\f', first_page=1))
        self.assertEqual([[2, 2]], pdf._page_text_evidence('Text\f\f', first_page=1)['no_alphanumeric_text_ranges'])

    def test_inventory_does_not_create_a_new_hold_on_successful_rows(self):
        result = {'records': [{'name': 'Example'}], 'holds': [], 'source_coverage': {'status': 'complete'}}
        self.assertIs(result, pdf._with_page_text_evidence(result, None))
        evidence = pdf._page_text_evidence('\f', first_page=1)
        updated = pdf._with_page_text_evidence(result, evidence)
        self.assertEqual([], updated['holds'])
        self.assertEqual('complete', updated['source_coverage']['status'])
        self.assertIs(evidence, updated['source_coverage']['page_text_evidence'])

    def test_failed_full_document_retains_lcd_rejections_and_one_existing_hold(self):
        layout = ('Front matter\f\n\f～\f' + LCD + '\n' + SAMPLE + '\f').encode()
        with patch.object(pdf, '_preflight', return_value=('pdftotext', {}, None)), \
             patch.object(pdf, '_call', return_value=subprocess.CompletedProcess([], 0, layout, b'')) as call, \
             patch.object(pdf, '_recover_grid_rows', side_effect=pdf.PdfTableExtractionError('No structured rows')):
            result = pdf.extract_pdf(Path('synthetic.pdf'), b'synthetic')
        self.assertEqual(1, call.call_count)
        self.assertEqual([], result['records'])
        self.assertEqual(1, len(result['holds']))
        self.assertEqual('pdf-register-pages-unavailable', result['holds'][0]['code'])
        self.assertEqual([[2, 3]], result['source_coverage']['page_text_evidence']['no_alphanumeric_text_ranges'])
        self.assertEqual(['pdf-lcd-display-example'], [r['code'] for r in result['rejected_rows']])
        self.assertEqual('held', result['status'])


if __name__ == '__main__':
    unittest.main()
