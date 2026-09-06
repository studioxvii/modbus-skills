"""Zero-character geometry skips must not suppress independent text recovery."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import extract_pdf  # noqa: E402
from modbus_skills.pdf_table_extraction import _extract_pdf_table_rows_in_process  # noqa: E402


def synthetic_pdf(path, *, mixed=False, glyphs=True):
    """Small real PDF with a vector-only page and/or a genuine glyph table."""
    streams = []
    if mixed or not glyphs:
        streams.append(b"20 20 m 400 20 l S\n20 40 m 400 40 l S\n20 20 m 20 40 l S\n")
    if glyphs:
        commands = []
        for x in (20, 120, 300, 400):
            commands.append(f"{x} 150 m {x} 210 l S")
        for y in (150, 170, 190, 210):
            commands.append(f"20 {y} m 400 {y} l S")
        rows = [["Address", "Name", "Datatype"], ["40011", "Synthetic current", "UINT16"], ["40012", "Synthetic voltage", "UINT16"]]
        for ri, row in enumerate(rows):
            for x, value in zip((23, 123, 303), row):
                commands.append(f"BT /F1 8 Tf {x} {197-ri*20} Td ({value}) Tj ET")
        streams.append("\n".join(commands).encode())
    kids = " ".join(f"{4+2*i} 0 R" for i in range(len(streams)))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>".encode(), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, stream in enumerate(streams):
        objects.extend([f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 430 240] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+2*i} 0 R >>".encode(), f"<< /Length {len(stream)} >>\nstream\n".encode()+stream+b"\nendstream"])
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode()+obj+b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(output)


class ZeroGlyphGridTests(unittest.TestCase):
    def fake_document(self, pages):
        document = mock.MagicMock()
        def open_document(_path, **options):
            selected = options.get("pages")
            visible = []
            for number, page in enumerate(pages, 1):
                if selected is None or number in selected:
                    page.page_number = number
                    visible.append(page)
            document.__enter__.return_value.pages = visible
            return document
        return mock.patch.dict(sys.modules, {"pdfplumber": SimpleNamespace(open=open_document)})

    def test_zero_chars_skip_geometry_regardless_of_images(self):
        for images in ([], [object()]):
            with self.subTest(images=bool(images)):
                page = SimpleNamespace(chars=[], images=images, find_tables=mock.Mock(side_effect=AssertionError("no text to recover")))
                with self.fake_document([page]):
                    self.assertEqual({"records": [], "quarantined_records": []}, _extract_pdf_table_rows_in_process(Path("synthetic.pdf")))
                page.find_tables.assert_not_called()

    def test_glyph_bearing_page_still_uses_existing_header_recovery(self):
        table = object()
        page = SimpleNamespace(chars=[{"text": "A"}], images=[object()], find_tables=mock.Mock(return_value=[table]))
        cells = [["Address", "Name", "Datatype"], ["40011", "Synthetic current", "UINT16"]]
        with self.fake_document([page]), mock.patch("modbus_skills.pdf_table_extraction._recover_offset_header", return_value=(cells, None)) as recover:
            result = _extract_pdf_table_rows_in_process(Path("synthetic.pdf"))
        recover.assert_called_once_with(page, table)
        self.assertEqual("40011", result["records"][0]["source_register"])

    def test_unselected_page_is_not_inspected_for_characters(self):
        class Unselected:
            @property
            def chars(self):
                raise AssertionError("unselected page inspected")
        page = SimpleNamespace(chars=[], find_tables=mock.Mock(side_effect=AssertionError("geometry invoked")))
        with self.fake_document([Unselected(), page]):
            self.assertEqual([], _extract_pdf_table_rows_in_process(Path("synthetic.pdf"), pages=[2])["records"])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "pdfplumber is unavailable")
    def test_real_mixed_pages_preserve_physical_page_and_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mixed.pdf"
            synthetic_pdf(path, mixed=True)
            result = _extract_pdf_table_rows_in_process(path)
            self.assertEqual(["40011", "40012"], [r["source_register"] for r in result["records"]])
            self.assertTrue(all(r["_source"]["page"] == 2 for r in result["records"]))
            self.assertEqual(result, _extract_pdf_table_rows_in_process(path, pages=[2]))
            self.assertEqual([], _extract_pdf_table_rows_in_process(path, pages=[1])["records"])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "pdfplumber is unavailable")
    def test_empty_first_reader_does_not_skip_independent_glyph_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glyphs.pdf"
            synthetic_pdf(path)
            with mock.patch("modbus_skills.pdf_extraction._preflight", return_value=("pdftotext", {"name": "pdftotext", "version": "synthetic"}, None)), mock.patch("modbus_skills.pdf_extraction._call", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
                result = extract_pdf(path, path.read_bytes(), page_range=(1, 1))
            self.assertEqual(["40011", "40012"], [r["source_register"] for r in result["records"]])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "pdfplumber is unavailable")
    def test_zero_glyph_source_still_requires_ocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "vectors.pdf"
            synthetic_pdf(path, glyphs=False)
            with mock.patch("modbus_skills.pdf_extraction._preflight", return_value=("pdftotext", {"name": "pdftotext", "version": "synthetic"}, None)), mock.patch("modbus_skills.pdf_extraction._call", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
                result = extract_pdf(path, path.read_bytes(), page_range=(1, 1))
            self.assertEqual([], result["records"])
            self.assertEqual("held", result["status"])
            self.assertIn("pdf-ocr-required", [h["code"] for h in result["holds"]])


if __name__ == "__main__":
    unittest.main()
