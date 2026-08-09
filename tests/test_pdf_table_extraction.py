from __future__ import annotations

import sys
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.pdf_table_extraction import (  # noqa: E402
    PdfTableExtractionError,
    extract_pdf_table_rows,
    parse_pdf_table,
    prepare_pdf_records,
)


class PdfTableExtractionTests(unittest.TestCase):
    def test_same_address_in_different_tables_has_distinct_source_identity(self) -> None:
        table = [
            ["Address", "Access", "Format", "Description"],
            ["001", "R", "UInt", "Status"],
        ]

        raw_first = parse_pdf_table(table, page_number=4, table_index=0)[0]
        raw_second = parse_pdf_table(table, page_number=4, table_index=1)[0]
        first, second = prepare_pdf_records(
            {"records": [raw_first, raw_second]}
        )["records"]

        self.assertNotEqual(first["logical_point_id"], second["logical_point_id"])
        self.assertEqual("p4:t0:r1", first["_source"]["region"])
        self.assertEqual("p4:t1:r1", second["_source"]["region"])

    def test_parses_oem_grid_with_pairs_stars_and_merged_cells(self) -> None:
        table = [
            [
                "Model A",
                "Model B",
                "REG.",
                "R/W",
                "NV",
                "Format",
                "Units",
                "Scale",
                "Range",
                "Description",
                None,
            ],
            ["Integer Data", None, None, None, None, None, None, None, None, None, None],
            ["•", "•", "001", "R", "NV", "ULong", "kWh", "E", "0-0xFFFF", "Energy (MSR)", None],
            ["•", "•", "002", None, None, None, None, None, "0-0xFFFF", "Energy (LSR)", None],
            ["•", "•", "257/258*", "R", "NV", "Float", "kWh", "", "", "Float energy", None],
        ]

        records = parse_pdf_table(table, page_number=19, table_index=0)

        self.assertEqual(["001", "002", "257/258*"], [row["source_register"] for row in records])
        self.assertEqual([1, 2, 257], [row["address"] for row in records])
        self.assertTrue(all("protocol_offset" not in row for row in records))
        self.assertEqual([1, 1, 2], [row["word_count"] for row in records])
        self.assertEqual("R", records[1]["access"])
        self.assertEqual("ULong", records[1]["format"])
        self.assertEqual("pdfplumber-table/v1", records[2]["_source"]["parser_id"])
        self.assertEqual("p19:t0:r4", records[2]["_source"]["region"])

    def test_ignores_non_register_tables(self) -> None:
        table = [["Command", "Description"], ["0x03", "Read Holding Registers"]]

        self.assertEqual([], parse_pdf_table(table, page_number=18, table_index=0))

    def test_prepares_explicit_ulong_word_pair_without_assuming_offset_convention(self) -> None:
        table = [
            ["REG.", "R/W", "Format", "Description"],
            ["001", "R", "ULong", "Energy (MSR)"],
            ["002", None, None, "Energy (LSR)"],
        ]

        prepared = prepare_pdf_records(
            {"records": parse_pdf_table(table, page_number=19, table_index=0)}
        )["records"]

        self.assertEqual(1, len(prepared))
        self.assertEqual("001/002", prepared[0]["source_register"])
        self.assertEqual(1, prepared[0]["address"])
        self.assertNotIn("protocol_offset", prepared[0])
        self.assertEqual("uint32", prepared[0]["datatype"])
        self.assertEqual(2, prepared[0]["word_count"])
        self.assertEqual("ABCD", prepared[0]["byte_order"])
        self.assertTrue(prepared[0]["byte_order_confirmed"])

    def test_selected_page_and_worker_time_are_bounded(self) -> None:
        with self.assertRaisesRegex(PdfTableExtractionError, "256 selected pages"):
            extract_pdf_table_rows(Path("map.pdf"), pages=range(1, 258))

        process = mock.Mock()
        process.wait.side_effect = [
            subprocess.TimeoutExpired(["grid-worker"], timeout=60),
            0,
        ]
        with mock.patch(
            "modbus_skills.pdf_table_extraction.subprocess.Popen",
            return_value=process,
        ), self.assertRaisesRegex(PdfTableExtractionError, "60 second limit"):
            extract_pdf_table_rows(Path("map.pdf"), pages=[1])
        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
