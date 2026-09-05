from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))

from modbus_skills.pdf_extraction import (  # noqa: E402
    _source_coverage,
    discover_register_pages,
    parse_bbox_rows,
    parse_layout_rows,
)
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence  # noqa: E402


class PdfContinuationCoverageTests(unittest.TestCase):
    HEADER = "Name  Description  Access  Input  Output  Config  Protocol offset"
    FIRST = "sample.alpha  Alarm condition  R  FALSE  TRUE  FALSE  7"
    SECOND = "sample.beta  Alarm condition  R  FALSE  TRUE  FALSE  18"
    CONTINUATION = (
        "sample.gamma  Alarm condition  R  FALSE  TRUE  FALSE  8\n"
        "sample.delta  Mode state  R  FALSE  TRUE  FALSE  103\n"
        "sample.epsilon  Alarm condition  R  FALSE  TRUE  FALSE  204\n"
        "sample.zeta  Channel state  R  FALSE  TRUE  FALSE  1204\n"
    )

    def document(self, second_header: bool) -> str:
        return (
            f"{self.HEADER}\n{self.FIRST}\n{self.SECOND}\f"
            + (self.HEADER + "\n" if second_header else "")
            + self.CONTINUATION
        )

    def test_clean_repeated_header_remains_complete(self) -> None:
        text = self.document(True)
        records, rejected = parse_layout_rows(text)
        self.assertEqual(6, len(records))
        self.assertEqual([], rejected)
        coverage = _source_coverage(records, rejected, [], discover_register_pages(text), True)
        self.assertEqual("complete", coverage["status"])
        self.assertEqual("not-asserted", coverage["full_source_fidelity"])

    def test_missing_continuation_rows_are_localized_not_silently_complete(self) -> None:
        text = self.document(False)
        records, rejected = parse_layout_rows(text)
        self.assertEqual(["7", "18", "103", "1204"], [r["source_register"] for r in records])
        self.assertEqual(2, len(rejected))
        self.assertEqual({"pdf-row-structure-unresolved"}, {r["code"] for r in rejected})
        self.assertEqual({(2, 1), (2, 3)}, {(r["page"], r["line"]) for r in rejected})
        self.assertTrue(all(r["_source"]["excerpt"] for r in rejected))
        coverage = _source_coverage(records, rejected, [], discover_register_pages(text), True)
        self.assertEqual([1, 2], coverage["covered_pages"])
        self.assertTrue(coverage["discovery_complete"])
        self.assertEqual("unknown", coverage["status"])

    def test_numbered_narrative_title_is_not_a_candidate(self) -> None:
        text = "Example 8123 Modbus Notes\n" + self.document(True)
        records, rejected = parse_layout_rows(text)
        self.assertEqual(6, len(records))
        self.assertNotIn("8123", [r["source_register"] for r in records])
        self.assertEqual([], rejected)
        self.assertEqual("sample.alpha", records[0]["name"])

    def test_numbered_title_is_not_absorbed_into_bare_offset_header(self) -> None:
        records, rejected = parse_layout_rows(
            "Example 8123 Modbus Notes\n"
            "Name  Description  Access  Offset\n"
            "sample.alpha  Alarm condition  R  7\n"
        )
        self.assertEqual([], rejected)
        self.assertEqual(1, len(records))
        self.assertEqual("sample.alpha", records[0]["name"])
        self.assertEqual("Alarm condition", records[0]["description"])
        self.assertEqual("unknown", records[0]["address_convention"])

    def test_bare_offset_header_retains_raw_values_without_base_or_bias(self) -> None:
        text = "Name  Access  Offset\nExample A  R  7\nExample B  R  40104\n"
        records, rejected = parse_layout_rows(text)
        self.assertEqual([], rejected)
        self.assertEqual(["7", "40104"], [r["source_offset"] for r in records])
        for row in records:
            self.assertEqual("unknown", row["address_convention"])
            self.assertEqual(row["source_offset"], row["source_address"]["raw"])
            self.assertNotIn("protocol_offset", row)
            self.assertNotIn("display_address", row)
            self.assertNotIn("engineering_offset", row)
        self.assertEqual(40104, records[1]["address_number"])

    def test_separate_offset_does_not_replace_explicit_address(self) -> None:
        records, rejected = parse_layout_rows(
            "Address  Name  Access  Offset\n40001  Level  R  3\n"
        )
        self.assertEqual([], rejected)
        self.assertEqual("40001", records[0]["source_register"])
        self.assertEqual("3", records[0]["source_offset"])
        self.assertNotIn("engineering_offset", records[0])

    def test_grid_offset_does_not_infer_reference_from_digits_or_area(self) -> None:
        result = parse_pdf_table_evidence(
            [["Name", "Offset", "Access", "Area"], ["Example", "40104", "R", "holding register"]],
            page_number=1,
            table_index=0,
        )
        self.assertEqual([], result["quarantined_records"])
        row = result["records"][0]
        self.assertEqual("40104", row["source_register"])
        self.assertEqual("unknown", row["address_convention"])
        self.assertNotIn("display_address", row)

    def test_coordinate_offset_keeps_unknown_convention(self) -> None:
        words = []
        for y, cells in [(10, ["Name", "Offset", "Access"]), (30, ["Example", "40104", "R"])]:
            for x, word in zip([10, 150, 250], cells):
                words.append(f'<word xMin="{x}" yMin="{y}" xMax="{x+40}" yMax="{y+8}">{word}</word>')
        rows = parse_bbox_rows("<doc><page>" + "".join(words) + "</page></doc>")
        self.assertEqual(1, len(rows))
        self.assertEqual("40104", rows[0]["source_offset"])
        self.assertEqual("unknown", rows[0]["address_convention"])
        self.assertNotIn("display_address", rows[0])


if __name__ == "__main__":
    unittest.main()
