"""Only typed new evidence-budget errors must survive all grid fallbacks."""
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf
from modbus_skills import pdf_table_extraction as grid
from test_pdf_merged_cell_proof import draw_pdf


def error():
    cls = getattr(grid, 'PdfTableEvidenceBudgetError', grid.PdfTableExtractionError)
    if cls is grid.PdfTableExtractionError:
        return cls('merged-cell evidence budget exceeded while indexing geometry')
    return cls('merged-cell evidence budget exceeded while indexing geometry', page=1, table_index=0, stage='indexing')


class PdfGridBudgetPropagationTests(unittest.TestCase):
    def assert_budget_hold(self, result, rows=2):
        self.assertEqual(len(result['records']), rows)
        holds = [hold for hold in result['holds'] if hold['code'] == 'pdf-grid-evidence-budget']
        self.assertEqual(len(holds), 1)
        self.assertTrue(holds[0]['blocking'])
        self.assertEqual(holds[0]['source_scope'], {'pages': [1], 'table_index': 0})
        self.assertEqual(result['status'], 'held')
        self.assertFalse(result['source_coverage']['discovery_complete'])
        self.assertNotEqual(result['source_coverage']['status'], 'complete')
        self.assertNotIn('merged_cell_evidence', json.dumps(result))

    def test_mixed_text_keeps_literal_rows_with_localized_budget_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'; draw_pdf(source, merged=True)
            before = source.read_bytes()
            with mock.patch.object(pdf, '_recover_grid_rows', side_effect=error()):
                result = pdf.extract_pdf(source, before)
            self.assert_budget_hold(result)
            self.assertEqual(source.read_bytes(), before)

    def test_chunked_text_keeps_rows_and_marks_evidence_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.pdf'; draw_pdf(source, merged=True)
            with mock.patch.object(pdf, '_recover_grid_rows', side_effect=error()):
                result = pdf._extract_large_pdf_in_chunks(source, source.read_bytes(), executable=shutil.which('pdftotext'), capability={})
            self.assert_budget_hold(result)

    def test_kept_layout_fallback_retains_rows_rejections_and_budget_scope(self):
        row = {'source_register': '0', 'name': 'Synthetic quantity', '_source': {'page': 1, 'row': 1, 'region': 'p1:t0:r1', 'parser_id': 'layout'}}
        rejection = {'code': 'original-rejection', 'page': 1}
        with mock.patch.object(pdf, '_recover_grid_rows', side_effect=error()):
            result = pdf._recover_grid_or(Path('synthetic.pdf'), b'public', page_range=(1, 1), fallback={}, keep_rows=[row], keep_rejected=[rejection])
        self.assert_budget_hold(result, rows=1)
        self.assertEqual(result['records'], [row])
        self.assertEqual(result['rejected_rows'], [rejection])

    def test_no_kept_rows_preserves_existing_fallback_hold_plus_budget(self):
        fallback = pdf._hold_result(Path('synthetic.pdf'), b'public', 'original-error', 'Original failure.', page_range=(1, 1))
        before = json.dumps(fallback, sort_keys=True)
        with mock.patch.object(pdf, '_recover_grid_rows', side_effect=error()):
            result = pdf._recover_grid_or(Path('synthetic.pdf'), b'public', page_range=(1, 1), fallback=fallback)
        self.assert_budget_hold(result, rows=0)
        self.assertIn('original-error', [hold['code'] for hold in result['holds']])
        self.assertEqual(json.dumps(fallback, sort_keys=True), before)

    def test_typed_worker_error_round_trip_and_no_string_based_promotion(self):
        stderr = io.StringIO()
        with mock.patch.object(grid, '_extract_pdf_table_rows_in_process', side_effect=error()), mock.patch.object(grid.sys, 'stderr', stderr):
            rc = grid._worker_main(['grid', '--worker', 'synthetic.pdf'])
        self.assertEqual(rc, 3)
        def process(_argv, **kwargs):
            kwargs['stderr'].write(stderr.getvalue().encode())
            return SimpleNamespace(wait=lambda **_kwargs: rc)
        with mock.patch.object(grid.subprocess, 'Popen', side_effect=process):
            with self.assertRaises(grid.PdfTableEvidenceBudgetError) as caught:
                grid._run_grid_worker(Path('synthetic.pdf'), [1], 60)
        self.assertEqual((caught.exception.page, caught.exception.table_index, caught.exception.stage), (1, 0, 'indexing'))
        # A generic error containing the same words must remain generic.
        def generic(_argv, **kwargs):
            kwargs['stderr'].write(b'merged-cell evidence budget unrelated generic failure')
            return SimpleNamespace(wait=lambda **_kwargs: 1)
        with mock.patch.object(grid.subprocess, 'Popen', side_effect=generic):
            with self.assertRaises(grid.PdfTableExtractionError) as caught:
                grid._run_grid_worker(Path('synthetic.pdf'), [1], 60)
        self.assertIs(type(caught.exception), grid.PdfTableExtractionError)

    def test_unrelated_generic_grid_error_keeps_previous_behavior(self):
        row = {'source_register': '0', 'name': 'Synthetic quantity', '_source': {'page': 1, 'region': 'p1:t0:r1'}}
        with mock.patch.object(pdf, '_recover_grid_rows', side_effect=grid.PdfTableExtractionError('unrelated unavailable geometry')):
            result = pdf._recover_grid_or(Path('synthetic.pdf'), b'public', page_range=(1, 1), fallback={}, keep_rows=[row])
        self.assertEqual(result['records'], [row])
        self.assertNotIn('pdf-grid-evidence-budget', [hold['code'] for hold in result['holds']])


if __name__ == '__main__': unittest.main()
