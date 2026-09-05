"""Public synthetic workbook legends and worksheet-qualified source evidence."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.compiler import compile_user_map  # noqa: E402
from modbus_skills.parsers import parse_xlsx  # noqa: E402
from modbus_skills.source_intake import _source_ref, compile_source_descriptor  # noqa: E402


HEADER = ["Parameter Name", "Modbus Data Type", "Modbus Function Code", "Modbus Address Read", "Length Read"]
LEGEND = [["Modbus Data Types"], ["Modbus Data Type", "Description", "", "Range"],
          ["UINT16", "16-bit unsigned integer (1 word)", "", "0 to 65535"],
          ["FLOAT32", "32-bit value (2 words)", "", "-1e30 to +1e30"],
          ["BOOLEAN", "Two states (0=OFF,1=ON)", "", "-"]]


def workbook(sheets):
    stream = io.BytesIO()
    book = ET.Element("workbook", xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main")
    sheet_nodes = ET.SubElement(book, "sheets")
    relationships = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    with zipfile.ZipFile(stream, "w") as archive:
        for index, (name, rows) in enumerate(sheets, 1):
            ET.SubElement(sheet_nodes, "sheet", {"name": name, "sheetId": str(index), "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id": f"rId{index}"})
            ET.SubElement(relationships, "Relationship", {"Id": f"rId{index}", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", "Target": f"worksheets/sheet{index}.xml"})
            worksheet = ET.Element("worksheet")
            data = ET.SubElement(worksheet, "sheetData")
            for number, values in enumerate(rows, 1):
                row = ET.SubElement(data, "row", r=str(number))
                for column, value in enumerate(values):
                    cell = ET.SubElement(row, "c", r=f"{chr(65+column)}{number}")
                    if isinstance(value, dict):
                        cell.set("t", "str")
                        ET.SubElement(cell, "f").text = value["formula"]
                        ET.SubElement(cell, "v").text = value["cached"]
                    elif isinstance(value, (int, float)):
                        ET.SubElement(cell, "v").text = str(value)
                    else:
                        cell.set("t", "inlineStr")
                        ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(value)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(worksheet))
        archive.writestr("xl/workbook.xml", ET.tostring(book))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
    return stream.getvalue()


class SourceWorkbookFidelityTests(unittest.TestCase):
    def test_datatype_legend_cannot_borrow_sample_register_header(self):
        parsed = parse_xlsx(workbook([("Format", [HEADER, *LEGEND])]))
        self.assertEqual([], parsed["records"])
        excluded = [a for a in parsed["assumptions"] if a["code"] == "excluded_xlsx_datatype_legend"]
        self.assertEqual([2, 3, 4, 5, 6], excluded[0]["rows"])
        self.assertEqual("0 to 65535", excluded[0]["source_rows"][2]["values"][3])

    def test_real_points_before_and_after_legend_survive(self):
        rows = [HEADER, ["First point", "UINT16", 3, 10, 1], *LEGEND,
                HEADER, ["Last point", "FLOAT32", 3, 12, 2]]
        parsed = parse_xlsx(workbook([("Registers", rows)]))
        real = [r for r in parsed["records"] if isinstance(r.get("address"), int)]
        self.assertEqual([10, 12], [r["address"] for r in real])
        self.assertEqual(["First point", "Last point"], [r["name"] for r in real])

    def test_datatype_named_real_registers_are_not_blacklisted(self):
        rows = [HEADER, ["UINT16", "UINT16", 3, 400001, 1],
                ["Range", "UINT16", 3, 400002, 1], ["Modbus Data Types", "UINT16", 3, 400003, 1]]
        self.assertEqual([400001, 400002, 400003], [r["address"] for r in parse_xlsx(workbook([("Registers", rows)]))["records"]])

    def test_ambiguous_register_address_remains_a_candidate(self):
        rows = [HEADER, ["Unresolved point", "UINT16", 3, "not confirmed", 1]]
        parsed = parse_xlsx(workbook([("Registers", rows)]))
        self.assertEqual("not confirmed", parsed["records"][0]["address"])

    def test_formula_backed_legend_like_rows_are_not_silently_excluded(self):
        for row_index, column in ((0, 0), (1, 0), (2, 0)):
            with self.subTest(row_index=row_index):
                legend = [list(row) for row in LEGEND]
                cached = legend[row_index][column]
                legend[row_index][column] = {"formula": '"' + cached + '"', "cached": cached}
                parsed = parse_xlsx(workbook([("Format", [HEADER, *legend])]))
                self.assertFalse(any(a["code"] == "excluded_xlsx_datatype_legend" for a in parsed["assumptions"]))
                self.assertTrue(any(r.get("name") == "UINT16" for r in parsed["records"]))

    def test_legend_named_heading_before_real_address_header_is_not_excluded(self):
        rows = [["Modbus Data Types"], HEADER, ["Real point", "UINT16", 3, 10, 1]]
        self.assertEqual([10], [r["address"] for r in parse_xlsx(workbook([("Registers", rows)]))["records"]])

    def test_worksheet_refs_are_unambiguous_and_preserve_raw_locators(self):
        refs = [_source_ref({"format": "xlsx", "sheet": sheet, "row": 3}, 0)
                for sheet in ("Alpha", "Beta", "A:B", "A%3AB", "测量")]
        self.assertEqual(5, len({ref["record_id"] for ref in refs}))
        self.assertEqual("xlsx:sheet:Alpha:row:3", refs[0]["record_id"])
        self.assertEqual({"format": "xlsx", "sheet": "Alpha", "row": 3}, {k: refs[0][k] for k in ("format", "sheet", "row")})

    def test_existing_nonworksheet_refs_remain_unchanged(self):
        self.assertEqual({"record_id": "csv:9"}, _source_ref({"format": "csv", "row": 9}, 0))
        self.assertEqual({"record_id": "json:0"}, _source_ref({"format": "json", "index": 0}, 0))
        self.assertEqual({"page_index": 2, "row_index": 4}, _source_ref({"format": "pdf", "page": 2, "line": 4}, 0))

    def test_compiled_json_csv_and_field_evidence_preserve_sheet_and_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.xlsx"
            source.write_bytes(workbook([(name, [["Synthetic registers"], HEADER, [name + " point", "UINT16", 3, address, 1]]) for name, address in (("Alpha", 10), ("Beta", 20))]))
            oem, _ = compile_source_descriptor({"path": str(source)})
            self.assertEqual({("Alpha", 3), ("Beta", 3)}, {(p["source_refs"][0]["sheet"], p["source_refs"][0]["row"]) for p in oem["points"]})
            self.assertTrue(all(p["protocol_offset"] is None for p in oem["points"]))
            for point in oem["points"]:
                self.assertTrue(all(e["source_ref"] == point["source_refs"][0]["record_id"] for e in point.get("source_field_evidence", [])))
            request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)}, "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all documented Modbus read points"]}, "targets": [], "target_options": {}}
            result = compile_user_map(request, root / "case")
            self.assertEqual("partial", result["state"])
            output = json.loads((root / "case/output/user-map.json").read_text())
            self.assertEqual({("Alpha", 3), ("Beta", 3)}, {(p["source_refs"][0]["sheet"], p["source_refs"][0]["row"]) for p in output["points"]})
            csv_rows = list(csv.DictReader(io.StringIO((root / "case/output/user-map.csv").read_text())))
            self.assertEqual({"xlsx:sheet:Alpha:row:3", "xlsx:sheet:Beta:row:3"}, {r["evidence_refs"] for r in csv_rows})


if __name__ == "__main__":
    unittest.main()
