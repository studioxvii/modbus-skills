"""A proven consecutive Protocol Offset pair retains its raw source evidence."""
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
sys.path.insert(0, str(ROOT/"plugins/modbus-skills/runtime"))
from modbus_skills.map_workflows import normalize_map  # noqa: E402
from modbus_skills.pdf_extraction import _reconcile, extract_pdf  # noqa: E402
from modbus_skills.pdf_table_extraction import prepare_pdf_records  # noqa: E402
from test_pdf_explicit_width_bias import readers, table, write_pdf  # noqa: E402

DEFAULTS = {"area": "holding-register", "function_code": 3, "route_id": "synthetic-route", "unit_id": 7}


class PdfProtocolPairTests(unittest.TestCase):
    def test_readers_share_first_address_and_preserve_full_proven_pair(self):
        for raw, first, wanted in (("0x60/0x61", "0x60", 96), ("96/97", "96", 96), ("0x60/0x61*", "0x60", 96)):
            with self.subTest(raw=raw):
                parsed = readers(table(address=raw))
                for method, rows in parsed.items():
                    row = rows[0]
                    self.assertEqual(raw, row["source_register"], method)
                    self.assertEqual(raw, row["address_parse"]["raw"], method)
                    self.assertEqual(first, row["source_address"]["raw"], method)
                    self.assertEqual(2, row["address_parse"]["word_count"], method)
                    self.assertTrue(any(c.get("value") == raw for c in row["_claims"]), method)
                accepted, held, conflicts = _reconcile(parsed["layout"], parsed["bbox"])
                self.assertFalse(held)
                self.assertFalse(conflicts)
                accepted, held, conflicts = _reconcile(accepted, parsed["grid"])
                self.assertEqual(1, len(accepted))
                self.assertFalse(held)
                self.assertFalse(conflicts)
                result = normalize_map(prepare_pdf_records({"records": accepted}), defaults=DEFAULTS)
                self.assertFalse(result["holds"])
                point = result["points"][0]
                self.assertEqual(wanted, point["protocol_offset"])
                self.assertEqual(2, point["word_span"])
                self.assertEqual("float32", point["datatype"])
                self.assertEqual("ABCD", point["byte_order"])
                self.assertEqual(.25, point["scale"])
                self.assertEqual(-3, point["engineering_offset"])

    def test_distinct_physical_pairs_are_not_merged_by_same_name(self):
        first = readers(table(address="0x60/0x61"))["grid"][0]
        second = readers(table(address="0x70/0x71"))["grid"][0]
        second["_source"] = {**second["_source"], "row": 2, "region": "p1:t0:r2"}
        accepted, held, conflicts = _reconcile([first], [second])
        self.assertEqual(2, len(accepted))
        self.assertFalse(held)
        self.assertFalse(conflicts)

    def test_pair_does_not_suppress_true_same_row_address_conflict(self):
        first = readers(table(address="0x60/0x61"))["grid"][0]
        second = readers(table(address="0x70/0x71"))["grid"][0]
        accepted, held, conflicts = _reconcile([first], [second])
        self.assertFalse(accepted)
        self.assertTrue(held)
        self.assertIn("address", {f["field"] for conflict in conflicts for f in conflict["fields"]})

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_full_pdf_pair_compiles_with_original_raw_source_and_physical_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder/"pair.pdf"
            write_pdf(source, table(address="0x60/0x61"))
            request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source), "format": "pdf", "defaults": DEFAULTS},
                "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
            request_path = folder/"request.json"
            request_path.write_text(json.dumps(request))
            output = folder/"compiled"
            result = subprocess.run([sys.executable, str(ROOT/"plugins/modbus-skills/skills/compile-user-map/scripts/run.py"), "--request", str(request_path), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("offline-complete", json.loads(result.stdout)["status"])
            user = json.loads((output/"output/user-map.json").read_text())
            self.assertFalse(user["holds"])
            self.assertEqual(96, user["points"][0]["protocol_offset"])
            self.assertEqual(2, user["points"][0]["word_span"])
            oem = json.loads((output/"artifacts/oem-map.json").read_text())["points"][0]
            self.assertEqual("0x60/0x61", oem["source_register"])
            evidence = next(e for e in oem["source_field_evidence"] if e["field"] == "protocol_offset")
            self.assertEqual("0x60/0x61", evidence["raw_value"])
            self.assertEqual(96, evidence["normalized_value"])
            self.assertEqual("confirmed", evidence["status"])
            self.assertTrue(any(ref.get("region_id") == "p1:t0:r1" for ref in oem["source_refs"]))

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_last_valid_pair_passes_and_crossing_pair_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index, (raw, offset, wanted) in enumerate((("0xFFFE/0xFFFF", 65534, "offline-complete"), ("0xFFFF/0x10000", 65535, "partial"))):
                source = folder/f"pair-{index}.pdf"
                write_pdf(source, table(address=raw))
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source), "format": "pdf", "defaults": DEFAULTS},
                    "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
                request_path = folder/f"request-{index}.json"
                request_path.write_text(json.dumps(request))
                output = folder/f"compiled-{index}"
                result = subprocess.run([sys.executable, str(ROOT/"plugins/modbus-skills/skills/compile-user-map/scripts/run.py"), "--request", str(request_path), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(wanted, json.loads(result.stdout)["status"])
                user = json.loads((output/"output/user-map.json").read_text())
                self.assertEqual(offset, user["points"][0]["protocol_offset"])
                self.assertEqual(2, user["points"][0]["word_span"])
                self.assertEqual(wanted == "partial", "point.range-out-of-bounds" in {h["code"] for h in user["holds"]})

    @unittest.skipUnless(shutil.which("pdftotext") and importlib.util.find_spec("pdfplumber"), "PDF tools unavailable")
    def test_invalid_nonconsecutive_and_width_conflicting_pairs_stay_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, (raw, width) in enumerate((("0x60/0x62", "2"), ("0x61/0x60", "2"), ("0x60/invalid", "2"), ("0x60/0x61", "3"))):
                with self.subTest(raw=raw, width=width):
                    source = Path(tmp)/f"pair-{index}.pdf"
                    write_pdf(source, table(address=raw, width=width))
                    result = extract_pdf(source, source.read_bytes())
                    self.assertFalse(result["records"])
                    self.assertTrue(result["holds"])
                    if width == "3":
                        self.assertIn("pdf-address-width-conflict", {hold["code"] for hold in result["holds"]})


if __name__ == "__main__":
    unittest.main()
