"""Caller-supplied engineering-unit metadata fills blanks without conversion."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.map_workflows import normalize_map  # noqa: E402
from modbus_skills.source_intake import _source_field_evidence  # noqa: E402

BASE = {"logical_point_id": "synthetic-flow", "name": "Synthetic Flow", "description": "Synthetic metadata test",
        "route_id": "synthetic-route", "unit_id": 7, "area": "input-register", "function_code": 4,
        "protocol_offset": 36, "datatype": "float32", "word_span": 2, "byte_order": "CDAB",
        "byte_order_confirmed": True, "scale": .25, "engineering_offset": -4, "access": "read-only"}


def unit_evidence(result):
    return [entry for entry in result["points"][0]["source_evidence"] if entry["field"] == "engineering_unit"]


def unit_assumptions(result):
    return [entry for entry in result["assumptions"] if entry.get("field") == "engineering_unit"]


class EngineeringUnitDefaultTests(unittest.TestCase):
    def test_explicit_default_fills_only_absent_null_empty_or_whitespace(self):
        for source in ({}, {"engineering_unit": None}, {"engineering_unit": ""}, {"engineering_unit": " \t "}):
            with self.subTest(source=source):
                result = normalize_map([{**BASE, **source}], defaults={"engineering_unit": " kPa "})
                self.assertEqual("kPa", result["points"][0]["engineering_unit"])
                self.assertEqual([], result["holds"])
                self.assertEqual([{"field": "engineering_unit", "source_field": "workflow_default", "source_value": " kPa ", "value": "kPa"}], unit_evidence(result))
                assumption = unit_assumptions(result)
                self.assertEqual(1, len(assumption))
                self.assertEqual("workflow-default", assumption[0]["code"])
                self.assertEqual("kPa", assumption[0]["value"])
                self.assertNotIn("approved", assumption[0]["message"].lower())
                self.assertNotIn("OEM", assumption[0]["message"])

    def test_nonblank_source_always_wins_and_retains_raw_evidence(self):
        for source in ("kPa", " kPa ", "unknown"):
            with self.subTest(source=source):
                result = normalize_map([{**BASE, "engineering_unit": source}], defaults={"engineering_unit": "degC"})
                self.assertEqual(source.strip(), result["points"][0]["engineering_unit"])
                self.assertEqual([], unit_assumptions(result))
                self.assertEqual([{"field": "engineering_unit", "source_field": "engineering_unit", "source_value": source, "value": source.strip()}], unit_evidence(result))

    def test_no_nonblank_default_does_not_invent_unit(self):
        for defaults in ({}, {"engineering_unit": None}, {"engineering_unit": ""}, {"engineering_unit": " \t "}):
            result = normalize_map([BASE], defaults=defaults)
            self.assertIsNone(result["points"][0]["engineering_unit"])
            self.assertFalse(unit_assumptions(result))

    def test_unknown_default_keys_and_aliases_remain_uninterpreted(self):
        for defaults in ({"eng_unit_typo": "kPa"}, {"units": "kPa"}, {"unit": "kPa"}):
            result = normalize_map([BASE], defaults=defaults)
            self.assertIsNone(result["points"][0]["engineering_unit"])
            self.assertFalse(result["holds"])
            self.assertFalse(unit_assumptions(result))

    def test_default_changes_no_other_canonical_point_field(self):
        baseline = normalize_map([BASE])["points"][0]
        filled = normalize_map([BASE], defaults={"engineering_unit": "kPa"})["points"][0]
        for point in (baseline, filled):
            point.pop("engineering_unit")
            point["source_evidence"] = [entry for entry in point["source_evidence"] if entry["field"] != "engineering_unit"]
        self.assertEqual(baseline, filled)

    def test_inputs_remain_unchanged(self):
        source, defaults = [{**BASE, "engineering_unit": ""}], {"engineering_unit": "kPa"}
        before = deepcopy((source, defaults))
        normalize_map(source, defaults=defaults)
        self.assertEqual(before, (source, defaults))

    def test_blank_source_claim_does_not_impersonate_default_provenance(self):
        for blank in (None, "", " \t "):
            with self.subTest(blank=blank):
                claim = {"field": "units", "raw_header": "Units", "raw_value": blank}
                normalized = normalize_map(
                    [{**BASE, "engineering_unit": blank, "_claims": [claim]}],
                    defaults={"engineering_unit": " kPa "},
                )
                point = normalized["points"][0]
                self.assertEqual([claim], point["source_claims"])
                evidence = _source_field_evidence(point, {"record_id": "synthetic-row"})
                unit = next(item for item in evidence if item["field"] == "engineering_unit")
                self.assertEqual({"field": "engineering_unit", "raw_header": "workflow_default",
                                  "raw_value": " kPa ", "normalized_value": "kPa",
                                  "source_ref": "workflow-default:engineering_unit", "status": "confirmed"}, unit)

    def test_nonblank_source_claim_keeps_source_provenance(self):
        claim = {"field": "units", "raw_header": "Units", "raw_value": " degC "}
        normalized = normalize_map(
            [{**BASE, "engineering_unit": " degC ", "_claims": [claim]}],
            defaults={"engineering_unit": "kPa"},
        )
        point = normalized["points"][0]
        evidence = _source_field_evidence(point, {"record_id": "synthetic-row"})
        unit = next(item for item in evidence if item["field"] == "engineering_unit")
        self.assertEqual("Units", unit["raw_header"])
        self.assertEqual(" degC ", unit["raw_value"])
        self.assertEqual("degC", unit["normalized_value"])
        self.assertEqual("synthetic-row", unit["source_ref"])
        self.assertEqual("confirmed", unit["status"])
        self.assertEqual([], unit_assumptions(normalized))

    def test_cli_normalize_and_compile_fill_and_source_override(self):
        skills = ROOT/"plugins/modbus-skills/skills"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for label, source_unit, wanted in (("fill", None, "kPa"), ("override", "degC", "degC")):
                source = folder/f"{label}-source.json"
                defaults = folder/f"{label}-defaults.json"
                normalized = folder/f"{label}-normalized.json"
                point = {**BASE, "engineering_unit": source_unit}
                source.write_text(json.dumps({"records": [point]}))
                defaults.write_text(json.dumps({"engineering_unit": "kPa"}))
                process = subprocess.run([sys.executable, str(skills/"normalize-map/scripts/run.py"), "--input", str(source), "--defaults", str(defaults), "--output", str(normalized)], cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, process.returncode, process.stderr)
                result = json.loads(normalized.read_text())
                self.assertEqual(wanted, result["points"][0]["engineering_unit"])
                self.assertEqual("workflow_default" if label == "fill" else "engineering_unit", unit_evidence(result)[0]["source_field"])
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source), "format": "json", "defaults": {"engineering_unit": "kPa"}},
                           "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
                request_path = folder/f"{label}-request.json"
                request_path.write_text(json.dumps(request))
                output = folder/f"{label}-compiled"
                process = subprocess.run([sys.executable, str(skills/"compile-user-map/scripts/run.py"), "--request", str(request_path), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, process.returncode, process.stderr)
                self.assertEqual("offline-complete", json.loads(process.stdout)["status"])
                user = json.loads((output/"output/user-map.json").read_text())["points"][0]
                self.assertEqual(wanted, user["engineering_unit"])
                for field in ("name", "description", "area", "function_code", "protocol_offset", "datatype", "word_span", "byte_order", "scale", "engineering_offset", "access"):
                    self.assertEqual(BASE[field], user[field], field)
                oem = json.loads((output/"artifacts/oem-map.json").read_text())
                self.assertEqual(label == "fill", bool(unit_assumptions(oem)))
                unit = next(item for item in oem["points"][0]["source_field_evidence"] if item["field"] == "engineering_unit")
                self.assertEqual("workflow_default" if label == "fill" else "engineering_unit", unit["raw_header"])
                self.assertEqual(label == "fill", unit["source_ref"] == "workflow-default:engineering_unit")
                self.assertEqual(wanted, unit["raw_value"])
                self.assertEqual(wanted, unit["normalized_value"])


if __name__ == "__main__":
    unittest.main()
