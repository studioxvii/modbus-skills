from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import (  # noqa: E402
    _non_register_context_mask, discover_register_pages, parse_bbox_rows, parse_layout_rows,
)


OPTIONS = "Serial communication settings\nBAUD rate setting  4800  9600  19200  38400  57600  115200"
REGISTER = ("Display Address   Name                  Data Type    Access\n"
            "115200            Baud rate setting     uint16       R")


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


class BaudOptionContextTests(unittest.TestCase):
    def test_explicit_baud_option_lists_are_not_register_rows(self):
        for text in (OPTIONS, "Baud options:  1200  2400  460800",
                     "BAUD rate setting Press 4800 9600 19200 38400 57600 115200 Press",
                     "Baud rate choices:  100001, 100002, 100003"):
            with self.subTest(text=text):
                self.assertEqual(([], []), parse_layout_rows(text))
                self.assertEqual([], parse_bbox_rows(bbox(text)))
                self.assertEqual([], discover_register_pages(text))

    def test_settings_heading_ends_previous_register_columns(self):
        text = REGISTER + "\n" + OPTIONS
        self.assertEqual(["115200"], [row["source_register"] for row in parse_layout_rows(text)[0]])
        self.assertEqual(["115200"], [row["source_register"] for row in parse_bbox_rows(bbox(text))])

    def test_real_six_digit_reference_and_baud_name_survive(self):
        for text in (REGISTER, OPTIONS + "\n" + REGISTER):
            with self.subTest(text=text):
                for rows in (parse_layout_rows(text)[0], parse_bbox_rows(bbox(text))):
                    self.assertEqual(1, len(rows))
                    self.assertEqual("115200", rows[0]["source_register"])
                    self.assertEqual("Baud rate setting", rows[0]["name"])
                    self.assertEqual("modicon-reference", rows[0]["address_convention"])
                self.assertEqual([1], discover_register_pages(text))

    def test_name_first_real_register_table_has_precedence_over_option_shape(self):
        text = ("Name                 Protocol Offset   Minimum   Maximum   Default\n"
                "Baud rate setting    1200              2400      4800      9600")
        for rows in (parse_layout_rows(text)[0], parse_bbox_rows(bbox(text))):
            self.assertEqual(1, len(rows))
            self.assertEqual("Baud rate setting", rows[0]["name"])
            self.assertEqual(1200, rows[0]["address_number"])
            self.assertEqual("protocol-offset", rows[0]["address_convention"])

    def test_headerless_actual_register_is_not_keyword_blacklisted(self):
        text = "115200  uint16  Baud rate setting"
        self.assertEqual(1, len(parse_layout_rows(text)[0]))
        self.assertEqual([1], discover_register_pages(text))
        # A lone value is not enough evidence of an options list. Whether a
        # headerless name-first row parses is outside this contextual filter.
        self.assertEqual([False], _non_register_context_mask(["Baud rate setting  115200"]))

    def test_non_option_residual_evidence_is_preserved(self):
        text = "Baud rate setting  115200  unknown-unit"
        rows, rejected = parse_layout_rows(text)
        self.assertEqual(1, len(rows) + len(rejected))

    def test_setting_name_plus_unlabelled_numbers_is_not_proved_menu_context(self):
        text = "Baud rate setting  100001  100002  100003"
        self.assertEqual([False], _non_register_context_mask([text]))

    def test_settings_page_does_not_inherit_previous_page_discovery(self):
        self.assertEqual([1], discover_register_pages(REGISTER + "\f" + OPTIONS))


if __name__ == "__main__":
    unittest.main()
