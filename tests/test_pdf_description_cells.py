"""Description projection needs actual source cells, never suffix stripping."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'plugins/modbus-skills/runtime').exists())
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills.pdf_extraction import _envelope, _reconcile, extract_pdf
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence

HEADERS = ['Name', 'Description', 'Access', 'Input?', 'Output?', 'Config?', 'Offset']
CELLS = ['pump.count', 'Timer current counter', 'R', 'FALSE', 'FALSE', 'FALSE', '19']
SOURCE_HASH = 'a' * 64


def geometry(description='Timer current counter', access='R'):
    values = [list(HEADERS), [CELLS[0], description, access, *CELLS[3:]]]
    bounds = [0, 100, 400, 480, 560, 640, 720, 800]
    rows = [SimpleNamespace(cells=[(bounds[c], r * 20, bounds[c + 1], r * 20 + 20)
                                  for c in range(7)]) for r in range(2)]
    chars = []
    for r, values_row in enumerate(values):
        for c, text in enumerate(values_row):
            for i, char in enumerate(text):
                chars.append({'text': char, 'x0': bounds[c] + 2 + i * 3,
                              'x1': bounds[c] + 5 + i * 3, 'top': r * 20 + 3,
                              'bottom': r * 20 + 10, 'upright': True})
    return SimpleNamespace(chars=chars), SimpleNamespace(rows=rows), values


def candidates(description='Timer current counter', access='R', mutate=None):
    from modbus_skills.pdf_table_extraction import _description_access_cell_evidence
    page, table, values = geometry(description, access)
    right = parse_pdf_table_evidence(values, page_number=1, table_index=0)['records'][0]
    left = deepcopy(right)
    left['_source'] = {**left['_source'], 'parser_id': 'pdftotext-layout/v1', 'region': 'p1:l2'}
    left['description'] = f'{description} {access}'
    left['_claims'] = [{'parser_id': 'pdftotext-layout/v1', 'field': 'description',
                        'value': left['description'], 'source_locator': deepcopy(left['_source'])}]
    if mutate:
        mutate(page, table, values)
    proof = _description_access_cell_evidence(page, table, values, right, SOURCE_HASH)
    if proof is not None:
        next(c for c in right['_claims'] if c['field'] == 'description')['body_cell_evidence'] = proof
    return left, right


def write_pdf(path,cells,x):
    drawing=[]
    for ri,row in enumerate(cells):
        for ci,text in enumerate(row):
            escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
            drawing.append(f'BT /F1 8 Tf {x[ci]+3} {300-ri*20} Td ({escaped}) Tj ET')
    drawing.extend(f'{v} 270 m {v} 310 l S' for v in x)
    drawing.extend(f'{x[0]} {y} m {x[-1]} {y} l S' for y in (270,290,310))
    data=('\n'.join(drawing)+'\n').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>',
       b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>',
       b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 650 400] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>',
       b'<< /Length '+str(len(data)).encode()+b' >>\nstream\n'+data+b'endstream']
    output=bytearray(b'%PDF-1.4\n'); offsets=[]
    for number,obj in enumerate(objects,1):
        offsets.append(len(output));output.extend(f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start=len(output);output.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    with path.open('xb') as f:f.write(output)


class DescriptionCellTests(unittest.TestCase):
    def test_emission_removes_only_invocation_owned_unused_proof_without_mutation(self):
        _, fresh = candidates()
        imported = deepcopy(fresh)  # Same source hash/parser, different provenance lifetime.
        copied = deepcopy(fresh)
        proof = next(c['body_cell_evidence'] for c in fresh['_claims'] if 'body_cell_evidence' in c)
        before = deepcopy([fresh, imported, copied])
        emitted = _envelope(Path('sample.pdf'), b'sample', [fresh, imported, copied], [], [], [], None,
                            fresh_body_proofs={id(proof): proof})
        self.assertEqual(before, [fresh, imported, copied])
        self.assertFalse(any('body_cell_evidence' in c for c in emitted['records'][0]['_claims']))
        self.assertEqual(imported, emitted['records'][1])
        self.assertEqual(copied, emitted['records'][2])
        self.assertEqual([{k:v for k,v in c.items() if k!='body_cell_evidence'} for c in fresh['_claims']],
                         emitted['records'][0]['_claims'])

    def test_emission_keeps_one_full_projection_proof_and_all_raw_claims(self):
        left, right = candidates()
        proof = next(c['body_cell_evidence'] for c in right['_claims'] if 'body_cell_evidence' in c)
        accepted, _, _ = self.reconcile(left, right)
        before = deepcopy(accepted)
        emitted = _envelope(Path('sample.pdf'), b'sample', accepted, [], [], [], None,
                            fresh_body_proofs={id(proof): proof})
        self.assertEqual(before, accepted)
        full = [c for c in emitted['records'][0]['_claims'] if 'body_cell_evidence' in c]
        self.assertEqual(1, len(full))
        self.assertEqual('pdf-description-cell-projection/v1', full[0]['parser_id'])
        self.assertEqual(proof, full[0]['body_cell_evidence'])
        self.assertEqual(len(accepted[0]['_claims']), len(emitted['records'][0]['_claims']))

    def test_emission_retains_full_proof_for_all_quarantines_including_width_move(self):
        left, right = candidates()
        proof = next(c['body_cell_evidence'] for c in right['_claims'] if 'body_cell_evidence' in c)
        width = {**right, 'code': 'pdf-address-width-conflict'}
        held = {**right, 'code': 'pdf-prior-source-quarantine'}
        holds = [{'code':'synthetic-hold', 'severity':'hold', 'blocking':True, 'message':'Keep this exact hold.'}]
        before = _envelope(Path('sample.pdf'), b'sample', [width], [], [], holds, None, quarantined=[held])
        after = _envelope(Path('sample.pdf'), b'sample', [width], [], [], holds, None, quarantined=[held],
                          fresh_body_proofs={id(proof): proof})
        self.assertEqual(before, after)
        self.assertEqual(2, len(after['quarantined_records']))

    def test_ocr_path_does_not_register_or_strip_preexisting_evidence(self):
        _, imported = candidates()
        before = deepcopy(imported)
        with mock.patch('modbus_skills.pdf_extraction._ocr_rows', return_value=([imported], [], {'name':'synthetic','version':'1'})):
            result = extract_pdf(Path('ocr.pdf'), b'sample', page_range=(1,1), ocr_evidence={})
        self.assertEqual(before, imported)
        self.assertEqual(before, result['records'][0])
        self.assertIn('pdf-ocr-human-review-required', [h['code'] for h in result['holds']])

    def test_indexed_proof_equals_full_scan_including_boundary_glyphs(self):
        from modbus_skills.pdf_table_extraction import (
            _description_access_cell_evidence, _prepare_description_cell_geometry,
        )
        for variant in ('clean', 'horizontal_crossing', 'vertical_crossing', 'edge_touch'):
            with self.subTest(variant=variant):
                page, table, values = geometry()
                row = parse_pdf_table_evidence(values, page_number=1, table_index=0)['records'][0]
                if variant != 'clean':
                    box = {'horizontal_crossing': (399, 23, 402, 30),
                           'vertical_crossing': (102, 18, 105, 24),
                           'edge_touch': (102, 15, 105, 20)}[variant]
                    page.chars.append({'text': 'X', 'x0': box[0], 'top': box[1],
                                       'x1': box[2], 'bottom': box[3]})
                prepared = _prepare_description_cell_geometry(page, table, values)
                self.assertIn('glyphs_by_row', prepared)
                full = _description_access_cell_evidence(page, table, values, row, SOURCE_HASH)
                indexed = _description_access_cell_evidence(page, table, values, row, SOURCE_HASH,
                                                            geometry=prepared)
                self.assertEqual(full, indexed)
                self.assertEqual(indexed, _description_access_cell_evidence(
                    page, table, values, row, SOURCE_HASH, geometry=prepared))

    def test_ambiguous_row_bands_retain_the_full_scan(self):
        from modbus_skills.pdf_table_extraction import (
            _description_access_cell_evidence, _prepare_description_cell_geometry,
        )
        for variant in ('overlap', 'empty', 'nonfinite'):
            with self.subTest(variant=variant):
                page, table, values = geometry()
                row = parse_pdf_table_evidence(values, page_number=1, table_index=0)['records'][0]
                if variant == 'overlap':
                    table.rows[0].cells[0] = (0, 0, 100, 25)
                elif variant == 'empty':
                    table.rows.append(SimpleNamespace(cells=[None] * 7))
                else:
                    table.rows[0].cells[0] = (0, 0, 100, float('inf'))
                prepared = _prepare_description_cell_geometry(page, table, values)
                self.assertNotIn('glyphs_by_row', prepared)
                self.assertEqual(_description_access_cell_evidence(page, table, values, row, SOURCE_HASH),
                                 _description_access_cell_evidence(page, table, values, row, SOURCE_HASH,
                                                                   geometry=prepared))

    def test_table_row_geometry_is_materialized_only_once_per_preparation(self):
        from modbus_skills.pdf_table_extraction import (
            _description_access_cell_evidence, _prepare_description_cell_geometry,
        )
        page, table, values = geometry()
        class CountedTable:
            calls = 0
            @property
            def rows(self):
                self.calls += 1
                return table.rows
        counted = CountedTable()
        prepared = _prepare_description_cell_geometry(page, counted, values)
        row = parse_pdf_table_evidence(values, page_number=1, table_index=0)['records'][0]
        for _ in range(3):
            self.assertIsNotNone(_description_access_cell_evidence(
                page, counted, values, row, SOURCE_HASH, geometry=prepared))
        self.assertEqual(1, counted.calls)

    def reconcile(self, left, right, **kwargs):
        return _reconcile([left], [right], source_sha256=SOURCE_HASH, **kwargs)

    def test_proved_adjacent_cells_correct_only_selected_description(self):
        left, right = candidates()
        original = deepcopy((left, right))
        accepted, held, conflicts = self.reconcile(left, right)
        self.assertEqual(([], []), (held, conflicts))
        self.assertEqual(CELLS[1], accepted[0]['description'])
        self.assertEqual(original, (left, right))
        self.assertEqual(left['_claims'] + right['_claims'], accepted[0]['_claims'][:-1])
        self.assertEqual('pdf-description-cell-projection/v1', accepted[0]['_claims'][-1]['parser_id'])
        for key in set(left) - {'description', '_claims'}:
            self.assertEqual(left[key], accepted[0][key], key)

    def test_literal_trailing_r_and_access_like_description_are_preserved(self):
        for literal in ('Counter R', 'R RW W', 'Access R', 'Read R/W'):
            with self.subTest(literal=literal):
                left, right = candidates(literal)
                self.assertEqual(literal, self.reconcile(left, right)[0][0]['description'])
                left['description'] = literal
                self.assertEqual(literal, self.reconcile(left, right)[0][0]['description'])

    def test_missing_proof_or_different_source_hash_cannot_correct(self):
        left, right = candidates()
        for value in (None, 'b' * 64):
            accepted, _, _ = _reconcile([left], [right], source_sha256=value)
            self.assertEqual(left['description'], accepted[0]['description'])
        for claim in right['_claims']:
            claim.pop('body_cell_evidence', None)
        self.assertEqual(left['description'], self.reconcile(left, right)[0][0]['description'])

    def test_missing_merged_or_ambiguous_geometry_does_not_license_correction(self):
        def missing(page, table, values):
            table.rows[1].cells[1] = None
        def merged(page, table, values):
            table.rows[1].cells[1] = (100, 20, 480, 40)
        def header_missing(page, table, values):
            table.rows[0].cells[1] = None
        def duplicate_header(page, table, values):
            values[0][3] = 'Description'
        def boundary_glyph(page, table, values):
            page.chars.append({'text': 'R', 'x0': 399, 'x1': 402, 'top': 23, 'bottom': 30})
        def duplicate_glyph(page, table, values):
            char = next(c for c in page.chars if c['x0'] == 102 and c['top'] == 23)
            page.chars.append(deepcopy(char))
        def text_disagrees_with_glyphs(page, table, values):
            values[1][1] = 'Different cell text'
        for mutate in (missing, merged, header_missing, duplicate_header, boundary_glyph,
                       duplicate_glyph, text_disagrees_with_glyphs):
            with self.subTest(control=mutate.__name__):
                left, right = candidates(mutate=mutate)
                self.assertEqual(left['description'], self.reconcile(left, right)[0][0]['description'])

    def test_genuine_description_conflict_and_non_access_suffix_are_unchanged(self):
        left, right = candidates()
        for text in ('An unrelated description R', 'Timer current counter RW', 'Timer current counter R R'):
            left['description'] = text
            self.assertEqual(text, self.reconcile(left, right)[0][0]['description'])
        left, right = candidates(access='R or W')
        self.assertEqual(left['description'], self.reconcile(left, right)[0][0]['description'])

    def test_prior_quarantine_and_material_conflicts_remain_unchanged(self):
        left, right = candidates()
        accepted, held, _ = _reconcile([], [right], quarantined_records=[left], source_sha256=SOURCE_HASH)
        self.assertFalse(accepted)
        self.assertEqual(left['description'], held[0]['description'])
        right['name'] = 'different.point'
        accepted, held, conflicts = self.reconcile(left, right)
        self.assertFalse(accepted)
        self.assertEqual(left['description'], held[0]['description'])
        self.assertIn('name', [c['field'] for c in conflicts[0]['fields']])

    def test_later_prior_locator_quarantine_also_prevents_description_projection(self):
        left, right = candidates()
        previous = deepcopy(left)
        previous['source_register'] = previous['source_address']['raw'] = '20'
        previous['name'] = 'separate.held.claim'
        accepted, held, _ = _reconcile([left], [right], quarantined_records=[previous],
                                       source_sha256=SOURCE_HASH)
        self.assertFalse(accepted)
        self.assertEqual(2, len(held))
        self.assertTrue(all(r['description'] == left['description'] for r in held))
        proof = next(c['body_cell_evidence'] for c in right['_claims'] if 'body_cell_evidence' in c)
        emitted = _envelope(Path('sample.pdf'), b'sample', accepted, [], [], [], None,
                            quarantined=held, fresh_body_proofs={id(proof): proof})
        self.assertEqual(held, emitted['quarantined_records'])

    def test_duplicate_physical_rows_and_offsets_do_not_license_projection(self):
        left, right = candidates()
        for duplicate in ('left', 'right'):
            accepted, held, _ = _reconcile([left, deepcopy(left)] if duplicate == 'left' else [left],
                [right, deepcopy(right)] if duplicate == 'right' else [right], source_sha256=SOURCE_HASH)
            self.assertFalse(any(c.get('parser_id') == 'pdf-description-cell-projection/v1'
                                 for r in accepted + held for c in r.get('_claims', [])))
        other = deepcopy(right)
        other['_source']['region'] = 'p1:t1:r1'
        other['name'] = 'other.point'
        accepted, held, _ = _reconcile([left], [right, other], source_sha256=SOURCE_HASH)
        self.assertFalse(any(c.get('parser_id') == 'pdf-description-cell-projection/v1'
                             for r in accepted + held for c in r.get('_claims', [])))

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'),
                         'PDF readers unavailable')
    def test_real_pdf_join_is_corrected_from_its_own_cells_with_original_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'description.pdf'
            write_pdf(path, [HEADERS, CELLS], [25,132,239,275,315,355,395,445])
            result = extract_pdf(path, path.read_bytes())
        self.assertEqual(1, len(result['records']))
        self.assertEqual([], result['quarantined_records'])
        row = result['records'][0]
        self.assertEqual(CELLS[1], row['description'])
        self.assertEqual('R', row['access'])
        self.assertEqual('unknown', row['address_convention'])
        self.assertEqual('19', row['source_offset'])
        descriptions = [claim for claim in row['_claims'] if claim['field'] == 'description']
        self.assertIn('Timer current counter R', [claim['value'] for claim in descriptions])
        projection = next(c for c in descriptions if c['parser_id'] == 'pdf-description-cell-projection/v1')
        self.assertEqual(1, sum('body_cell_evidence' in c for c in row['_claims']))
        self.assertEqual(result['input_hashes']['source'], projection['body_cell_evidence']['source_sha256'])
        self.assertEqual(['Timer current counter', 'R'],
                         [c['raw_value'] for c in projection['body_cell_evidence']['cells']])

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'),
                         'PDF readers unavailable')
    def test_real_pdf_joined_name_remains_quarantined(self):
        cells = ['pump.status.long.name', 'Pump latch', 'RW', 'FALSE', 'FALSE', 'TRUE', '17']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'name.pdf'
            write_pdf(path, [HEADERS, cells], [25,132,350,390,430,470,510,560])
            result = extract_pdf(path, path.read_bytes())
        self.assertEqual([], result['records'])
        self.assertEqual(1, len(result['quarantined_records']))
        self.assertEqual('pump.status.long.name Pump latch', result['quarantined_records'][0]['description'])
        self.assertFalse(any(c['parser_id'] == 'pdf-description-cell-projection/v1'
                             for c in result['quarantined_records'][0]['_claims']))

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'),
                         'PDF readers unavailable')
    def test_real_agreeing_readers_keep_raw_claims_without_unused_full_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'agree.pdf'
            write_pdf(path, [HEADERS, CELLS], [25,132,350,390,430,470,510,560])
            result = extract_pdf(path, path.read_bytes())
        self.assertEqual(1, len(result['records']))
        row = result['records'][0]
        self.assertEqual(CELLS[1], row['description'])
        self.assertFalse(any('body_cell_evidence' in c for c in row['_claims']))
        self.assertTrue(any(c['parser_id']=='pdfplumber-table/v1' and c['field']=='description'
                            and c['raw_value']==CELLS[1] and c['source_locator']['region']=='p1:t0:r1'
                            for c in row['_claims']))

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'),
                         'PDF readers unavailable')
    def test_grid_only_and_coordinate_failure_paths_do_not_emit_unused_fresh_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fallback.pdf'
            write_pdf(path, [HEADERS, CELLS], [25,132,239,275,315,355,395,445])
            source = path.read_bytes()
            layout = subprocess.check_output(['pdftotext','-layout',str(path),'-'], timeout=15)
            for mode in ('no_executable', 'empty_text', 'no_discovery', 'coordinate_failure', 'no_text_rows'):
                with self.subTest(mode=mode):
                    capability = {'name':'pdftotext', 'version':'synthetic'}
                    preflight = (None, None, {'status':'held'}) if mode=='no_executable' else ('pdftotext', capability, None)
                    responses = ([subprocess.CompletedProcess([],0,b'',b'')] if mode=='empty_text' else
                                 [subprocess.CompletedProcess([],0,b'Introduction',b'')] if mode=='no_discovery' else
                                 [subprocess.CompletedProcess([],0,layout,b''), subprocess.CompletedProcess([],1 if mode=='coordinate_failure' else 0,b'<doc/>',b'')])
                    with mock.patch('modbus_skills.pdf_extraction._preflight', return_value=preflight), \
                         mock.patch('modbus_skills.pdf_extraction._call', side_effect=responses):
                        if mode=='no_text_rows':
                            with mock.patch('modbus_skills.pdf_extraction.parse_layout_rows', return_value=([],[])):
                                result = extract_pdf(path, source)
                        else:
                            result = extract_pdf(path, source)
                    self.assertEqual(1, len(result['records']))
                    self.assertEqual(CELLS[1], result['records'][0]['description'])
                    self.assertFalse(any('body_cell_evidence' in c for c in result['records'][0]['_claims']))

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'),
                         'PDF readers unavailable')
    def test_chunk_emission_keeps_used_proof_only_and_is_deterministic(self):
        from modbus_skills.pdf_extraction import _extract_large_pdf_in_chunks
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'chunk.pdf'
            write_pdf(path, [HEADERS, CELLS], [25,132,239,275,315,355,395,445])
            source = path.read_bytes()
            layout = subprocess.check_output(['pdftotext','-layout',str(path),'-'], timeout=15)
            results = []
            for _ in range(2):
                with mock.patch('modbus_skills.pdf_extraction._call', side_effect=[
                    subprocess.CompletedProcess([],0,layout,b''), subprocess.CompletedProcess([],1,b'',b'')]):
                    results.append(_extract_large_pdf_in_chunks(path, source, executable='pdftotext',
                                                                 capability={'name':'pdftotext','version':'synthetic'}))
            self.assertEqual(results[0], results[1])
            self.assertEqual(CELLS[1], results[0]['records'][0]['description'])
            self.assertEqual(1, sum('body_cell_evidence' in c for c in results[0]['records'][0]['_claims']))


if __name__ == '__main__':
    unittest.main()
