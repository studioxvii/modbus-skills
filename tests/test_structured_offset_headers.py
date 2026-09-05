"""Contextual structured Offset rows retain evidence, not an address basis."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))

from modbus_skills.map_workflows import normalize_map
from modbus_skills.parsers import parse_csv, parse_json, parse_source, parse_xlsx


def workbook(rows):
    stream = io.BytesIO()
    root = ET.Element("worksheet")
    data = ET.SubElement(root, "sheetData")
    for number, values in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(number))
        for column, value in enumerate(values):
            cell = ET.SubElement(row, "c", r=f"{chr(65 + column)}{number}", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(value)
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(root))
    return stream.getvalue()


class StructuredOffsetHeaderTests(unittest.TestCase):
    def assert_unresolved(self, parsed, area=None):
        self.assertEqual(1, len(parsed["records"]))
        row = parsed["records"][0]
        self.assertEqual("0", row["source_offset"])
        self.assertNotIn("protocol_offset", row)
        self.assertNotIn("address", row)
        self.assertTrue(any(w["code"] == "ambiguous_offset_header" for w in parsed["warnings"]))
        point = normalize_map(parsed)["points"][0]
        self.assertIsNone(point["protocol_offset"])
        self.assertEqual(area, point["area"])
        self.assertIsNone(point["byte_order"])
        return row

    def test_xlsx_contextual_header_after_multicell_banner(self):
        parsed = parse_xlsx(workbook([
            ["Modbus Coils", "Synthetic context"],
            ["Description", "Access", "Offset"],
            ["Synthetic alarm", "R", "0"],
        ]))
        row = self.assert_unresolved(parsed)
        self.assertEqual("Synthetic alarm", row["description"])
        self.assertEqual("R", row["access"])
        self.assertEqual(3, row["_source"]["row"])
        self.assertEqual(2, next(a["row"] for a in parsed["assumptions"] if a["code"] == "xlsx_header_row"))

    def test_csv_keeps_exact_source_fields_and_unknown_convention(self):
        row = self.assert_unresolved(parse_csv("Name,Access,Offset\nSynthetic alarm,R,0\n"))
        self.assertEqual("Synthetic alarm", row["name"])
        self.assertEqual("R", row["access"])

    def test_tabular_text_function_context_is_candidate_only(self):
        row = self.assert_unresolved(parse_source("Name\tFunction Code\tOffset\nSynthetic value\t03\t0\n", source_format="tsv"), area="holding-register")
        self.assertEqual("03", row["function_code"])

    def test_area_header_can_corroborate_offset_candidate(self):
        parsed = parse_csv("Name,Area,Offset\nSynthetic bit,coil,0\n")
        self.assertEqual("0", parsed["records"][0]["source_offset"])
        self.assertIsNone(normalize_map(parsed)["points"][0]["protocol_offset"])

    def test_write_marker_is_preserved_not_promoted_to_read(self):
        parsed = parse_csv("Name,Access,Offset\nSynthetic command,W,7\n")
        self.assertEqual("W", parsed["records"][0]["access"])
        normalized = normalize_map(parsed)
        self.assertEqual("write-only", normalized["points"][0]["access"])
        self.assertTrue(normalized["holds"])

    def test_ordinary_offset_text_is_not_a_register_table(self):
        for rows in (
            [["Description", "Offset"], ["Caption position", "8"]],
            [["Name", "Access", "Offset"], ["Paragraph", "public", "8"]],
            [["Name", "Access", "Offset"], ["Paragraph", "R", "left"]],
        ):
            with self.subTest(rows=rows):
                self.assertEqual([], parse_xlsx(workbook(rows))["records"])
                text = "\n".join(",".join(row) for row in rows) + "\n"
                self.assertEqual([], parse_csv(text)["records"])

    def test_missing_and_repeated_header_rows_are_disposed(self):
        parsed = parse_xlsx(workbook([
            ["Description", "Access", "Offset"],
            ["First", "R", "0"],
            ["Description", "Access", "Offset"],
            ["Second", "R", "1"],
            ["No offset", "R", ""],
        ]))
        self.assertEqual(["0", "1"], [r["source_offset"] for r in parsed["records"]])
        self.assertEqual([3, 5], [r["row"] for r in parsed["rejected_rows"]])

    def test_explicit_address_still_wins_and_plain_json_is_unchanged(self):
        parsed = parse_csv("Name,Access,Protocol Offset,Offset\nSynthetic value,R,3,9\n")
        self.assertEqual("3", parsed["records"][0]["protocol_offset"])
        self.assertEqual("9", parsed["records"][0]["source_offset"])
        self.assertEqual([], parse_json([{"Name": "Unscoped", "Access": "R", "Offset": 0}])["records"])


if __name__ == "__main__":
    unittest.main()
