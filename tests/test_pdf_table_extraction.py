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
    extract_pdf_table_evidence,
    extract_pdf_table_rows,
    parse_pdf_table,
    parse_pdf_table_evidence,
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

    def test_preserves_shifted_oem_column_semantics(self) -> None:
        table = [
            ["Start", "Size", "R/W", "Type", "Units", "Scale Factor", "Description"],
            ["0x0043", "1", "R", "int16", "uF", "0", "Capacitance"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=7, table_index=2)
        prepared = prepare_pdf_records(evidence)["records"][0]

        self.assertEqual([], evidence["quarantined_records"])
        self.assertEqual(0x43, prepared["address"])
        self.assertEqual(1, prepared["word_count"])
        self.assertEqual("int16", prepared["datatype"])
        self.assertEqual("uF", prepared["engineering_unit"])
        self.assertEqual(0.0, prepared["scale"])
        self.assertEqual("Capacitance", prepared["description"])
        self.assertEqual("p7:t2:r1", prepared["_source"]["region"])
        claims = {claim["field"]: claim for claim in prepared["_claims"]}
        self.assertEqual("Start", claims["address"]["raw_header"])
        self.assertEqual("Type", claims["format"]["raw_header"])
        self.assertEqual("Scale Factor", claims["scale"]["raw_header"])

    def test_conflicting_duplicate_semantic_columns_are_quarantined(self) -> None:
        table = [
            ["Start", "R/W", "Type", "Data Type", "Description"],
            ["17", "R", "int16", "uint16", "Conflicting type"],
            ["18", "R", "int16", "int16", "Unambiguous type"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=8, table_index=0)

        self.assertEqual(["18"], [row["source_register"] for row in evidence["records"]])
        self.assertEqual(1, len(evidence["quarantined_records"]))
        self.assertEqual(
            "pdf-grid-column-ambiguous",
            evidence["quarantined_records"][0]["code"],
        )
        self.assertEqual(["format"], evidence["quarantined_records"][0]["fields"])

    def test_conflicting_duplicate_addresses_are_quarantined(self) -> None:
        table = [
            ["Start", "Address", "R/W", "Type", "Description"],
            ["17", "18", "R", "int16", "Conflicting address"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=9, table_index=0)

        self.assertEqual([], evidence["records"])
        self.assertEqual(1, len(evidence["quarantined_records"]))
        self.assertEqual(
            ["address"], evidence["quarantined_records"][0]["fields"]
        )

    def test_inherited_cells_reuse_the_original_source_claim(self) -> None:
        table = [
            ["Start", "R/W", "Type", "Units", "Description"],
            ["17", "R", "int16", "uF", "First"],
            ["18", "", "", "", "Second"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=10, table_index=0)
        claims = {
            claim["field"]: claim for claim in evidence["records"][1]["_claims"]
        }

        self.assertEqual("R", claims["access"]["raw_value"])
        self.assertEqual("int16", claims["format"]["raw_value"])
        self.assertEqual("uF", claims["units"]["raw_value"])
        self.assertEqual("p10:t0:r1", claims["units"]["source_locator"]["region"])

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

    def test_legacy_row_api_projects_records_from_evidence(self) -> None:
        payload = {"records": [{"address": 1}], "quarantined_records": []}
        with mock.patch(
            "modbus_skills.pdf_table_extraction._run_grid_worker",
            return_value=payload,
        ):
            self.assertEqual([{"address": 1}], extract_pdf_table_rows(Path("map.pdf")))
            self.assertEqual(payload, extract_pdf_table_evidence(Path("map.pdf")))


if __name__ == "__main__":
    unittest.main()
