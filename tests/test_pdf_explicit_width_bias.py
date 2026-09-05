"""Public synthetic checks for explicit PDF table widths and engineering bias."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import _envelope, _reconcile, extract_pdf, parse_bbox_rows, parse_layout_rows  # noqa: E402
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence  # noqa: E402


def table(width_header="Words", width="2", bias="-3", address="98"):
    return [["Protocol Offset", "Name", "Datatype", width_header, "Byte Order", "Scale", "Engineering Offset", "Access"],
            [address, "Sample Flow", "float32", width, "ABCD", ".25", bias, "R"]]


def readers(cells):
    layout, rejected = parse_layout_rows("\n".join("".join(cell.ljust(24) for cell in row).rstrip() for row in cells))
    assert not rejected, rejected
    doc = ET.Element("doc")
    page = ET.SubElement(doc, "page")
    for row_index, row in enumerate(cells):
        for column_index, cell in enumerate(row):
            if cell:
                x, y = 10+column_index*120, 10+row_index*30
                ET.SubElement(page, "word", xMin=str(x), xMax=str(x+len(cell)*4), yMin=str(y), yMax=str(y+9)).text = cell
    coordinate = parse_bbox_rows(ET.tostring(doc, encoding="unicode"))
    grid = parse_pdf_table_evidence(cells, page_number=1, table_index=0)
    return {"layout": layout, "bbox": coordinate, "grid": [*grid["records"], *grid["quarantined_records"]]}


def write_pdf(path, cells):
    """Small valid drawn-table PDF, synthesized without third-party writers."""
    x = [25, 118, 255, 336, 405, 490, 545, 672, 805]
    lines = []
    for ri, row in enumerate(cells):
        for ci, text in enumerate(row):
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"BT /F1 8 Tf {x[ci]+3} {450-ri*35} Td ({escaped}) Tj ET")
    bottom = 440-(len(cells)-1)*35
    for pos in x:
        lines.append(f"{pos} {bottom} m {pos} 470 l S")
    for ri in range(len(cells)+1):
        y = 470 if ri == 0 else 440-(ri-1)*35
        lines.append(f"25 {y} m 805 {y} l S")
    data = ("\n".join(lines)+"\n").encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
               b"<< /Length "+str(len(data)).encode()+b" >>\nstream\n"+data+b"endstream"]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode()+obj+b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(output)


class PdfExplicitWidthBiasTests(unittest.TestCase):
    def test_words_and_word_count_are_literal_fields_in_all_readers(self):
        for header in ("Words", "Word Count"):
            for reader, rows in readers(table(header, width="02", bias="-3.50")).items():
                with self.subTest(header=header, reader=reader):
                    self.assertEqual(1, len(rows))
                    row = rows[0]
                    self.assertEqual("02", row["word_count"])
                    self.assertEqual("-3.50", row["engineering_offset"])
                    self.assertEqual("ABCD", row["byte_order"])
                    claims = row["_claims"]
                    self.assertTrue(any(c.get("field") == "word_count" and c.get("value") == "02" for c in claims))
                    self.assertTrue(any(c.get("field") == "engineering_offset" and c.get("value") == "-3.50" for c in claims))

    def test_absent_width_retains_existing_default_not_datatype_inference(self):
        cells = table()
        for row in cells:
            del row[3]
        for reader, rows in readers(cells).items():
            with self.subTest(reader=reader):
                self.assertEqual(1, rows[0]["word_count"])
                self.assertIn(rows[0].get("datatype", rows[0].get("format")), {"float32"})

    def test_unknown_width_header_does_not_gain_a_width(self):
        for reader, rows in readers(table("Unspecified", width="7")).items():
            with self.subTest(reader=reader):
                self.assertEqual(1, rows[0]["word_count"])

    def test_bare_offset_is_not_engineering_bias_or_address_approval(self):
        cells = [["Offset", "Name", "Datatype", "Words", "Engineering Offset", "Access"],
                 ["40104", "Sample Flow", "float32", "2", "-3", "R"]]
        for reader, rows in readers(cells).items():
            with self.subTest(reader=reader):
                self.assertEqual("40104", rows[0]["source_register"])
                self.assertEqual("unknown", rows[0]["address_convention"])
                self.assertNotIn("protocol_offset", rows[0])
                self.assertEqual("-3", rows[0]["engineering_offset"])

    def test_true_pair_mismatch_is_quarantined_with_both_raw_claims(self):
        for width in ("3", "3.0", "-1", "1.5"):
            for reader, rows in readers(table(width=width, address="0x62/0x63")).items():
                with self.subTest(reader=reader, width=width):
                    self.assertEqual(width, rows[0]["word_count"])
                    self.assertEqual(2, rows[0]["address_parse"]["word_count"])
                    envelope = _envelope(Path("synthetic.pdf"), b"synthetic", rows, [], [], [], (1, 1), discovered_pages=[1])
                    self.assertFalse(envelope["records"])
                    self.assertEqual("held", envelope["status"])
                    self.assertTrue(envelope["quarantined_records"])
                    self.assertIn("pdf-address-width-conflict", {h["code"] for h in envelope["holds"]})

    def test_true_pair_consistent_width_remains_usable(self):
        for reader, rows in readers(table(width="2", address="0x62/0x63")).items():
            with self.subTest(reader=reader):
                self.assertEqual("2", rows[0]["word_count"])
                self.assertNotEqual("pdf-address-width-conflict", rows[0].get("code"))

    def test_blank_grid_cells_do_not_inherit_width_or_bias(self):
        cells = table()
        cells.append(["100", "Sample Next", "uint16", "", "AB", "1", "", "R"])
        evidence = parse_pdf_table_evidence(cells, page_number=1, table_index=0)
        self.assertEqual(1, evidence["records"][1]["word_count"])
        self.assertNotIn("engineering_offset", evidence["records"][1])

    def test_invalid_printed_width_is_preserved_not_defaulted(self):
        for reader, rows in readers(table(width="unknown")).items():
            with self.subTest(reader=reader):
                self.assertEqual("unknown", rows[0]["word_count"])

    def test_bias_disagreement_is_material_but_decimal_spelling_agrees(self):
        left = readers(table())["layout"][0]
        for bias, conflict in (("-3.00", False), ("4", True)):
            right = {**left, "engineering_offset": bias}
            accepted, held, conflicts = _reconcile([left], [right])
            self.assertEqual(conflict, bool(held))
            self.assertEqual(not conflict, bool(accepted))
            if conflict:
                self.assertEqual("engineering_offset", conflicts[0]["fields"][0]["field"])

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_generated_pdf_preserves_width_and_bias_without_new_source_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"synthetic.pdf"
            write_pdf(path, table())
            result = extract_pdf(path, path.read_bytes())
        self.assertEqual(1, len(result["records"]))
        self.assertFalse(result["quarantined_records"])
        row = result["records"][0]
        self.assertEqual("2", str(row["word_count"]))
        self.assertEqual("-3", row["engineering_offset"])
        self.assertEqual("ABCD", row["byte_order"])

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_generated_pdf_address_pair_conflict_cannot_be_reaccepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"synthetic.pdf"
            write_pdf(path, table(width="3", address="0x62/0x63"))
            result = extract_pdf(path, path.read_bytes())
        self.assertFalse(result["records"])
        self.assertEqual("held", result["status"])
        self.assertTrue(result["quarantined_records"])
        self.assertTrue(all(str(row["word_count"]) == "3" for row in result["quarantined_records"]))


if __name__ == "__main__":
    unittest.main()
