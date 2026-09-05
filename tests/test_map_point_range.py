"""Resolved read spans cannot cross the end of the Modbus address space."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"plugins/modbus-skills/runtime"))
from modbus_skills.map_workflows import normalize_map  # noqa: E402

BASE = {"name": "Sample Value", "area": "holding-register", "function_code": 3,
        "unit_id": 7, "route_id": "synthetic-route", "access": "read-only", "scale": 1}


class MapPointRangeTests(unittest.TestCase):
    def test_explicit_derived_and_default_spans_share_boundary_guard(self):
        for source, defaults in (({"datatype": "float32", "word_span": 2, "byte_order": "ABCD"}, {}),
                                 ({"datatype": "float32", "byte_order": "ABCD"}, {}),
                                 ({"datatype": "string"}, {"word_span": 2})):
            with self.subTest(source=source, defaults=defaults):
                result = normalize_map([{**BASE, "protocol_offset": 65535, **source}], defaults=defaults)
                self.assertEqual(["point.range-out-of-bounds"], [h["code"] for h in result["holds"]])
                self.assertEqual(65535, result["points"][0]["protocol_offset"])
                self.assertEqual(2, result["points"][0]["word_span"])
                self.assertEqual("word_span", result["holds"][0]["field"])

    def test_last_valid_scalar_and_multiword_ranges_pass(self):
        for point in ({"protocol_offset": 65535, "datatype": "uint16"},
                      {"protocol_offset": 65534, "datatype": "float32", "byte_order": "ABCD"},
                      {"protocol_offset": 65532, "datatype": "int64", "byte_order": "ABCDEFGH"}):
            with self.subTest(point=point):
                result = normalize_map([{**BASE, **point}])
                self.assertFalse(result["holds"])

    def test_invalid_offset_or_width_does_not_gain_duplicate_range_hold(self):
        cases = [({"protocol_offset": 65536, "datatype": "uint16"}, "address.invalid"),
                 ({"protocol_offset": -1, "datatype": "uint16"}, "address.invalid"),
                 ({"protocol_offset": 65535, "datatype": "float32", "word_span": 0, "byte_order": "ABCD"}, "point.span-invalid")]
        for point, code in cases:
            with self.subTest(point=point):
                result = normalize_map([{**BASE, **point}])
                self.assertEqual([code], [h["code"] for h in result["holds"]])

    def test_unknown_offset_stays_unresolved_without_inferred_range(self):
        result = normalize_map([{**BASE, "source_address": {"raw": "65535", "convention": "unknown"}, "datatype": "float32", "byte_order": "ABCD"}])
        self.assertIsNone(result["points"][0]["protocol_offset"])
        self.assertEqual(["address.convention-unresolved"], [h["code"] for h in result["holds"]])

    def test_full_json_compile_holds_crossing_spans_but_keeps_last_valid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cases = [(65535, "float32", None, "partial"), (65535, "float32", 2, "partial"),
                     (65534, "float32", None, "offline-complete"), (65535, "uint16", None, "offline-complete")]
            for index, (offset, datatype, span, expected) in enumerate(cases):
                point = {**BASE, "protocol_offset": offset, "datatype": datatype}
                if datatype == "float32":
                    point["byte_order"] = "ABCD"
                if span is not None:
                    point["word_span"] = span
                source = folder/f"source-{index}.json"
                source.write_text(json.dumps({"records": [point]}))
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source), "format": "json"},
                    "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
                request_path = folder/f"request-{index}.json"
                request_path.write_text(json.dumps(request))
                output = folder/f"compiled-{index}"
                result = subprocess.run([sys.executable, str(ROOT/"plugins/modbus-skills/skills/compile-user-map/scripts/run.py"), "--request", str(request_path), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, json.loads(result.stdout)["status"])
                user = json.loads((output/"output/user-map.json").read_text())
                self.assertEqual(expected == "partial", "point.range-out-of-bounds" in {h["code"] for h in user["holds"]})
                self.assertEqual(offset, user["points"][0]["protocol_offset"])


if __name__ == "__main__":
    unittest.main()
