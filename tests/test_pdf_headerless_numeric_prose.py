"""A datatype word does not make an incidental prose number a register column."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.pdf_extraction import extract_pdf, parse_layout_rows


class HeaderlessNumericProseTests(unittest.TestCase):
    def test_prose_numbers_are_localized_exceptions_not_points(self):
        for line in ('The device returns IEEE 754 Float values.',
                     'Scale the raw Float value by dividing by 1000.',
                     'Read 200 to obtain a Single Float constant.'):
            for parser in ('pdftotext-layout/v1', 'external-ocr-layout/v1'):
                with self.subTest(line=line, parser=parser):
                    rows, rejected = parse_layout_rows(line, first_page=7, parser_id=parser)
                    self.assertEqual([], rows)
                    self.assertEqual(1, len(rejected))
                    self.assertEqual('pdf-headerless-column-roles-unresolved', rejected[0]['code'])
                    self.assertEqual({'page': 7, 'line': 1}, {k: rejected[0][k] for k in ('page', 'line')})
                    self.assertEqual(line, rejected[0]['_source']['excerpt'])
                    self.assertNotIn('source_register', rejected[0])

    def test_ambiguous_name_first_single_spacing_preserves_evidence(self):
        line = 'Synthetic temperature 123 float32'
        rows, rejected = parse_layout_rows(line)
        self.assertEqual([], rows)
        self.assertEqual(line, rejected[0]['_source']['excerpt'])

    def test_address_leading_and_real_column_separators_remain_candidates(self):
        for line in ('123 Synthetic temperature float32',
                     'Synthetic temperature  123  float32',
                     'Synthetic temperature | 123 | float32',
                     '40056  16-bit int  Synthetic flow'):
            with self.subTest(line=line):
                rows, rejected = parse_layout_rows(line)
                self.assertEqual(1, len(rows))
                self.assertEqual([], rejected)

    def test_explicit_name_first_header_takes_precedence(self):
        rows, rejected = parse_layout_rows(
            'Name                    Protocol Offset    Data Type\n'
            'Synthetic temperature   123                float32')
        self.assertEqual(1, len(rows))
        self.assertEqual(123, rows[0]['address_number'])
        self.assertEqual('Synthetic temperature', rows[0]['name'])
        self.assertEqual([], rejected)

    def test_name_first_pipe_without_datatype_does_not_gain_new_inference(self):
        rows, _rejected = parse_layout_rows('Synthetic flow | 123 | 456')
        self.assertEqual([], rows)

    def test_mixed_page_preserves_valid_row_and_localizes_ambiguous_line(self):
        rows, rejected = parse_layout_rows('The device returns IEEE 754 Float values.\n'
                                           '40056  16-bit int  Synthetic flow')
        self.assertEqual(['40056'], [row['source_register'] for row in rows])
        self.assertEqual([1], [row['line'] for row in rejected])

    def test_ocr_envelope_keeps_uncertainty_without_false_registers(self):
        import hashlib
        data = b'%PDF synthetic source identity'
        source_hash = hashlib.sha256(data).hexdigest()
        evidence = {'schema_version': 'modbus-ocr-evidence/v1', 'artifact_type': 'modbus-ocr-evidence',
                    'input_hashes': {'source_pdf': source_hash}, 'source_sha256': source_hash,
                    'assumptions': [], 'findings': [], 'holds': [],
                    'tool': {'name': 'synthetic-ocr-fixture', 'version': '1'},
                    'pages': [{'page_index': 1, 'text': 'The device returns IEEE 754 Float values.'}]}
        result = extract_pdf(Path('synthetic.pdf'), data, page_range=(1, 1), ocr_evidence=evidence)
        self.assertEqual([], result['records'])
        self.assertEqual(1, len(result['rejected_rows']))
        self.assertTrue(any(hold.get('blocking') for hold in result['holds']))
        self.assertNotEqual('complete', result['source_coverage']['status'])


if __name__ == '__main__':
    unittest.main()
