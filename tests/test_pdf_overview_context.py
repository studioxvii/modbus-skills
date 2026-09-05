from pathlib import Path
import re
import sys
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import _overview_context_mask, _reconcile, parse_bbox_rows, parse_layout_rows
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence


OVERVIEW = "\n".join([
    "MODBUS MAP OVERVIEW",
    "This is an overview of register blocks and the coil address grid.",
    "Holding Registers                                           Coils",
    "Modbus Address   Discrete Data   Modbus Function Code         Discrete Data   Modbus Function Code",
    "40000            Status Data     >>Read Holding               0 1 2 3 4 5 6 7",
    "                 (status and alarm states)                   .   .   309.   .   .   310 311 312",
])
TABLE = "\n".join([
    "Offset   Name          Data Type    Access",
    "17       Pump status   uint16       R",
])


def bbox(text):
    root = Element("doc")
    for text_page in text.split("\f"):
        page = SubElement(root, "page")
        for number, line in enumerate(text_page.splitlines()):
            for word in re.finditer(r"\S+", line):
                SubElement(page, "word", {
                    "xMin": str(word.start() * 5), "xMax": str(word.end() * 5),
                    "yMin": str(number * 15), "yMax": str(number * 15 + 10),
                }).text = word.group()
    return tostring(root, encoding="unicode")


class OverviewContextTests(unittest.TestCase):
    def test_overview_fragments_are_not_points_and_keep_locators(self):
        rows, rejected = parse_layout_rows(OVERVIEW, first_page=7)
        self.assertEqual([], rows)
        self.assertEqual([5, 6], [r["line"] for r in rejected])
        for item in rejected:
            self.assertEqual("pdf-overview-declaration", item["code"])
            self.assertEqual(7, item["page"])
            self.assertEqual(f"p7:l{item['line']}", item["_source"]["region"])
            self.assertEqual(OVERVIEW.splitlines()[item["line"] - 1].strip(), item["_source"]["excerpt"])
        self.assertEqual([], parse_bbox_rows(bbox(OVERVIEW)))

    def test_real_table_after_overview_resumes_without_basis_default(self):
        for separator in ("\n", "\nRegisters\n"):
            text = OVERVIEW + separator + TABLE
            rows, _ = parse_layout_rows(text)
            self.assertEqual(["Pump status"], [r["name"] for r in rows])
            self.assertEqual("unknown", rows[0]["address_convention"])
            self.assertNotIn("area", rows[0])
            self.assertNotIn("protocol_offset", rows[0])
            self.assertEqual(["Pump status"], [r["name"] for r in parse_bbox_rows(bbox(text))])

    def test_prior_table_columns_do_not_leak_into_overview(self):
        text = TABLE + "\n" + OVERVIEW
        self.assertEqual(["Pump status"], [r["name"] for r in parse_layout_rows(text)[0]])
        self.assertEqual(["Pump status"], [r["name"] for r in parse_bbox_rows(bbox(text))])

    def test_other_explicit_area_pair_is_also_overview_not_defaults(self):
        text = OVERVIEW.replace("Holding Registers", "Input Registers").replace("Coils", "Discrete Inputs")
        self.assertEqual([], parse_layout_rows(text)[0])
        self.assertEqual([], parse_bbox_rows(bbox(text)))

    def test_keyword_alone_or_single_area_does_not_ban_headerless_points(self):
        for intro in ("Overview", "Holding Registers", "Input Registers", "Coils", "Discrete Inputs"):
            for address in ("40056", "400056"):
                with self.subTest(intro=intro, address=address):
                    rows, _ = parse_layout_rows(intro + f"\n{address}  uint16  Pump rate")
                    self.assertEqual(1, len(rows))
                    self.assertEqual(address, rows[0]["source_register"])

    def test_new_page_or_register_heading_ends_overview_for_headerless_point(self):
        for separator in ("\f", "\nRegisters\n", "\n3. Input Registers\n"):
            rows, _ = parse_layout_rows(OVERVIEW + separator + "40056  uint16  Pump rate")
            self.assertEqual(["40056"], [r["source_register"] for r in rows])

    def test_incomplete_overview_structure_is_not_silently_excluded(self):
        variants = (
            OVERVIEW.replace("OVERVIEW", "NOTES").replace("overview", "description"),
            OVERVIEW.replace("Modbus Function Code", "Operation", 1),
            OVERVIEW.replace("Coils", "Holding Register"),
        )
        for text in variants:
            with self.subTest(text=text):
                # Other existing parsers may reject incomplete table structures;
                # this new filter must not silently classify them as overviews.
                self.assertFalse(any(_overview_context_mask(text.splitlines())))
                _, rejected = parse_layout_rows(text)
                self.assertNotIn("pdf-overview-declaration", [r["code"] for r in rejected])

    def test_explicit_real_table_with_overview_named_point_and_function_column(self):
        text = ("Overview\nOffset   Name              Function Code   Data Type\n"
                "17       Overview status   03              uint16")
        rows, _ = parse_layout_rows(text)
        self.assertEqual(["Overview status"], [r["name"] for r in rows])

    def test_material_conflicts_and_previous_quarantines_still_hold(self):
        grid = parse_pdf_table_evidence(
            [["Offset", "Name", "Data Type", "Access"], ["17", "Pump status", "uint16", "RW"]],
            page_number=1, table_index=0,
        )["records"]
        strict, _ = parse_layout_rows(TABLE)
        accepted, held, conflicts = _reconcile(strict, grid)
        self.assertEqual([], accepted)
        self.assertEqual(1, len(held))
        self.assertEqual("access", conflicts[0]["fields"][0]["field"])
        accepted, held, _ = _reconcile([], grid, quarantined_records=held)
        self.assertEqual([], accepted)
        self.assertEqual(1, len(held))


if __name__ == "__main__":
    unittest.main()
