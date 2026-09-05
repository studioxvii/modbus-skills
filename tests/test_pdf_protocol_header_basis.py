"""An explicit protocol header takes precedence over numeric display heuristics."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills.map_workflows import normalize_map
from modbus_skills.pdf_extraction import _reconcile
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence, prepare_pdf_records
from test_pdf_explicit_width_bias import readers, table, write_pdf


class PdfProtocolHeaderBasisTests(unittest.TestCase):
    def test_explicit_basis_never_infers_area_or_subtracts_display_prefix(self):
        for address, expected in [('30001', 30001), ('40001', 40001), ('10001', 10001),
                                  ('00001', 1), ('40001*', 40001),
                                  ('29999/30000', 29999), ('40001/40002', 40001),
                                  ('0x9C41/0x9C42', 40001)]:
            for method, rows in readers(table(address=address)).items():
                with self.subTest(address=address, method=method):
                    self.assertEqual(1, len(rows))
                    row = rows[0]
                    self.assertEqual(address, row['source_register'])
                    self.assertEqual('protocol-offset', row['address_convention'])
                    self.assertNotIn('display_address', row)
                    self.assertIsNone(row.get('area'))
                    self.assertEqual(expected, row['address_number'])
                    result = normalize_map(prepare_pdf_records({'records': [row]}), defaults={
                        'area': 'input-register', 'function_code': 4,
                        'unit_id': 7, 'route_id': 'synthetic-route'})
                    self.assertFalse(result['holds'])
                    self.assertEqual(expected, result['points'][0]['protocol_offset'])
                    self.assertEqual('input-register', result['points'][0]['area'])

    def test_all_three_readers_reconcile_same_five_digit_pair_without_conflict(self):
        parsed = readers(table(address='40001/40002'))
        accepted, held, conflicts = _reconcile(parsed['layout'], parsed['bbox'])
        self.assertFalse(held)
        self.assertFalse(conflicts)
        accepted, held, conflicts = _reconcile(accepted, parsed['grid'])
        self.assertEqual(1, len(accepted))
        self.assertFalse(held)
        self.assertFalse(conflicts)
        self.assertNotIn('display_address', accepted[0])

    def test_generic_display_and_bare_offset_headers_keep_existing_meaning(self):
        for header, convention in [('Register', 'modicon-reference'), ('Offset', 'unknown')]:
            cells = table(address='40001')
            cells[0][0] = header
            for method, rows in readers(cells).items():
                with self.subTest(header=header, method=method):
                    self.assertEqual(convention, rows[0]['address_convention'])

    def test_explicit_register_area_is_not_overridden_by_digits(self):
        cells = [['Protocol Offset', 'Name', 'Datatype', 'Area', 'Access'],
                 ['40001', 'Sample Counter', 'uint16', 'input-register', 'R']]
        evidence = parse_pdf_table_evidence(cells, page_number=1, table_index=0)
        self.assertFalse(evidence['quarantined_records'])
        self.assertEqual('input-register', evidence['records'][0]['area'])
        self.assertEqual('protocol-offset', evidence['records'][0]['address_convention'])
        self.assertEqual('40001', evidence['records'][0]['source_register'])

    def test_display_prefix_under_protocol_header_is_not_silently_reinterpreted(self):
        cells = table(address='4x0001')
        evidence = parse_pdf_table_evidence(cells, page_number=1, table_index=0)
        self.assertFalse(evidence['records'])
        self.assertTrue(evidence['quarantined_records'])

    @unittest.skipUnless(shutil.which('pdftotext') and importlib.util.find_spec('pdfplumber'), 'PDF tools unavailable')
    def test_actual_compile_keeps_five_digit_offset_and_source_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder/'explicit.pdf'
            write_pdf(source, table(address='40001/40002'))
            request = {'schema_version': 'modbus-compile-request/v1',
                       'source': {'path': str(source), 'format': 'pdf', 'defaults': {
                           'area': 'input-register', 'function_code': 4,
                           'unit_id': 7, 'route_id': 'synthetic-route'}},
                       'selection_template': {'schema_version': 'modbus-user-selection-template/v1',
                           'mode': 'all-readable', 'requested_measurements': ['all documented Modbus read points']},
                       'targets': [], 'target_options': {}}
            request_path = folder/'request.json'
            request_path.write_text(json.dumps(request))
            output = folder/'compiled'
            command = [sys.executable, str(ROOT/'plugins/modbus-skills/skills/compile-user-map/scripts/run.py'),
                       '--request', str(request_path), '--output', str(output)]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('offline-complete', json.loads(result.stdout)['status'])
            user = json.loads((output/'output/user-map.json').read_text())
            self.assertFalse(user['holds'])
            point = user['points'][0]
            self.assertEqual(40001, point['protocol_offset'])
            self.assertEqual(2, point['word_span'])
            self.assertEqual('input-register', point['area'])
            self.assertEqual('ABCD', point['byte_order'])
            oem = json.loads((output/'artifacts/oem-map.json').read_text())['points'][0]
            self.assertEqual('40001/40002', oem['source_register'])
            evidence = next(e for e in oem['source_field_evidence'] if e['field']=='protocol_offset')
            self.assertEqual('40001/40002', evidence['raw_value'])
            self.assertEqual(40001, evidence['normalized_value'])


if __name__ == '__main__':
    unittest.main()
