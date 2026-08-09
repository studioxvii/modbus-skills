from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.pdf_extraction import PdfExtractionError, _call, extract_pdf  # noqa: E402


VERSION = subprocess.CompletedProcess([], 0, b"", b"pdftotext version 25.06.0\n")
HELP = subprocess.CompletedProcess(
    [],
    0,
    b"",
    b"-f <int> -l <int> -layout -bbox-layout -enc <string>\n",
)


def completed(stdout: bytes = b"", *, stderr: bytes = b"", code: int = 0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def bbox(*rows: tuple[str, str, str]) -> bytes:
    body = []
    y = 10
    for address, name, datatype in (("Address", "Name", "Data Type"), *rows):
        body.extend(
            (
                f'<word xMin="10" yMin="{y}" xMax="50" yMax="{y + 8}">{address}</word>',
                f'<word xMin="100" yMin="{y}" xMax="180" yMax="{y + 8}">{name}</word>',
                f'<word xMin="250" yMin="{y}" xMax="320" yMax="{y + 8}">{datatype}</word>',
            )
        )
        y += 12
    return (
        '<doc><page width="600" height="800"><flow><block><line>'
        + "</line><line>".join(body)
        + "</line></block></flow></page></doc>"
    ).encode()


class PdfExtractionTests(unittest.TestCase):
    def extract(self, effects, *, name: str = "map.pdf", grid_rows=None):
        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which", return_value="/usr/bin/pdftotext"
        ), mock.patch(
            "modbus_skills.pdf_extraction._call", side_effect=effects
        ) as run_mock, mock.patch(
            "modbus_skills.pdf_extraction.extract_pdf_table_rows",
            return_value=[] if grid_rows is None else grid_rows,
        ):
            result = extract_pdf(Path(name), b"%PDF synthetic")
        return result, run_mock

    def test_clean_strict_and_coordinate_claims_need_no_human_hold(self) -> None:
        layout = b"Address  Name  Data Type\n40001  Tank Level  float32\n"
        result, _ = self.extract([VERSION, HELP, completed(layout), completed(bbox(("40001", "Tank Level", "float32")))])

        self.assertEqual("candidate", result["status"])
        self.assertEqual([], result["holds"])
        self.assertEqual("25.06.0", result["extractor"]["version"])
        self.assertIn("-bbox-layout", result["extractor"]["features"])
        self.assertEqual(1, len(result["records"]))
        parser_ids = {claim["parser_id"] for claim in result["records"][0]["_claims"]}
        self.assertEqual({"pdftotext-layout/v1", "pdftotext-bbox-layout/v1"}, parser_ids)

    def test_merged_cell_layout_falls_back_to_coordinate_rows_without_hold(self) -> None:
        layout = b"Address Name Data Type\n40001 Tank Level float32\n"
        result, _ = self.extract([VERSION, HELP, completed(layout), completed(bbox(("40001", "Tank Level", "float32")))])

        self.assertEqual("candidate", result["status"])
        self.assertEqual([], result["holds"])
        self.assertEqual("pdftotext-bbox-layout/v1", result["records"][0]["_source"]["parser_id"])
        self.assertIn("pdf-strict-parser-no-rows", {item["code"] for item in result["findings"]})

    def test_grid_recovery_runs_when_text_parsers_find_no_rows(self) -> None:
        layout = b"Modbus register map 001 002\n"
        grid_row = {
            "source_address": "257/258",
            "protocol_offset": 257,
            "word_count": 2,
            "access": "R",
            "format": "Float",
            "description": "Real Energy",
            "_source": {
                "format": "pdf",
                "page": 1,
                "row": 2,
                "region": "p1:t0:r2",
                "parser_id": "pdfplumber-table/v1",
                "method": "coordinate-derived",
                "excerpt": "257/258 | R | Float | Real Energy",
            },
        }
        result, _ = self.extract(
            [VERSION, HELP, completed(layout), completed(b"<doc><page/></doc>")],
            grid_rows=[grid_row],
        )

        self.assertEqual("candidate", result["status"])
        self.assertEqual([], result["holds"])
        self.assertEqual([grid_row], result["records"])
        self.assertIn("pdf-grid-recovery-used", {item["code"] for item in result["findings"]})

    def test_grid_recovery_does_not_require_pdftotext(self) -> None:
        grid_row = {
            "source_register": "001",
            "address": 1,
            "word_count": 1,
            "name": "Status",
            "description": "Status",
            "_source": {
                "format": "pdf",
                "page": 3,
                "row": 2,
                "region": "p3:t0:r2",
                "parser_id": "pdfplumber-table/v1",
                "method": "coordinate-derived",
                "excerpt": "001 | R | Status",
            },
        }
        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which", return_value=None
        ), mock.patch(
            "modbus_skills.pdf_extraction.extract_pdf_table_rows",
            return_value=[grid_row],
        ):
            result = extract_pdf(Path("map.pdf"), b"%PDF synthetic")

        self.assertEqual("candidate", result["status"])
        self.assertEqual([], result["holds"])
        self.assertEqual([grid_row], result["records"])

    def test_partial_text_rows_are_completed_by_grid_rows(self) -> None:
        layout = b"Address  Name  Data Type\n001  Status  uint16\n"
        grid_rows = [
            {
                "source_register": register,
                "address": int(register),
                "word_count": 1,
                "name": name,
                "description": name,
                "format": "UInt",
                "_source": {
                    "format": "pdf",
                    "page": 1,
                    "row": row,
                    "region": f"p1:t0:r{row}",
                    "parser_id": "pdfplumber-table/v1",
                    "method": "coordinate-derived",
                    "excerpt": f"{register} | R | {name}",
                },
            }
            for row, register, name in ((2, "001", "Status"), (3, "002", "Alarm"))
        ]

        result, _ = self.extract(
            [VERSION, HELP, completed(layout), completed(bbox(("001", "Status", "uint16")))],
            grid_rows=grid_rows,
        )

        self.assertEqual("candidate", result["status"])
        self.assertEqual({"Status", "Alarm"}, {row["name"] for row in result["records"]})
        self.assertIn("pdf-grid-recovery-used", {item["code"] for item in result["findings"]})

    def test_only_material_conflict_is_quarantined(self) -> None:
        layout = (
            b"Address  Name  Data Type\n"
            b"40001  Tank Level  float32\n"
            b"40003  Tank Temp  int16\n"
        )
        coordinates = bbox(("40002", "Tank Level", "float32"), ("40003", "Tank Temp", "int16"))
        result, _ = self.extract([VERSION, HELP, completed(layout), completed(coordinates)])

        self.assertEqual("held", result["status"])
        self.assertEqual("pdf-material-claim-conflict", result["holds"][0]["code"])
        self.assertEqual(1, len(result["records"]))
        self.assertEqual("Tank Temp", result["records"][0]["name"])
        self.assertEqual(1, len(result["quarantined_records"]))

    def test_discovers_register_pages_before_coordinate_fallback(self) -> None:
        layout = b"Introduction only\fAddress Name Data Type\n40001 Tank Level float32\n"
        result, run_mock = self.extract(
            [VERSION, HELP, completed(layout), completed(bbox(("40001", "Tank Level", "float32")))]
        )

        self.assertEqual([2], result["discovered_register_pages"])
        bbox_argv = run_mock.call_args_list[3].args[0]
        self.assertEqual("2", bbox_argv[bbox_argv.index("-f") + 1])
        self.assertEqual("2", bbox_argv[bbox_argv.index("-l") + 1])

    def test_missing_or_incompatible_capability_fails_once(self) -> None:
        with mock.patch("modbus_skills.pdf_extraction.shutil.which", return_value=None):
            missing = extract_pdf(Path("map.pdf"), b"pdf")
        self.assertEqual(["pdf-text-extractor-unavailable"], [h["code"] for h in missing["holds"]])

        result, run_mock = self.extract([VERSION, completed(stderr=b"-layout -f -l -enc")])
        self.assertEqual(["pdf-text-extractor-incompatible"], [h["code"] for h in result["holds"]])
        self.assertEqual(2, run_mock.call_count)

    def test_timeout_and_malformed_bbox_are_bounded_failures(self) -> None:
        timeout = subprocess.TimeoutExpired(["pdftotext"], timeout=60)
        timed_out, _ = self.extract([VERSION, HELP, timeout])
        self.assertEqual("pdf-text-extraction-timeout", timed_out["holds"][0]["code"])

        layout = b"Address Name Data Type\n40001 Tank Level float32\n"
        malformed, _ = self.extract([VERSION, HELP, completed(layout), completed(b"not xml")])
        self.assertEqual("pdf-coordinate-output-malformed", malformed["holds"][0]["code"])

    def test_shell_metacharacter_filename_is_passed_as_one_argv_item(self) -> None:
        filename = "map; touch SHOULD_NOT_EXIST.pdf"
        result, run_mock = self.extract(
            [VERSION, HELP, completed(b"Address  Name\n40001  Level\n"), completed(bbox(("40001", "Level", "uint16")))],
            name=filename,
        )
        self.assertEqual("candidate", result["status"])
        for call in run_mock.call_args_list:
            self.assertIsInstance(call.args[0], list)
        self.assertIn(filename, run_mock.call_args_list[2].args[0])

    def test_tool_output_is_stopped_at_the_memory_limit(self) -> None:
        with mock.patch("modbus_skills.pdf_extraction._MAX_TOOL_OUTPUT_BYTES", 8):
            with self.assertRaisesRegex(PdfExtractionError, "output exceeds 8 bytes"):
                _call([sys.executable, "-c", "print('0123456789')"], timeout=5)


if __name__ == "__main__":
    unittest.main()
