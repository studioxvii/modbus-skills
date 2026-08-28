from __future__ import annotations

import json
import sys
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))
PDF_FIXTURES = ROOT / "tests" / "fixtures" / "pdf-extraction"

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
        self.assertEqual([1, 2, 257], [row["address_number"] for row in records])
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
        self.assertEqual(0x43, prepared["address_number"])
        self.assertEqual(
            {"raw": "0x0043", "convention": "protocol-offset"},
            prepared["source_address"],
        )
        self.assertNotIn("protocol_offset", prepared)
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

    def test_parses_symbolic_register_fixture_as_display_addresses(self) -> None:
        table = json.loads(
            (PDF_FIXTURES / "symbolic-register-table.json").read_text(
                encoding="utf-8"
            )
        )

        evidence = parse_pdf_table_evidence(table, page_number=4, table_index=0)

        self.assertEqual(
            ["REG_SYNTHETIC_INPUT", "REG_SYNTHETIC_HOLDING"],
            [row["name"] for row in evidence["records"]],
        )
        self.assertEqual(
            ["3x1000", "4x1161"],
            [row["source_register"] for row in evidence["records"]],
        )
        self.assertEqual(
            ["31000", "41161"],
            [row["display_address"] for row in evidence["records"]],
        )
        self.assertEqual(
            ["input-register", "holding-register"],
            [row["area"] for row in evidence["records"]],
        )
        self.assertTrue(
            all("protocol_offset" not in row for row in evidence["records"])
        )
        self.assertEqual(
            {"Min": "0", "Max": "100", "Step": "1"},
            evidence["records"][0]["_extra"],
        )
        self.assertEqual(
            ["pdf-grid-address-ambiguous"],
            [row["code"] for row in evidence["quarantined_records"]],
        )
        self.assertEqual(4, evidence["records"][0]["_source"]["page"])

    def test_accepts_display_address_grammar_without_emitting_offsets(self) -> None:
        table = [
            ["Reg addr", "R/W", "Data Type", "Meaning"],
            ["33000", "R", "uint16", "Input display reference"],
            ["40001", "R", "uint16", "Holding display reference"],
            ["0x1A", "R", "uint16", "Hex source address"],
            ["3x1000", "R", "uint16", "Input shorthand"],
            ["4x1161", "R", "uint16", "Holding shorthand"],
            ["40001/40002", "R", "uint32", "Two-word display pair"],
            ["33004-33019", "R", "uint16", "Range"],
            ["4x3x1160", "R", "uint16", "Mixed prefix"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=6, table_index=0)
        records = {row["source_register"]: row for row in evidence["records"]}

        self.assertEqual(
            {"33000", "40001", "0x1A", "3x1000", "4x1161", "40001/40002"},
            set(records),
        )
        self.assertEqual("33000", records["33000"]["display_address"])
        self.assertEqual("31000", records["3x1000"]["display_address"])
        self.assertEqual("41161", records["4x1161"]["display_address"])
        self.assertEqual(2, records["40001/40002"]["word_count"])
        self.assertEqual("protocol-offset", records["0x1A"]["address_convention"])
        self.assertTrue(all("protocol_offset" not in row for row in records.values()))
        self.assertEqual(
            {
                "pdf-grid-address-range-unresolved",
                "pdf-grid-address-ambiguous",
            },
            {row["code"] for row in evidence["quarantined_records"]},
        )

    def test_address_and_name_without_type_are_quarantined(self) -> None:
        table = [
            ["Register number", "Meaning", "Remarks"],
            ["40001", "Synthetic status", "Read only"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=8, table_index=0)

        self.assertEqual([], evidence["records"])
        self.assertEqual(
            ["pdf-grid-type-unresolved"],
            [row["code"] for row in evidence["quarantined_records"]],
        )

    def test_joins_wrapped_grid_header_and_quarantines_bit_list(self) -> None:
        table = [
            ["Register", "Meaning", "Data", "Unit", "Remarks"],
            ["Address", None, "Type", None, None],
            ["(Decimal)", None, None, None, None],
            ["12510", "Synthetic state", "uint16", "%", "Read only"],
            ["12514", "Reserved", None, None, "0—No 1—Yes"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=7, table_index=0)

        self.assertEqual(["12510"], [row["source_register"] for row in evidence["records"]])
        self.assertEqual("Synthetic state", evidence["records"][0]["name"])
        self.assertEqual("uint16", evidence["records"][0]["format"])
        self.assertEqual(
            {"Unit": "%", "Remarks": "Read only"},
            evidence["records"][0]["_extra"],
        )
        self.assertEqual(
            ["pdf-grid-bit-list-vs-register-unresolved"],
            [row["code"] for row in evidence["quarantined_records"]],
        )

    def test_recognizes_modbus_address_and_contents_header_aliases(self) -> None:
        table = [
            ["Code", "Modbus", "Contents", "Type"],
            [None, "Address", None, None],
            ["3", "40001", "Status word", "uint16"],
        ]

        evidence = parse_pdf_table_evidence(table, page_number=5, table_index=0)

        self.assertEqual([], evidence["quarantined_records"])
        self.assertEqual(
            ["40001"], [row["source_register"] for row in evidence["records"]]
        )
        self.assertEqual("Status word", evidence["records"][0]["name"])
        self.assertEqual("uint16", evidence["records"][0]["format"])

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
