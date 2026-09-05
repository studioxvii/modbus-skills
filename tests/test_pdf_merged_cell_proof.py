"""Prospective public contracts: never infer a merged cell from None alone."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.pdf_table_extraction import (
    PdfTableExtractionError,
    prepare_pdf_records,
    _extract_pdf_table_rows_in_process,
    extract_pdf_table_evidence,
    parse_pdf_table_evidence,
)


HEADERS = ['Protocol Offset', 'Name', 'Format', 'R/W', 'Units']


def draw_pdf(path, *, merged=False, second_unit='', header=True, data=None, merged_columns=None):
    cuts = [20, 115, 225, 290, 335, 395]
    table = data or [HEADERS if header else ['Plain', 'unlabelled', 'cells', '', ''],
                     ['0', 'First quantity', 'uint16', 'R', 'kg'],
                     ['1', 'Second quantity', 'uint16', 'R', second_unit]]
    height = 80 + 40 * len(table)
    levels = list(range(height, 79, -40))
    commands = ['0.4 w']
    for x in cuts:
        commands.append(f'{x} 80 m {x} {height} l S')
    merged_columns = set(merged_columns or ([4] if merged else []))
    for y in levels:
        for column in range(5):
            if y not in levels[2:-1] or column not in merged_columns:
                commands.append(f'{cuts[column]} {y} m {cuts[column+1]} {y} l S')
    for row, cells in enumerate(table):
        for column, value in enumerate(cells):
            if value:
                text = value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                commands.append(f'BT /F1 8 Tf {cuts[column]+4} {levels[row]-14} Td ({text}) Tj ET')
    stream = '\n'.join(commands).encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 {height+40}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'.encode(), b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    positions = []
    for index, obj in enumerate(objects, 1):
        positions.append(len(data))
        data.extend(f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for position in positions:
        data.extend(f'{position:010} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    path.write_bytes(data)


def second(records):
    return next(row for row in records if row['source_register'] == '1')


class PdfMergedCellProofTests(unittest.TestCase):
    def test_explicit_empty_text_cells_never_forward_fill(self):
        parsed = parse_pdf_table_evidence([
            HEADERS,
            ['0', 'First quantity', 'uint16', 'R', 'kg'],
            ['1', 'Second quantity', 'uint16', 'R', ''],
        ], page_number=1, table_index=0)
        self.assertIsNone(second(parsed['records']).get('units'))

    def test_none_without_physical_geometry_never_forward_fills(self):
        parsed = parse_pdf_table_evidence([
            HEADERS,
            ['0', 'First quantity', 'uint16', 'R', 'kg'],
            ['1', 'Second quantity', 'uint16', 'R', None],
        ], page_number=1, table_index=0)
        self.assertIsNone(second(parsed['records']).get('units'))

    def test_explicit_blank_cannot_be_bridged_by_later_none(self):
        parsed = parse_pdf_table_evidence([
            HEADERS,
            ['0', 'First quantity', 'uint16', 'R', 'kg'],
            ['1', 'Second quantity', 'uint16', 'R', ''],
            ['2', 'Third quantity', 'uint16', 'R', None],
        ], page_number=1, table_index=0)
        self.assertTrue(all(row.get('units') is None for row in parsed['records'][1:]))

    def test_actual_drawn_empty_rectangle_stays_empty(self):
        with tempfile.TemporaryDirectory(prefix='modbus-blank-proof-') as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source)
            before = source.read_bytes()
            parsed = extract_pdf_table_evidence(source)
            self.assertEqual(len(parsed['records']), 2)
            self.assertIsNone(second(parsed['records']).get('units'))
            self.assertEqual(source.read_bytes(), before)

    def test_actual_spanning_cell_requires_complete_source_and_target_proof(self):
        with tempfile.TemporaryDirectory(prefix='modbus-merged-proof-') as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            before = source.read_bytes()
            parsed = extract_pdf_table_evidence(source)
            row = second(parsed['records'])
            self.assertEqual(row['units'], 'kg')
            claim = next(c for c in row['_claims'] if c['field'] == 'units')
            self.assertIn('merged_cell_evidence', claim)
            proof = claim['merged_cell_evidence']
            self.assertEqual(proof['method'], 'same-source-spanning-cell/v1')
            self.assertEqual(proof['source_sha256'], sha256(before).hexdigest())
            self.assertEqual(proof['source_locator'], {'page': 1, 'row': 1, 'region': 'p1:t0:r1'})
            self.assertEqual(proof['target_locator'], {'page': 1, 'row': 2, 'region': 'p1:t0:r2'})
            self.assertEqual(proof['source_cell_bbox'], [335.0, 80.0, 395.0, 160.0])
            self.assertEqual(proof['target_row_band'], [120.0, 160.0])
            self.assertEqual(proof['raw_value'], 'kg')
            self.assertTrue(proof['glyphs'])
            self.assertEqual(claim['source_locator'], proof['source_locator'])
            self.assertEqual(row['_source']['region'], 'p1:t0:r2')
            self.assertEqual(extract_pdf_table_evidence(source), parsed)
            self.assertEqual(source.read_bytes(), before)

    def test_distinct_later_literal_and_headerless_controls(self):
        with tempfile.TemporaryDirectory(prefix='modbus-merge-controls-') as temp:
            source = Path(temp) / 'literal.pdf'
            draw_pdf(source, second_unit='m')
            row = second(extract_pdf_table_evidence(source)['records'])
            self.assertEqual(row['units'], 'm')
            self.assertFalse(any('merged_cell_evidence' in c for c in row['_claims']))
            other = Path(temp) / 'headerless.pdf'
            draw_pdf(other, merged=True, header=False)
            self.assertEqual(extract_pdf_table_evidence(other)['records'], [])

    @contextmanager
    def altered_geometry(self, source, *, mode):
        # Keep real synthetic bytes/cells/glyphs, alter ONLY the selected reader
        # evidence to prove missing/ambiguous metadata is never an authority.
        import pdfplumber
        with pdfplumber.open(source) as document:
            original = document.pages[0]
            table = original.find_tables()[0]
            cells = table.extract()
            rows = [SimpleNamespace(cells=list(row.cells), bbox=row.bbox) for row in table.rows]
            chars = deepcopy(original.chars)
            rectangles = list(table.cells)
            edges = deepcopy(original.edges)
            if mode == 'missing_source_rectangle':
                rows[1].cells[4] = None
                rectangles = [box for box in rectangles if box != table.rows[1].cells[4]]
            elif mode == 'duplicate_physical_row':
                rows[2] = SimpleNamespace(cells=list(rows[1].cells), bbox=rows[1].bbox)
            elif mode == 'no_matching_body_glyph':
                chars = [char for char in chars if not (335 <= char['x0'] <= 395 and 80 <= char['top'] <= 160)]
            elif mode == 'crossing_boundary_glyph':
                next(c for c in chars if c['text'] == 'k' and c['x0'] > 335)['x0'] = 330
            elif mode == 'interior_separator':
                edges.append({'x0': 335, 'x1': 395, 'top': 120, 'bottom': 120, 'orientation': 'h'})
            elif mode == 'contradictory_target_literal':
                cells[2][4] = 'm'
            elif mode == 'count_visits':
                class Counted(list):
                    visits = 0
                    def __iter__(self):
                        for value in super().__iter__():
                            self.visits += 1
                            yield value
                chars = Counted(chars)
            else:
                raise AssertionError(mode)
            fake_table = SimpleNamespace(rows=rows, cells=rectangles, bbox=table.bbox, extract=lambda: cells)
            fake_page = SimpleNamespace(chars=chars, edges=edges, find_tables=lambda: [fake_table])
            fake_document = SimpleNamespace(pages=[fake_page], stream=BytesIO(source.read_bytes()))
            manager = mock.MagicMock()
            manager.__enter__.return_value = fake_document
            with mock.patch('pdfplumber.open', return_value=manager):
                yield chars

    def test_missing_ambiguous_or_unproved_glyph_geometry_never_carries(self):
        with tempfile.TemporaryDirectory(prefix='modbus-merge-evidence-') as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            for mode in ['missing_source_rectangle', 'duplicate_physical_row', 'no_matching_body_glyph']:
                with self.subTest(mode=mode), self.altered_geometry(source, mode=mode):
                    parsed = _extract_pdf_table_rows_in_process(source)
                    self.assertIsNone(second(parsed['records']).get('units'))

    def test_crossing_glyph_and_interior_separator_refuse_common_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            for mode in ['crossing_boundary_glyph', 'interior_separator']:
                with self.subTest(mode=mode), self.altered_geometry(source, mode=mode):
                    self.assertIsNone(second(_extract_pdf_table_rows_in_process(source)['records']).get('units'))

    def test_contradictory_target_literal_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            with self.altered_geometry(source, mode='contradictory_target_literal'):
                row = second(_extract_pdf_table_rows_in_process(source)['records'])
                self.assertEqual(row['units'], 'm')
                self.assertFalse(any('merged_cell_evidence' in c for c in row['_claims']))

    def test_spoofed_raw_evidence_is_not_merge_authority(self):
        table = [HEADERS + ['merged_cell_evidence'],
                 ['0', 'First quantity', 'uint16', 'R', 'kg', ''],
                 ['1', 'Second quantity', 'uint16', 'R', None,
                  {'method': 'same-source-spanning-cell/v1', 'raw_value': 'kg'}]]
        self.assertIsNone(second(parse_pdf_table_evidence(table, page_number=1, table_index=0)['records']).get('units'))

    def test_proof_budget_fails_before_parser_association(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            with mock.patch('modbus_skills.pdf_table_extraction._MAX_MERGED_PROOF_BYTES', 1, create=True), mock.patch('modbus_skills.pdf_table_extraction.parse_pdf_table_evidence') as parser:
                with self.assertRaisesRegex(PdfTableExtractionError, 'merged-cell evidence budget'):
                    _extract_pdf_table_rows_in_process(source)
                parser.assert_not_called()

    def test_glyph_index_has_finite_full_page_visit_count(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, merged=True)
            with self.altered_geometry(source, mode='count_visits') as chars:
                row = second(_extract_pdf_table_rows_in_process(source)['records'])
                self.assertEqual(row['units'], 'kg')
                self.assertLessEqual(chars.visits, 3 * len(chars))

    def test_long_common_cell_does_not_rescan_page_per_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, data=[HEADERS] + [[str(n), f'Quantity {n}', 'uint16', 'R', 'kg' if n == 0 else ''] for n in range(40)], merged_columns=[4])
            with self.altered_geometry(source, mode='count_visits') as chars:
                parsed = _extract_pdf_table_rows_in_process(source)
                self.assertEqual(len(parsed['records']), 40)
                self.assertTrue(all(row['units'] == 'kg' for row in parsed['records']))
                self.assertLessEqual(chars.visits, 3 * len(chars))

    def test_actual_merged_ulong_msr_lsr_preserves_pair_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'
            draw_pdf(source, data=[['REG.', 'Description', 'Format', 'R/W', 'Units'],
                                  ['001', 'Energy (MSR)', 'ULong', 'R', 'kWh'],
                                  ['002', 'Energy (LSR)', '', '', '']], merged_columns=[2, 3, 4])
            evidence = extract_pdf_table_evidence(source)
            second_row = evidence['records'][1]
            self.assertEqual(second_row['format'], 'ULong')
            self.assertTrue(all('merged_cell_evidence' in c for c in second_row['_claims'] if c['field'] in ('format', 'access', 'units')))
            prepared = prepare_pdf_records(evidence)['records']
            self.assertEqual(len(prepared), 1)
            self.assertEqual(prepared[0]['source_register'], '001/002')
            self.assertNotIn('protocol_offset', prepared[0])
            self.assertEqual((prepared[0]['datatype'], prepared[0]['word_count'], prepared[0]['byte_order']), ('uint32', 2, 'ABCD'))


if __name__ == '__main__':
    unittest.main()
