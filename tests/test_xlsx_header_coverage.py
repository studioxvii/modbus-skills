"""Public-safe XLSX regressions for contextual headers, not inferred engineering."""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.map_workflows import normalize_map  # noqa: E402
from modbus_skills.parsers import parse_xlsx  # noqa: E402


def workbook(sheets: list[tuple[list[list[object]], set[int]]]) -> bytes:
    """Small OOXML fixtures; no spreadsheet package or formula evaluation."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for sheet_index, (rows, hidden_rows) in enumerate(sheets, 1):
            worksheet = ET.Element("worksheet")
            data = ET.SubElement(worksheet, "sheetData")
            for row_index, values in enumerate(rows, 1):
                row = ET.SubElement(data, "row", r=str(row_index))
                if row_index in hidden_rows:
                    row.set("hidden", "1")
                for column_index, value in enumerate(values):
                    cell = ET.SubElement(row, "c", r=f"{chr(65 + column_index)}{row_index}")
                    if isinstance(value, (int, float)):
                        ET.SubElement(cell, "v").text = str(value)
                    else:
                        cell.set("t", "inlineStr")
                        ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(value)
            archive.writestr(f"xl/worksheets/sheet{sheet_index}.xml", ET.tostring(worksheet))
    return stream.getvalue()


class XlsxHeaderCoverageTests(unittest.TestCase):
    def test_dec_hex_context_keeps_hidden_visible_and_alternate_rows(self) -> None:
        source = workbook([
            ([
                ["Dec", "Hex", "Count", "R/W", "Datatype", "Name (EN)"],
                [256, "100", 2, "R", "UInt32", "Hidden counter"],
                [258, "102", 1, "R", "UInt16", "Visible status"],
            ], {2}),
            ([
                ["Register", "Registers", "Type", "Point Name", "Register Type"],
                [258, 1, "UInt16", "Alternate status", "Input Registers"],
            ], set()),
        ])
        parsed = parse_xlsx(source)
        self.assertEqual([256, 258, 258], [r["address"] for r in parsed["records"]])
        self.assertEqual("100", parsed["records"][0]["hex"])
        self.assertEqual(2, parsed["records"][0]["_source"]["row"])
        self.assertEqual("sheet1", parsed["records"][0]["_source"]["sheet"])
        self.assertTrue(any(w["code"] == "contextual_xlsx_address_header" for w in parsed["warnings"]))
        normalized = normalize_map(parsed)
        self.assertTrue(all(p["protocol_offset"] is None for p in normalized["points"]))
        self.assertIsNone(normalized["points"][0]["area"])
        self.assertIsNone(normalized["points"][0]["byte_order"])

    def test_dec_hex_without_point_table_context_is_not_a_register_map(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Dec", "Hex", "Name"], [16, "10", "Number example"],
        ], set())]))
        self.assertEqual([], parsed["records"])
        self.assertTrue(any(w["code"] == "skipped_non_register_worksheet" for w in parsed["warnings"]))

    def test_conflicting_dec_hex_retains_raw_evidence_and_unknown_basis(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Dec", "Hex", "Count", "Datatype", "Name"],
            [256, "102", 1, "UInt16", "Conflicting row"],
        ], set())]))
        self.assertEqual(256, parsed["records"][0]["address"])
        self.assertEqual("102", parsed["records"][0]["hex"])
        point = normalize_map(parsed)["points"][0]
        self.assertIsNone(point["protocol_offset"])
        self.assertIsNone(point["area"])

    def test_banner_yields_to_real_index_function_header_and_write_hold(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Holding Registers (Synthetic controller)", "# of Registers"],
            ["Index", "Point Name", "Datatype", "Function Code", "Scale"],
            [0, "Synthetic command", "I16", "06", 1],
        ], set())]))
        self.assertEqual(1, len(parsed["records"]))
        row = parsed["records"][0]
        self.assertEqual(0, row["address"])
        self.assertEqual("06", row["function_code"])
        self.assertEqual("Synthetic command", row["name"])
        self.assertEqual(3, row["_source"]["row"])
        self.assertEqual(2, next(a["row"] for a in parsed["assumptions"] if a["code"] == "xlsx_header_row"))
        self.assertIn("function-code.write-forbidden", {h["code"] for h in normalize_map(parsed)["holds"]})

    def test_bare_or_dnp3_index_is_not_promoted(self) -> None:
        for title in ("Example numbers", "DNP3 point table"):
            with self.subTest(title=title):
                parsed = parse_xlsx(workbook([([
                    [title], ["Index", "Point Name", "Datatype", "Function Code"],
                    [0, "Synthetic point", "I16", "06"],
                ], set())]))
                self.assertEqual([], parsed["records"])

    def test_dnp3_context_after_register_banner_is_not_promoted(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Holding Registers"], ["DNP3 point table"],
            ["Index", "Point Name", "Datatype", "Function Code"],
            [0, "Synthetic point", "I16", "06"],
        ], set())]))
        self.assertFalse(any(r.get("function_code") == "06" for r in parsed["records"]))

    def test_address_only_and_normal_dense_headers_keep_earliest_tie(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Address", "Name"], [0, "Synthetic first"], [1, "Synthetic second"],
        ], set())]))
        self.assertEqual([0, 1], [r["address"] for r in parsed["records"]])
        minimal = parse_xlsx(workbook([([["Address"], [0], [1]], set())]))
        self.assertEqual([0, 1], [r["address"] for r in minimal["records"]])

    def test_later_denser_header_does_not_drop_earlier_real_table_rows(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Address", "Name"],
            [0, "First table point"],
            ["Address", "Name", "Datatype", "Word Count"],
            [1, "Second table point", "uint16", 1],
        ], set())]))
        self.assertEqual(1, next(a["row"] for a in parsed["assumptions"] if a["code"] == "xlsx_header_row"))
        self.assertEqual(0, parsed["records"][0]["address"])
        self.assertEqual("First table point", parsed["records"][0]["name"])
        # Multiple tables are not independently segmented by this narrow fix;
        # later wider cells remain raw evidence rather than dropping prior data.
        self.assertEqual(["uint16", 1], parsed["records"][-1]["_extra"])

    def test_numeric_data_after_address_only_header_prevents_later_header_jump(self) -> None:
        parsed = parse_xlsx(workbook([([
            ["Address"], [0], ["Address", "Name", "Datatype"],
            [1, "Later point", "uint16"],
        ], set())]))
        self.assertEqual(1, next(a["row"] for a in parsed["assumptions"] if a["code"] == "xlsx_header_row"))
        self.assertEqual(0, parsed["records"][0]["address"])


if __name__ == "__main__":
    unittest.main()
