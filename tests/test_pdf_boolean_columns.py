from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import _reconcile, parse_layout_rows
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence


HEADERS = ["Name", "Description", "Access", "Input?", "Output?", "Settings?", "Offset"]
HEADER = "Name                    Description                             Access Input? Output? Settings? Offset"
CELLS = ["pump.state", "Pump state", "RW", "FALSE", "FALSE", "TRUE", "17"]
ROW = "pump.state              Pump state                              RW     FALSE FALSE   TRUE      17"


def grid(cells=CELLS, headers=HEADERS):
    return parse_pdf_table_evidence([headers, cells], page_number=1, table_index=0)


class BooleanColumnTests(unittest.TestCase):
    def test_two_boolean_tokens_under_distinct_headers_stay_separate(self):
        rows, _ = parse_layout_rows(HEADER + "\n" + ROW)
        self.assertEqual({"input": "FALSE", "output": "FALSE", "settings": "TRUE"}, rows[0]["_extra"])
        self.assertEqual("RW", rows[0]["access"])
        self.assertEqual("unknown", rows[0]["address_convention"])
        self.assertEqual("17", rows[0]["source_offset"])
        self.assertNotIn("area", rows[0])
        claims = [c for c in rows[0]["_claims"] if c["field"].startswith("_extra:")]
        self.assertEqual(["Input?", "Output?", "Settings?"], [c["raw_header"] for c in claims])
        self.assertEqual([3, 4, 5], [c["column_index"] for c in claims])

    def test_three_booleans_and_access_are_split_only_at_their_anchors(self):
        header = "Name                    Description                             Access Input? Output? Config? Offset"
        for access in ("R", "RW", "W"):
            # Access and booleans form one segment; each token remains closest
            # to its own header anchor, not to a shared free-text cell.
            line = ("pump.state              Pump state                              " + access.ljust(8) + "TRUE FALSE FALSE").ljust(94) + "17"
            rows, _ = parse_layout_rows(header + "\n" + line)
            self.assertEqual(access, rows[0]["access"])
            self.assertEqual({"input": "TRUE", "output": "FALSE", "config": "FALSE"}, rows[0]["_extra"])

    def test_name_and_description_join_is_not_repaired_or_released(self):
        cells = ["pump.state.long.symbol", "Pump state latch", "RW", "FALSE", "FALSE", "TRUE", "19"]
        line = "pump.state.long.symbol Pump state latch                          RW     FALSE FALSE   TRUE      19"
        strict, _ = parse_layout_rows(HEADER + "\n" + line)
        accepted, held, conflicts = _reconcile(strict, grid(cells)["records"])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(held))
        self.assertIn("name", [f["field"] for f in conflicts[0]["fields"]])

    def test_tight_access_header_allows_only_its_aligned_scalar_tokens(self):
        header = "Name                    Description                             R/W Input? Output? Config? Offset"
        line = "pump.state              Pump state                              RW TRUE FALSE   FALSE".ljust(91) + "17"
        rows, _ = parse_layout_rows(header + "\n" + line)
        self.assertEqual("RW", rows[0]["access"])
        self.assertEqual({"input": "TRUE", "output": "FALSE", "config": "FALSE"}, rows[0]["_extra"])

    def test_geometry_without_distinct_token_columns_is_not_a_split(self):
        from modbus_skills.pdf_extraction import _layout_header_at, _split_boolean_columns
        columns = _layout_header_at([HEADER], 0)[1]
        # Both boolean tokens are in the Input column's own neighborhood.
        original = [(69, "TRUE FALSE")]
        self.assertEqual(original, _split_boolean_columns(original, columns))

    def test_equidistant_column_anchor_is_unresolved_not_right_biased(self):
        from modbus_skills.pdf_extraction import _layout_header_at, _split_boolean_columns
        columns = _layout_header_at([HEADER], 0)[1]
        # Final FALSE starts82: exactly between Output78 and Settings86.
        original = [(71, "TRUE FALSE FALSE")]
        self.assertEqual(original, _split_boolean_columns(original, columns))

    def test_free_description_boolean_phrase_is_not_split(self):
        line = ROW.replace("Pump state", "TRUE FALSE")
        rows, _ = parse_layout_rows(HEADER + "\n" + line)
        self.assertEqual("TRUE FALSE", rows[0]["description"])

    def test_same_cell_enum_and_incomplete_header_are_not_inferred(self):
        rows, _ = parse_layout_rows("Name          Description      Values       Offset\npump.state    Pump state       TRUE FALSE   17")
        self.assertEqual("TRUE FALSE", rows[0]["_extra"]["values"])
        incomplete = HEADER.replace("Output?", "Choices")
        rows, _ = parse_layout_rows(incomplete + "\n" + ROW)
        self.assertEqual("FALSE FALSE", rows[0]["_extra"]["input"])

    def test_grid_raw_extra_headers_and_each_cell_claim_are_preserved(self):
        row = grid()["records"][0]
        self.assertEqual({"Input?": "FALSE", "Output?": "FALSE", "Settings?": "TRUE"}, row["_extra"])
        claims = [c for c in row["_claims"] if c["field"].startswith("_extra:")]
        self.assertEqual(["Input?", "Output?", "Settings?"], [c["raw_header"] for c in claims])
        self.assertEqual(["FALSE", "FALSE", "TRUE"], [c["raw_value"] for c in claims])

    def test_reconcile_preserves_raw_extra_fields_without_mutating_inputs(self):
        strict, _ = parse_layout_rows(HEADER + "\n" + ROW)
        right = grid()["records"]
        original = deepcopy((strict, right))
        accepted, held, conflicts = _reconcile(strict, right)
        self.assertEqual(([], []), (held, conflicts))
        self.assertEqual(original, (strict, right))
        self.assertEqual("FALSE", accepted[0]["_extra"]["input"])
        self.assertEqual("FALSE", accepted[0]["_extra"]["Input?"])
        self.assertEqual(6, len([c for c in accepted[0]["_claims"] if c["field"].startswith("_extra:")]))

    def test_actual_extra_disagreement_is_held_not_overwritten(self):
        left = grid()["records"][0]
        left = {**left, "_extra": {"input": "TRUE"}, "_source": {**left["_source"], "region": "p1:l2"}}
        accepted, held, conflicts = _reconcile([left], grid()["records"])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(held))
        self.assertEqual("TRUE", held[0]["_extra"]["input"])
        self.assertEqual("FALSE", held[0]["_extra"]["Input?"])
        self.assertEqual("_extra:input", conflicts[0]["fields"][0]["field"])

    def test_duplicate_grid_extra_columns_with_conflicting_values_stay_held(self):
        result = grid(CELLS + ["TRUE"], HEADERS + ["Input?"])
        self.assertEqual([], result["records"])
        row = result["quarantined_records"][0]
        self.assertEqual("pdf-grid-column-ambiguous", row["code"])
        self.assertEqual(["FALSE", "TRUE"], [c["value"] for c in row["_claims"] if c["field"] == "_extra:input"])

    def test_access_conflict_and_previous_quarantine_never_cleared(self):
        strict, _ = parse_layout_rows(HEADER + "\n" + ROW.replace("RW     ", "R      "))
        right = grid()["records"]
        accepted, held, conflicts = _reconcile(strict, right)
        self.assertEqual([], accepted)
        self.assertIn("access", [f["field"] for f in conflicts[0]["fields"]])
        accepted, held, _ = _reconcile([], right, quarantined_records=held)
        self.assertEqual([], accepted)
        self.assertEqual(1, len(held))


if __name__ == "__main__":
    unittest.main()
