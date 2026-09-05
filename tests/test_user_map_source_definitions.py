"""Synthetic datatype dictionaries remain source context, not enum semantics."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.source_intake import compile_source_descriptor
from modbus_skills.user_map import compile_user_map_bundle
from test_source_workbook_fidelity import HEADER, workbook


DEFINITION = "Two states (0=IDLE; 1=RUN)"


def legend(definition=DEFINITION):
    return [["Modbus Data Types"], ["Modbus Data Type", "Description", "", "Range"],
            ["BOOLEAN", definition, "", "-"]]


def source(definitions=None):
    sheets = definitions if definitions is not None else [("Definitions", legend())]
    sheets = [*sheets, ("Points", [[*HEADER, "Description"],
                                  ["Status", "BOOLEAN", 2, 7, 1, "Original status description"],
                                  ["BOOLEAN", "UINT16", 3, 8, 1, "Original numeric description"]])]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "synthetic.xlsx"
        path.write_bytes(workbook(sheets))
        return compile_source_descriptor({"path": str(path)})[0]


def bundle(oem, names=("Status", "BOOLEAN")):
    included = [p for p in oem["points"] if p["name"] in names]
    candidate = {
        "oem_map_hash": stable_input_hash(oem), "requested_measurements": ["selected readings"],
        "included": [{"oem_point_id": p["oem_point_id"], "matched_intent": "selected readings",
                      "match_quality": "exact", "reason": "Synthetic scoped selection",
                      "evidence_refs": [r["record_id"] for r in p["source_refs"]]} for p in included],
        "suggested": [], "excluded": [],
    }
    return compile_user_map_bundle(oem, candidate, case_id="synthetic-source-definitions")


class UserMapSourceDefinitionTests(unittest.TestCase):
    def test_literal_note_json_markdown_and_field_evidence_without_point_changes(self):
        oem = source()
        original = copy.deepcopy(oem)
        result = bundle(oem)
        note, = result["user_map"]["assumptions"]
        status = next(p for p in oem["points"] if p["name"] == "Status")
        evidence = next(e for e in status["source_field_evidence"] if e["field"] == "datatype")
        self.assertEqual("BOOLEAN", note["source_datatype"])
        self.assertEqual(DEFINITION, note["definition"])
        self.assertEqual({"format": "xlsx", "sheet": "Definitions", "row": 3,
                          "datatype_cell": "A3", "definition_cell": "B3"}, note["source_location"])
        self.assertEqual([{"oem_point_id": status["oem_point_id"], **evidence}], note["matching_datatype_evidence"])
        self.assertEqual("BOOLEAN", note["matching_datatype_evidence"][0]["raw_value"])
        self.assertEqual(note, json.loads(result["json"])["assumptions"][0])
        self.assertIn("0=IDLE; 1=RUN", result["human_summary"])
        self.assertIn("Definitions!A3, B3".replace("!", "\\!"), result["human_summary"])
        self.assertIn("do not configure decoding", result["human_summary"])
        without = copy.deepcopy(oem)
        without["assumptions"] = []
        baseline = bundle(without)
        for field in ("points", "holds", "exception_annex"):
            self.assertEqual(baseline["user_map"][field], result["user_map"][field])
        self.assertEqual(baseline["csv"], result["csv"])
        self.assertEqual(["Original status description", "Original numeric description"],
                         [p["description"] for p in result["user_map"]["points"]])
        self.assertNotIn('"enum_values"', result["json"])
        self.assertEqual(original, oem)
        self.assertEqual(result, bundle(oem))

    def test_absent_legend_does_not_invent_labels(self):
        result = bundle(source([]))
        self.assertEqual([], result["user_map"]["assumptions"])
        self.assertNotIn("Source datatype definitions", result["human_summary"])

    def test_inverted_definition_is_literal_not_decoding(self):
        result = bundle(source([("Definitions", legend("Two states (0=RUN; 1=IDLE)"))]))
        self.assertEqual("Two states (0=RUN; 1=IDLE)", result["user_map"]["assumptions"][0]["definition"])
        self.assertEqual("bool", result["user_map"]["points"][0]["datatype"])
        self.assertNotIn('"enum_values"', result["json"])

    def test_numeric_point_named_datatype_and_unselected_bool_do_not_match(self):
        result = bundle(source(), names=("BOOLEAN",))
        self.assertEqual("uint16", result["user_map"]["points"][0]["datatype"])
        self.assertEqual([], result["user_map"]["assumptions"])

    def test_conflicting_contexts_and_distinct_sheets_survive_duplicate_group(self):
        oem = source([("First", legend()), ("Second", legend("Two states (0=RUN; 1=IDLE)"))])
        groups = [a for a in oem["assumptions"] if a["code"] == "excluded_xlsx_datatype_legend"]
        oem["assumptions"].append(copy.deepcopy(groups[0]))
        oem["assumptions"].append({"code": "unrelated", "message": "Do not copy this assumption"})
        result = bundle(oem)
        notes = result["user_map"]["assumptions"]
        self.assertEqual(["First", "Second"], [n["source_location"]["sheet"] for n in notes])
        self.assertEqual([DEFINITION, "Two states (0=RUN; 1=IDLE)"], [n["definition"] for n in notes])
        self.assertNotIn("Do not copy this assumption", result["json"])
        self.assertIn("without resolving conflicts", result["human_summary"])

    def test_formula_heading_header_or_definition_never_promotes_cached_labels(self):
        for row, column in ((0, 0), (1, 0), (1, 1), (2, 0), (2, 1)):
            with self.subTest(row=row, column=column):
                rows = legend()
                cached = rows[row][column]
                rows[row][column] = {"formula": '"' + cached + '"', "cached": cached}
                result = bundle(source([("Definitions", rows)]))
                self.assertEqual([], result["user_map"]["assumptions"])

    def test_matching_does_not_use_normalized_alias_or_unconfirmed_evidence(self):
        for mutation in ("alias", "missing", "unresolved", "stale"):
            with self.subTest(mutation=mutation):
                oem = source()
                point = next(p for p in oem["points"] if p["name"] == "Status")
                evidence = next(e for e in point["source_field_evidence"] if e["field"] == "datatype")
                if mutation == "alias":
                    evidence["raw_value"] = "BOOL"
                elif mutation == "missing":
                    point["source_field_evidence"].remove(evidence)
                elif mutation == "unresolved":
                    evidence["status"] = "unresolved"
                else:
                    evidence["normalized_value"] = "uint16"
                self.assertEqual([], bundle(oem)["user_map"]["assumptions"])

    def test_sheet_definition_markdown_escaped_but_json_literal(self):
        name = "Defs_[x]&<b>"
        definition = "Two states (0=[IDLE](file); 1=<b>RUN</b>)\n# instruction"
        result = bundle(source([(name, legend(definition))]))
        note, = result["user_map"]["assumptions"]
        self.assertEqual(definition, note["definition"])
        self.assertEqual(name, note["source_location"]["sheet"])
        self.assertNotIn("<b>", result["human_summary"])
        self.assertNotIn("\n# instruction", result["human_summary"])
        self.assertIn(r"\[IDLE\]\(file\)", result["human_summary"])
        self.assertIn("&lt;b&gt;", result["human_summary"])

    def test_blank_leading_columns_keep_physical_cell_identity(self):
        rows = [["", *row] for row in legend()]
        note, = bundle(source([("Definitions", rows)]))["user_map"]["assumptions"]
        self.assertEqual("B3", note["source_location"]["datatype_cell"])
        self.assertEqual("C3", note["source_location"]["definition_cell"])

    def test_malformed_or_other_format_assumptions_are_not_forwarded(self):
        for mutation in ("missing_rows", "bad_header", "bad_row", "other_format"):
            with self.subTest(mutation=mutation):
                oem = source()
                group = next(a for a in oem["assumptions"] if a["code"] == "excluded_xlsx_datatype_legend")
                if mutation == "missing_rows":
                    group.pop("source_rows")
                elif mutation == "bad_header":
                    group["source_rows"][1]["values"] = ["not a datatype dictionary"]
                elif mutation == "bad_row":
                    group["source_rows"][2]["row"] = True
                else:
                    oem["source_reference"]["format"] = "json"
                self.assertEqual([], bundle(oem)["user_map"]["assumptions"])


if __name__ == "__main__":
    unittest.main()
