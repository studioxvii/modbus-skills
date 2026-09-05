"""Prospective extra refusal controls for source-proved common cells."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.pdf_table_extraction import (
    PdfTableExtractionError, _extract_pdf_table_rows_in_process,
    extract_pdf_table_evidence,
)
from test_pdf_merged_cell_proof import draw_pdf, second


class PdfMergedCellLimitTests(unittest.TestCase):
    def test_incidence_cap_refuses_before_parser_association(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            before = source.read_bytes()
            with mock.patch('modbus_skills.pdf_table_extraction._MAX_MERGED_INDEX_INCIDENCES', 1), mock.patch('modbus_skills.pdf_table_extraction.parse_pdf_table_evidence') as parser:
                with self.assertRaisesRegex(PdfTableExtractionError, 'merged-cell evidence budget exceeded while indexing geometry'):
                    _extract_pdf_table_rows_in_process(source)
                parser.assert_not_called()
            self.assertEqual(source.read_bytes(), before)

    def test_duplicate_semantic_headers_refuse_common_cell_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'duplicate.pdf'
            draw_pdf(source, data=[['Protocol Offset', 'Name', 'Format', 'Units', 'Units'],
                                  ['0', 'First quantity', 'uint16', 'kg', 'kg'],
                                  ['1', 'Second quantity', 'uint16', '', '']],
                     merged_columns=[3, 4])
            before = source.read_bytes()
            evidence = extract_pdf_table_evidence(source)
            self.assertEqual(len(evidence['records']), 2)
            self.assertEqual(evidence['records'][0]['units'], 'kg')
            target = second(evidence['records'])
            self.assertIsNone(target.get('units'))
            self.assertFalse(any('merged_cell_evidence' in claim for claim in target['_claims']))
            self.assertEqual(source.read_bytes(), before)
            control = Path(temp) / 'unique.pdf'
            draw_pdf(control, merged=True)
            target = second(extract_pdf_table_evidence(control)['records'])
            self.assertEqual(target['units'], 'kg')
            self.assertTrue(any('merged_cell_evidence' in claim for claim in target['_claims']))


if __name__ == '__main__':
    unittest.main()
