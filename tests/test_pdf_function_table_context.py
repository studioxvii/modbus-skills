from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import (  # noqa: E402
    _function_table_mask, discover_register_pages, parse_bbox_rows, parse_layout_rows,
)
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence  # noqa: E402


FUNCTION_TABLE = "\n".join([
    "2.4 Function code",
    "These codes select protocol operations, not register addresses.",
    "Code          Name                      Action",
    "0x03          Read register             Read one or more registers",
    "0x06          Set single register       Write one 16-bit register",
    "0x10          Set multiple registers    Write several registers",
])
REGISTER_TABLE = "\n".join([
    "Protocol Offset   Name                  Data Type    Access",
    "0x03              Synthetic Readback    uint16       R",
])


def bbox(text):
    root = Element("doc")
    page = SubElement(root, "page")
    for line_number, line in enumerate(text.splitlines()):
        for word in re.finditer(r"\S+", line):
            node = SubElement(page, "word", {
                "xMin": str(word.start() * 5), "xMax": str(word.end() * 5),
                "yMin": str(line_number * 15), "yMax": str(line_number * 15 + 10),
            })
            node.text = word.group()
    return tostring(root, encoding="unicode")


class FunctionTableContextTests(unittest.TestCase):
    def test_explicit_function_table_has_no_register_candidates_or_row_holds(self):
        for heading in ("2.4 Function code", "Supported Function Codes", "Modbus function codes"):
            with self.subTest(heading=heading):
                text = FUNCTION_TABLE.replace("2.4 Function code", heading)
                self.assertEqual(([], []), parse_layout_rows(text))
                self.assertEqual([], parse_bbox_rows(bbox(text)))
                self.assertEqual([], discover_register_pages(text))

    def test_standalone_operation_header_excludes_small_hex_codes(self):
        for header in ("Code  Name  Action", "Function Code  Description"):
            with self.subTest(header=header):
                text = header + "\n0x03  Read registers\n0x06  Write single register"
                self.assertEqual(([], []), parse_layout_rows(text))
                self.assertEqual([], discover_register_pages(text))

    def test_register_header_resumes_after_function_table(self):
        for separator in ("", "2.5 Registers\n"):
            with self.subTest(separator=separator):
                text = FUNCTION_TABLE + "\n" + separator + REGISTER_TABLE
                rows, _ = parse_layout_rows(text)
                self.assertEqual([3], [row["address_number"] for row in rows])
                self.assertEqual("Synthetic Readback", rows[0]["name"])
                self.assertEqual("protocol-offset", rows[0]["address_convention"])
                self.assertEqual([3], [row["address_number"] for row in parse_bbox_rows(bbox(text))])
                self.assertEqual([1], discover_register_pages(text))

    def test_function_table_cannot_borrow_previous_register_columns(self):
        text = REGISTER_TABLE + "\n" + FUNCTION_TABLE
        self.assertEqual([3], [r["address_number"] for r in parse_layout_rows(text)[0]])
        self.assertEqual([3], [r["address_number"] for r in parse_bbox_rows(bbox(text))])

    def test_real_register_header_can_include_function_code_column(self):
        text = ("Protocol Offset   Name                  Function Code   Data Type\n"
                "0x03              Synthetic Readback    03              uint16")
        self.assertEqual([3], [r["address_number"] for r in parse_layout_rows(text)[0]])
        self.assertEqual([1], discover_register_pages(text))

    def test_real_headerless_register_and_function_named_point_are_not_banned(self):
        text = "40056  16-bit int  Flow rate"
        self.assertEqual(1, len(parse_layout_rows(text)[0]))
        self.assertEqual([1], discover_register_pages(text))
        self.assertEqual([False], _function_table_mask(["40001  Function code"]))

    def test_function_page_does_not_inherit_register_page_discovery(self):
        self.assertEqual([1], discover_register_pages(REGISTER_TABLE + "\f" + FUNCTION_TABLE))

    def test_grid_parser_does_not_assign_code_header_address_semantics(self):
        result = parse_pdf_table_evidence(
            [["Code", "Name", "Action"], ["0x03", "Read register", "Read one or more registers"]],
            page_number=1, table_index=0,
        )
        self.assertEqual([], result["records"])
        self.assertEqual([], result["quarantined_records"])


if __name__ == "__main__":
    unittest.main()
