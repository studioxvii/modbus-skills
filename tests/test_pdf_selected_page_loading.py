"""Selected grid pages keep physical identities without loading every page."""
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_table_extraction import _extract_pdf_table_rows_in_process
from test_pdf_zero_glyph_grid import synthetic_pdf


class SelectedPageLoadingTests(unittest.TestCase):
    def test_selected_late_page_matches_complete_document_evidence(self):
        import pdfplumber
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pdf"
            synthetic_pdf(path, mixed=True)
            complete = _extract_pdf_table_rows_in_process(path)
            with mock.patch("pdfplumber.open", wraps=pdfplumber.open) as opened:
                selected = _extract_pdf_table_rows_in_process(path, pages=[2])
            opened.assert_called_once_with(path, pages=[2])
            self.assertEqual(complete, selected)
            self.assertTrue(selected["records"])
            self.assertTrue(all(r["_source"]["page"] == 2 for r in selected["records"]))

    def test_selected_empty_page_does_not_acquire_other_page_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pdf"
            synthetic_pdf(path, mixed=True)
            self.assertEqual({"records": [], "quarantined_records": []},
                             _extract_pdf_table_rows_in_process(path, pages=[1]))

    def test_unselected_mode_keeps_original_reader_call(self):
        import pdfplumber
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.pdf"
            synthetic_pdf(path)
            with mock.patch("pdfplumber.open", wraps=pdfplumber.open) as opened:
                _extract_pdf_table_rows_in_process(path)
            opened.assert_called_once_with(path)
