"""Public behavioral controls for exact PDF access-header recognition."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))

from modbus_skills.pdf_extraction import _layout_field
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence


def make_pdf(path, table):
    widths = [105, 130, 250, 110]
    cuts = [20]
    for width in widths:
        cuts.append(cuts[-1] + width)
    top, row_height = 170, 45
    commands = ['0.4 w']
    for x in cuts:
        commands.append(f'{x} {top-row_height*len(table)} m {x} {top} l S')
    for row in range(len(table) + 1):
        y = top - row_height * row
        commands.append(f'{cuts[0]} {y} m {cuts[-1]} {y} l S')
    for row_index, row in enumerate(table):
        for column, value in enumerate(row):
            for line_index, line in enumerate(value.split('\n')):
                escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                commands.append(f'BT /F1 8 Tf {cuts[column]+4} {top-row_height*row_index-13-line_index*10} Td ({escaped}) Tj ET')
    stream = '\n'.join(commands).encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 640 220] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        data.extend(f'{offset:010} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(data)


class PdfReadWriteHeaderTests(unittest.TestCase):
    def parse(self, header='Read/Write', access='Read only'):
        return parse_pdf_table_evidence([
            ['Holding Address', 'Parameter name', 'Parameter Details', header],
            ['40011', 'Tank quantity', 'Measured tank quantity', access],
        ], page_number=2, table_index=3)

    def test_exact_slash_whitespace_variants_grid_and_layout(self):
        for header in ['Read/Write', 'Read/\nWrite', 'Read / Write', 'READ /\tWRITE:', 'Read\n/\nWrite']:
            with self.subTest(header=header):
                self.assertEqual(_layout_field(header, 3), 'access')
                parsed = self.parse(header)
                self.assertEqual(parsed['quarantined_records'], [])
                self.assertEqual(len(parsed['records']), 1)
                row = parsed['records'][0]
                self.assertEqual(row['source_register'], '40011')
                self.assertEqual(row['name'], 'Tank quantity')
                self.assertEqual(row['description'], 'Measured tank quantity')
                self.assertEqual(row['access'], 'Read only')
                for field in ['datatype', 'format', 'word_count', 'byte_order', 'protocol_offset', 'unit_id']:
                    self.assertNotIn(field, row)

    def test_original_header_and_access_claim_remain_literal(self):
        parsed = self.parse('Read/\nWrite', 'Read/write')
        self.assertEqual(len(parsed['records']), 1)
        row = parsed['records'][0]
        claims = [claim for claim in row['_claims'] if claim['field'] == 'access']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['raw_header'], 'Read/ Write')
        self.assertEqual(claims[0]['raw_value'], 'Read/write')
        self.assertEqual(claims[0]['value'], 'Read/write')
        self.assertEqual(claims[0]['column_index'], 3)
        self.assertEqual(claims[0]['source_locator'], {'page': 2, 'row': 1, 'region': 'p2:t3:r1'})

    def test_prose_and_reversed_headers_are_not_access(self):
        for header in ['Read/Write policy', 'Allowed Read/Write operations', 'Write/Read', 'Read Write', 'Read/Write (see manual)']:
            with self.subTest(header=header):
                self.assertTrue(_layout_field(header, 3).startswith('_extra:'))
                parsed = self.parse(header)
                self.assertEqual(parsed['records'], [])
                self.assertEqual(len(parsed['quarantined_records']), 1)
                self.assertEqual(parsed['quarantined_records'][0]['code'], 'pdf-grid-type-unresolved')

    def test_conflicting_duplicate_access_headers_stay_quarantined(self):
        parsed = parse_pdf_table_evidence([
            ['Holding Address', 'Name', 'Read/\nWrite', 'R/W'],
            ['40011', 'Tank quantity', 'R', 'W'],
        ], page_number=1, table_index=0)
        self.assertEqual(parsed['records'], [])
        self.assertEqual(len(parsed['quarantined_records']), 1)
        row = parsed['quarantined_records'][0]
        self.assertEqual(row['code'], 'pdf-grid-column-ambiguous')
        self.assertIn('access', row['fields'])
        self.assertIn('R | W', row['_source']['excerpt'])

    def test_no_header_context_is_not_invented(self):
        parsed = parse_pdf_table_evidence([
            ['40011', 'Tank quantity', 'Read only'],
            ['40012', 'Next quantity', 'Read/write'],
        ], page_number=2, table_index=0)
        self.assertEqual(parsed, {'records': [], 'quarantined_records': []})

    def test_existing_short_access_alias_unchanged(self):
        old = self.parse('R/W')
        new = self.parse('Read/Write')
        self.assertEqual(len(old['records']), 1)
        self.assertEqual(len(new['records']), 1)
        self.assertEqual(old['records'][0]['access'], new['records'][0]['access'])

    def test_field_roles_prevent_name_datatype_guess(self):
        parsed = parse_pdf_table_evidence([
            ['Holding Address', 'Name', 'Description', 'Read/Write'],
            ['40011', 'Float phase', 'A mode with bit labels in its description', 'Read only'],
        ], page_number=1, table_index=0)
        self.assertEqual(len(parsed['records']), 1)
        row = parsed['records'][0]
        self.assertEqual(row['name'], 'Float phase')
        self.assertEqual(row['description'], 'A mode with bit labels in its description')
        self.assertNotIn('format', row)
        self.assertNotIn('datatype', row)

    def test_extra_columns_and_scaling_keep_source_roles(self):
        parsed = parse_pdf_table_evidence([
            ['Holding Address', 'Name', 'Description', 'Factory Setting', 'Range', 'Scale factor', 'Unit', 'Notes', 'Read/\nWrite'],
            ['40011', 'Tank quantity', 'Measured tank quantity', '12', '0-200', '0.1', 'kg', 'Model option only', 'Read/write'],
        ], page_number=1, table_index=0)
        self.assertEqual(len(parsed['records']), 1)
        row = parsed['records'][0]
        self.assertEqual(row['access'], 'Read/write')
        self.assertEqual(row['scale'], '0.1')
        self.assertEqual(row['units'], 'kg')
        self.assertEqual(row['_extra']['Factory Setting'], '12')
        self.assertEqual(row['_extra']['Notes'], 'Model option only')

    def test_documented_pdf_entrypoint_retains_literal_access(self):
        # Uses the currently imported module's own plugin, never a live-tree copy.
        import modbus_skills.pdf_extraction as extraction
        plugin = Path(extraction.__file__).resolve().parents[2]
        for header in ['Read/Write', 'Read/\nWrite', 'R/W']:
            with self.subTest(header=header), tempfile.TemporaryDirectory(prefix='modbus-header-test-') as temp:
                root = Path(temp)
                source = root / 'source.pdf'
                make_pdf(source, [['Holding\nAddress', 'Parameter name', 'Parameter Details', header], ['40011', 'Tank quantity', 'Measured tank quantity', 'Read only']])
                result = subprocess.run([sys.executable, str(plugin / 'skills/extract-pdf-map/scripts/run.py'), '--input', str(source), '--output', str(root / 'out')], capture_output=True, timeout=30, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                parsed = json.loads((root / 'out/pdf-extraction.json').read_text())
                self.assertEqual(parsed['quarantined_records'], [])
                self.assertEqual(len(parsed['records']), 1)
                row = parsed['records'][0]
                self.assertEqual(row['name'], 'Tank quantity')
                self.assertEqual(row['description'], 'Measured tank quantity')
                self.assertEqual(row['access'], 'Read only')
                self.assertNotIn('datatype', row)


if __name__ == '__main__':
    unittest.main()
