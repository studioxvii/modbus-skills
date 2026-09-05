from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import stable_input_hash  # noqa: E402
from modbus_skills.compiler_contracts import build_oem_map  # noqa: E402
from modbus_skills.source_intake import SourceIntakeError, bind_selection_template  # noqa: E402
from modbus_skills.user_map import compile_user_map_bundle  # noqa: E402


def point(**fields):
    return {"oem_point_id": "synthetic-a", "name": None,
            "description": "Synthetic temperature", "area": "holding-register",
            "protocol_offset": 10, "datatype": "uint16", "word_span": 1,
            "access": "read-only", "function_code": 3,
            "source_refs": [{"record_id": "synthetic-row-2"}], **fields}


def source(points=None, holds=()):
    return build_oem_map(points or [point()], source_hash="a" * 64, holds=holds)


def selection(oem, **entry_fields):
    return {"oem_map_hash": stable_input_hash(oem), "requested_measurements": ["temperature"],
            "included": [{"oem_point_id": p["oem_point_id"], "matched_intent": "temperature",
                          "match_quality": "exact", "reason": "Explicit synthetic row selection",
                          "evidence_refs": [p["source_refs"][0]["record_id"]], **entry_fields}
                         for p in oem["points"]], "suggested": [], "excluded": []}


class UserMapSourceLabelTests(unittest.TestCase):
    def bundle(self, oem, **entry_fields):
        before = copy.deepcopy(oem)
        result = compile_user_map_bundle(oem, selection(oem, **entry_fields), case_id="synthetic-labels")
        self.assertEqual(before, oem)
        return result

    def assert_label(self, result, expected, index=0):
        json_point = json.loads(result["json"])["points"][index]
        csv_point = list(csv.DictReader(io.StringIO(result["csv"])))[index]
        self.assertEqual(expected, json_point["display_name"])
        self.assertEqual(expected, csv_point["display_name"])
        self.assertIn(f"— {expected} (", result["human_summary"])
        self.assertNotIn("— None (", result["human_summary"])

    def test_description_is_preserved_exactly_in_json_csv_and_human_display(self):
        description = 'Synthetic temperature, "inlet" °C'
        result = self.bundle(source([point(description=description)]))
        self.assert_label(result, description)
        self.assertIsNone(result["user_map"]["points"][0]["name"])
        self.assertEqual(description, result["user_map"]["points"][0]["description"])
        row = next(csv.DictReader(io.StringIO(result["csv"])))
        self.assertEqual(description, row["description"])
        self.assertEqual("", row["name"])  # Source Name remains honestly absent.

    def test_csv_preserves_existing_column_positions_and_appends_new_labels(self):
        result = self.bundle(source())
        header = next(csv.reader(io.StringIO(result["csv"])))
        self.assertEqual([
            "requested_measurement", "group", "alias", "oem_point_id", "name",
            "source_register", "area", "protocol_offset", "datatype", "word_span",
            "engineering_unit", "confidence", "reason", "evidence_refs",
            "description", "display_name",
        ], header)

    def test_real_name_precedes_description_without_overwriting_either(self):
        result = self.bundle(source([point(name="Source name")]))
        self.assert_label(result, "Source name")
        self.assertEqual("Synthetic temperature", result["user_map"]["points"][0]["description"])
        self.assertEqual("Source name", result["user_map"]["points"][0]["name"])

    def test_explicit_user_alias_precedes_real_name_and_description(self):
        for name in (None, "Source name"):
            with self.subTest(name=name):
                result = self.bundle(source([point(name=name)]), alias="Operator label")
                self.assert_label(result, "Operator label")
                self.assertEqual(name, result["user_map"]["points"][0]["name"])
                self.assertEqual("Synthetic temperature", result["user_map"]["points"][0]["description"])

    def test_empty_or_whitespace_labels_fall_back_without_changing_source_values(self):
        for name in (None, "", "  "):
            for description in (None, "", " \t"):
                with self.subTest(name=name, description=description):
                    result = self.bundle(source([point(name=name, description=description)]), alias="  ")
                    self.assert_label(result, "synthetic-a")
                    self.assertEqual(name, result["user_map"]["points"][0]["name"])
                    self.assertEqual(description, result["user_map"]["points"][0]["description"])

    def test_duplicate_descriptions_keep_both_identities_and_offsets(self):
        result = self.bundle(source([point(), point(oem_point_id="synthetic-b", protocol_offset=11,
                                                   source_refs=[{"record_id": "synthetic-row-3"}])]))
        self.assertEqual(["synthetic-a", "synthetic-b"], [p["oem_point_id"] for p in result["user_map"]["points"]])
        self.assertEqual([10, 11], [p["protocol_offset"] for p in result["user_map"]["points"]])
        self.assert_label(result, "Synthetic temperature", 0)
        self.assert_label(result, "Synthetic temperature", 1)

    def test_unknown_engineering_fields_and_hold_survive_label_projection(self):
        hold = {"code": "source.address-unresolved", "severity": "hold", "blocking": True,
                "oem_point_id": "synthetic-a", "message": "Source convention is unknown"}
        result = self.bundle(source([point(protocol_offset=None, area=None, datatype=None, word_span=None)], [hold]))
        self.assert_label(result, "Synthetic temperature")
        rendered = result["user_map"]["points"][0]
        for field in ("protocol_offset", "area", "datatype", "word_span"):
            self.assertIsNone(rendered[field])
        self.assertTrue(any(h["code"] == hold["code"] and h["blocking"] for h in result["user_map"]["holds"]))

    def test_exact_name_contract_does_not_gain_description_alias(self):
        template = {"schema_version": "modbus-user-selection-template/v1", "requested_measurements": ["temperature"],
                    "included": [{"exact_name": "Synthetic temperature"}], "suggested": [], "excluded": []}
        for points in ([point()], [point(), point(oem_point_id="synthetic-b")], [point(name="Different name")]):
            with self.subTest(points=len(points)):
                with self.assertRaisesRegex(SourceIntakeError, "must match exactly one"):
                    bind_selection_template(template, source(points))

    def test_write_only_row_is_not_promoted_by_its_description(self):
        hold = {"code": "source.write-only", "severity": "hold", "blocking": True,
                "oem_point_id": "synthetic-a", "message": "No supported read operation"}
        result = self.bundle(source([point(access="write-only", function_code=6)], [hold]))
        rendered = result["user_map"]["points"][0]
        self.assertEqual("write-only", rendered["access"])
        self.assertEqual(6, rendered["function_code"])
        self.assertTrue(any(h["code"] == hold["code"] and h["blocking"] for h in result["user_map"]["holds"]))
        self.assert_label(result, "Synthetic temperature")


if __name__ == "__main__":
    unittest.main()
