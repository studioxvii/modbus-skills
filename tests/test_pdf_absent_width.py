"""Synthetic PDF start addresses do not claim a datatype's register width."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.map_workflows import normalize_map  # noqa: E402
from modbus_skills.pdf_table_extraction import prepare_pdf_records  # noqa: E402
from test_pdf_explicit_width_bias import readers, table, write_pdf  # noqa: E402

DEFAULTS = {"area": "holding-register", "function_code": 3, "route_id": "synthetic-route", "unit_id": 7}


def source_cells(datatype="float32", layout="ABCD", width=None, address="96", header="Protocol Offset"):
    cells = table(width=width or "", address=address)
    cells[0][0] = header
    cells[1][2] = datatype
    cells[1][4] = layout or ""
    if layout is None:
        for row in cells:
            del row[4]
    if width is None:
        for row in cells:
            del row[3]
    return cells


class PdfAbsentWidthTests(unittest.TestCase):
    def test_all_readers_leave_absent_width_unclaimed(self):
        for datatype, layout, span in (("uint16", "AB", 1), ("float32", "ABCD", 2), ("int64", "ABCDEFGH", 4)):
            for reader, rows in readers(source_cells(datatype, layout)).items():
                with self.subTest(datatype=datatype, reader=reader):
                    row = rows[0]
                    self.assertNotIn("word_count", row)
                    self.assertNotIn("word_count", row["address_parse"])
                    self.assertFalse(any(c.get("field") in {"word_count", "word_span"} for c in row["_claims"]))
                    normalized = normalize_map(prepare_pdf_records({"records": [row]}), defaults=DEFAULTS)
                    point = normalized["points"][0]
                    self.assertEqual(span, point["word_span"])
                    self.assertNotIn("point.datatype-span-mismatch", {h["code"] for h in normalized["holds"]})
                    evidence = next(e for e in point["source_evidence"] if e["field"] == "word_span")
                    self.assertEqual("datatype", evidence["source_field"])
                    self.assertIsNone(evidence["source_value"])
                    self.assertTrue(any(a["code"] == "span-from-datatype" for a in normalized["assumptions"]))

    def test_explicit_width_and_pair_evidence_keep_material_conflicts(self):
        for width, address, wanted, conflict in (("1", "96", 1, True), (None, "0x60/0x61", 2, False), ("3", "0x60/0x61", 3, True)):
            for reader, rows in readers(source_cells(width=width, address=address, header="Address")).items():
                with self.subTest(width=width, address=address, reader=reader):
                    self.assertEqual(wanted, int(rows[0]["word_count"]))
                    result = normalize_map(prepare_pdf_records({"records": rows}), defaults=DEFAULTS)
                    self.assertEqual(conflict, "point.datatype-span-mismatch" in {h["code"] for h in result["holds"]})
                    if width == "3":
                        self.assertEqual("pdf-address-width-conflict", rows[0]["code"])
                        self.assertEqual(2, rows[0]["address_parse"]["word_count"])

    def test_unknown_offset_basis_stays_unknown_without_width(self):
        for reader, rows in readers(source_cells(header="Offset")).items():
            with self.subTest(reader=reader):
                self.assertEqual("unknown", rows[0]["address_convention"])
                self.assertNotIn("protocol_offset", rows[0])
                self.assertNotIn("word_count", rows[0])

    def test_string_width_requires_source_or_explicit_default(self):
        base = {**DEFAULTS, "protocol_offset": 96, "name": "Sample Text", "datatype": "string", "access": "read-only"}
        for value in (None, ""):
            result = normalize_map([{**base, "word_span": value}])
            self.assertIsNone(result["points"][0]["word_span"])
            self.assertEqual(["point.span-unresolved"], [h["code"] for h in result["holds"]])
        for source, defaults, expected in (({}, {"word_span": 4}, 4), ({"word_span": 2}, {"word_span": 4}, 2)):
            result = normalize_map([{**base, **source}], defaults=defaults)
            self.assertEqual(expected, result["points"][0]["word_span"])
            self.assertFalse(result["holds"])

    def test_invalid_string_width_does_not_get_duplicate_missing_width_hold(self):
        base = {**DEFAULTS, "protocol_offset": 96, "name": "Sample Text", "datatype": "string", "access": "read-only"}
        for width in (0, -1, "unknown", " ", 1.5, True):
            with self.subTest(width=width):
                result = normalize_map([{**base, "word_span": width}])
                self.assertEqual(["point.span-invalid"], [h["code"] for h in result["holds"]])

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_full_pdf_compile_derives_fixed_widths_and_holds_unknown_strings(self):
        cases = (("uint16", "AB", None, "96", "Protocol Offset", 1, "offline-complete"),
                 ("float32", "ABCD", None, "96", "Protocol Offset", 2, "offline-complete"),
                 ("int64", "ABCDEFGH", None, "96", "Protocol Offset", 4, "offline-complete"),
                 ("string", None, None, "96", "Protocol Offset", None, "partial"),
                 ("float32", "ABCD", "1", "96", "Protocol Offset", 1, "partial"),
                 ("float32", "ABCD", None, "0x60/0x61", "Address", 2, "offline-complete"))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index, (datatype, layout, width, address, header, span, status) in enumerate(cases):
                with self.subTest(datatype=datatype, width=width, address=address):
                    source = folder/f"source-{index}.pdf"
                    write_pdf(source, source_cells(datatype, layout, width, address, header))
                    request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source), "format": "pdf", "defaults": DEFAULTS},
                        "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
                    request_path = folder/f"request-{index}.json"
                    request_path.write_text(json.dumps(request))
                    output = folder/f"output-{index}"
                    result = subprocess.run([sys.executable, str(ROOT/"plugins/modbus-skills/skills/compile-user-map/scripts/run.py"), "--request", str(request_path), "--output", str(output)], capture_output=True, text=True, cwd=ROOT, timeout=30)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(status, json.loads(result.stdout)["status"])
                    user = json.loads((output/"output/user-map.json").read_text())
                    self.assertEqual(span, user["points"][0]["word_span"])
                    if datatype == "string":
                        self.assertIn("point.span-unresolved", {h["code"] for h in user["holds"]})
                    if width is None and datatype != "string" and "/" not in address:
                        oem = json.loads((output/"artifacts/oem-map.json").read_text())
                        evidence = next(e for e in oem["points"][0]["source_field_evidence"] if e["field"] == "word_span")
                        self.assertIsNone(evidence["raw_value"])
                        self.assertEqual("datatype", evidence["raw_header"])


if __name__ == "__main__":
    unittest.main()
