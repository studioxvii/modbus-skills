"""Source length literals are not consumed merely by matching a derived width."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
sys.path.insert(0, str(ROOT / "tests"))
from modbus_skills.source_intake import compile_source_descriptor
from test_source_workbook_fidelity import workbook
from test_literal_source_context import bundle


class UnconsumedSourceLengthTests(unittest.TestCase):
    def source(self, datatype, header, length):
        rows = [["Name", "Datatype", "Protocol Offset", "Area", "Access", header],
                ["Synthetic", datatype, 7, "holding-register", "Read", length]]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.xlsx"
            path.write_bytes(workbook([("Points", rows)]))
            return compile_source_descriptor({"path": str(path)})[0]

    def assert_literal(self, oem, field, value, datatype, span):
        result = bundle(oem)
        for artifact in (oem, result["user_map"]):
            self.assertEqual(1, len(artifact["points"]))
            point = artifact["points"][0]
            self.assertEqual(datatype, point["datatype"])
            self.assertEqual(span, point["word_span"])
            expected_codes = {"source-uninterpreted-fields"}
            if artifact is oem:
                expected_codes.update({"xlsx_header_row", "function-code-from-area", "generated-logical-point-id"})
                if datatype is not None:
                    expected_codes.add("span-from-datatype")
            self.assertCountEqual(expected_codes, [a["code"] for a in artifact["assumptions"]])
            note, = [a for a in artifact["assumptions"] if a["code"] == "source-uninterpreted-fields"]
            self.assertEqual("source-uninterpreted-fields", note["code"])
            self.assertEqual("source-context-only", note["status"])
            self.assertEqual({field: value}, note["fields"])
            self.assertEqual([{"oem_point_id": oem["points"][0]["oem_point_id"], "source_ref": {
                "record_id": "xlsx:sheet:Points:row:2", "format": "xlsx", "sheet": "Points", "row": 2,
            }}], note["bindings"])
        evidence = [e for e in oem["points"][0]["source_field_evidence"] if e["field"] == "word_span"]
        self.assertTrue(all(e["raw_header"] != field for e in evidence))
        self.assertNotIn(field, result["csv"].splitlines()[0])

    def test_unknown_datatype_length_read_cannot_establish_width(self):
        oem = self.source("Unknown", "Length Read", 1)
        self.assert_literal(oem, "length_read", 1, None, None)
        self.assertTrue(oem["holds"])

    def test_conflicting_unconsumed_length_does_not_replace_datatype_width(self):
        self.assert_literal(self.source("UINT16", "Length Read", 2), "length_read", 2, "uint16", 1)

    def test_equal_unconsumed_length_remains_a_source_claim(self):
        self.assert_literal(self.source("UINT16", "Length Read", 1), "length_read", 1, "uint16", 1)

    def test_recognized_length_words_is_consumed_not_redundant(self):
        oem = self.source("UINT32", "Length Words", 2)
        self.assertEqual(2, oem["points"][0]["word_span"])
        self.assertCountEqual({"xlsx_header_row", "function-code-from-area", "generated-logical-point-id"},
                              [a["code"] for a in oem["assumptions"]])
        self.assertEqual([], bundle(oem)["user_map"]["assumptions"])
        evidence = next(e for e in oem["points"][0]["source_field_evidence"] if e["field"] == "word_span")
        self.assertEqual("word_count", evidence["raw_header"])
        self.assertEqual(2, evidence["raw_value"])

    def test_ordinary_unknown_length_remains_literal(self):
        self.assert_literal(self.source("UINT16", "Length", 8), "length", 8, "uint16", 1)


if __name__ == "__main__":
    unittest.main()
