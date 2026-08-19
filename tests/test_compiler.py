from __future__ import annotations

import copy
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import subprocess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler import CompilerError, compile_user_map
from modbus_skills.compiler_contracts import build_device_binding, build_oem_map
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence


def oem_map(*, multiword: bool = False) -> dict[str, object]:
    return build_oem_map(
        [
            {
                "oem_point_id": "temperature",
                "name": "Temperature",
                "area": "holding-register",
                "protocol_offset": 10,
                "datatype": "float32" if multiword else "uint16",
                "word_span": 2 if multiword else 1,
                "source_refs": [
                    {"page_index": 2, "row_index": 4, "region_id": "row-4"}
                ],
            }
        ],
        source_hash="a" * 64,
    )


def request(*, targets: list[str] | None = None, binding: dict | None = None) -> dict:
    source = oem_map()
    value = {
        "schema_version": "modbus-compile-request/v1",
        "oem_map": source,
        "selection_candidate": {
            "oem_map_hash": stable_input_hash(source),
            "requested_measurements": ["temperature"],
            "included": [
                {
                    "oem_point_id": "temperature",
                    "matched_intent": "temperature",
                    "match_quality": "exact",
                    "reason": "Exact requested measurement",
                    "evidence_refs": ["row-4"],
                }
            ],
            "suggested": [],
            "excluded": [],
        },
        "targets": targets or [],
        "target_options": {},
    }
    if binding is not None:
        value["binding"] = binding
    return value


def selection_pause_request() -> dict:
    value = request()
    entry = value["selection_candidate"]["included"].pop()
    entry["match_quality"] = "near"
    value["selection_candidate"]["suggested"] = [entry]
    return value


def selection_reply(case: dict, packet: dict) -> dict:
    return {
        "schema_version": "modbus-compile-resume/v1",
        "case_id": case["case_id"],
        "case_hash": stable_input_hash(case),
        "action": "provide-selection-decision",
        "decision_candidate": {
            "schema_version": "modbus-compiler-decision-candidate/v1",
            "case_id": packet["case_id"],
            "phase": packet["phase"],
            "packet_id": packet["packet_id"],
            "source_hash": packet["source_hash"],
            "input_hashes": copy.deepcopy(packet["input_hashes"]),
            "decisions": [
                {
                    "decision_id": "selection.choose-included-points",
                    "disposition": "include-specified",
                    "selected_subject_ids": ["temperature"],
                    "reason": "The engineer selected the temperature point.",
                    "evidence_refs": ["row-4"],
                }
            ],
        },
    }


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_clean_offline_compile_finishes_in_one_call_and_is_idempotent(self) -> None:
        case_root = self.root / "case"
        result = compile_user_map(request(), case_root)

        self.assertEqual(result["state"], "offline-complete")
        self.assertEqual(result["next_action"]["kind"], "none")
        self.assertEqual(result["target_statuses"], [])
        self.assertEqual(
            {"user-map.md", "user-map.json", "user-map.csv"},
            {path.name for path in (case_root / "output").iterdir()},
        )
        self.assertTrue((case_root / "compile-result.json").is_file())
        self.assertTrue((case_root / "case.json").is_file())
        self.assertEqual(result, compile_user_map(copy.deepcopy(request()), case_root))
        self.assertEqual(stat.S_IMODE(case_root.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((case_root / "case.json").stat().st_mode), 0o600
        )

    def test_selected_blocking_hold_cannot_report_offline_complete(self) -> None:
        held_map = build_oem_map(
            oem_map()["points"],
            source_hash="a" * 64,
            holds=[{
                "code": "point.datatype-unresolved",
                "severity": "hold",
                "blocking": True,
                "point_ids": ["temperature"],
                "message": "Declare the datatype.",
            }],
        )
        compile_request = request()
        compile_request["oem_map"] = held_map
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(held_map)

        result = compile_user_map(compile_request, self.root / "held-oem-case")

        self.assertEqual("partial", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])
        self.assertTrue((self.root / "held-oem-case" / "output" / "user-map.md").is_file())

    def test_unknown_declared_source_coverage_cannot_report_complete(self) -> None:
        source = build_oem_map(
            oem_map()["points"],
            source_hash="a" * 64,
            source_coverage={
                "status": "unknown",
                "accepted_row_count": 1,
                "rejected_row_count": 0,
                "quarantined_row_count": 0,
                "detected_pages": [2],
                "detected_regions": ["row-4"],
                "basis": "single-or-conflicting-parser-evidence",
                "discovery_complete": True,
            },
        )
        compile_request = request()
        compile_request["oem_map"] = source
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(source)

        result = compile_user_map(compile_request, self.root / "coverage-case")

        self.assertEqual("partial", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])

    def test_pdf_without_independent_field_evidence_cannot_report_complete(self) -> None:
        point = copy.deepcopy(oem_map()["points"][0])
        source = build_oem_map(
            [point],
            source_hash="a" * 64,
            source_reference={"filename": "synthetic.pdf", "format": "pdf"},
            source_coverage={
                "status": "complete",
                "accepted_row_count": 1,
                "rejected_row_count": 0,
                "quarantined_row_count": 0,
                "detected_pages": [2],
                "covered_pages": [2],
                "detected_regions": ["row-4"],
                "basis": "bounded-discovery",
                "discovery_complete": True,
                "independent_parser_row_count": 1,
                "single_parser_row_count": 0,
            },
        )
        compile_request = request()
        compile_request["oem_map"] = source
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(source)

        result = compile_user_map(compile_request, self.root / "pdf-evidence-case")

        self.assertEqual("partial", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])
        self.assertEqual(
            "artifacts/oem-map.json", result["next_action"]["evidence_artifact"]
        )
        evidence_issue = next(
            issue
            for issue in result["next_action"]["issues"]
            if issue["code"] == "pdf-field-evidence-unconfirmed"
        )
        self.assertEqual("temperature", evidence_issue["point_id"])
        self.assertEqual(
            ["datatype", "protocol_offset", "word_span"], evidence_issue["fields"]
        )

    def test_pdf_with_confirmed_field_evidence_can_complete(self) -> None:
        point = copy.deepcopy(oem_map()["points"][0])
        point["source_refs"] = [{"page_index": 2, "row_index": 4}]
        point["source_field_evidence"] = [
            {
                "field": "datatype",
                "raw_header": "Type",
                "raw_value": "uint16",
                "normalized_value": "uint16",
                "source_ref": "page-2-row-4",
                "status": "confirmed",
            },
            {
                "field": "protocol_offset",
                "raw_header": "Start",
                "raw_value": "10",
                "normalized_value": 10,
                "source_ref": "page-2-row-4",
                "status": "confirmed",
            },
        ]
        source = build_oem_map(
            [point],
            source_hash="a" * 64,
            source_reference={"filename": "synthetic.pdf", "format": "pdf"},
            source_coverage={
                "status": "complete",
                "accepted_row_count": 1,
                "rejected_row_count": 0,
                "quarantined_row_count": 0,
                "detected_pages": [2],
                "covered_pages": [2],
                "detected_regions": ["row-4"],
                "basis": "bounded-discovery",
                "discovery_complete": True,
                "independent_parser_row_count": 1,
                "single_parser_row_count": 0,
            },
        )
        compile_request = request()
        compile_request["oem_map"] = source
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(source)

        result = compile_user_map(compile_request, self.root / "confirmed-pdf-case")

        self.assertEqual("offline-complete", result["state"])

    def test_pdf_without_coverage_cannot_report_complete(self) -> None:
        point = copy.deepcopy(oem_map()["points"][0])
        point["source_field_evidence"] = [
            {
                "field": field,
                "raw_header": header,
                "raw_value": raw,
                "normalized_value": normalized,
                "source_ref": "row-4",
                "status": "confirmed",
            }
            for field, header, raw, normalized in (
                ("protocol_offset", "Start", "10", 10),
                ("datatype", "Type", "uint16", "uint16"),
            )
        ]
        source = build_oem_map(
            [point],
            source_hash="a" * 64,
            source_reference={"filename": "synthetic.pdf", "format": "pdf"},
        )
        compile_request = request()
        compile_request["oem_map"] = source
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(source)

        result = compile_user_map(compile_request, self.root / "pdf-no-coverage")

        self.assertEqual("partial", result["state"])

    def test_stale_pdf_evidence_cannot_report_complete(self) -> None:
        point = copy.deepcopy(oem_map()["points"][0])
        point["source_field_evidence"] = [
            {
                "field": "protocol_offset",
                "raw_header": "Start",
                "raw_value": "10",
                "normalized_value": 10,
                "source_ref": "row-4",
                "status": "confirmed",
            },
            {
                "field": "datatype",
                "raw_header": "Type",
                "raw_value": "uint16",
                "normalized_value": "uint16",
                "source_ref": "row-4",
                "status": "confirmed",
            },
            {
                "field": "word_span",
                "raw_header": "Size",
                "raw_value": "2",
                "normalized_value": 2,
                "source_ref": "row-4",
                "status": "confirmed",
            },
        ]
        source = build_oem_map(
            [point],
            source_hash="a" * 64,
            source_reference={"filename": "synthetic.pdf", "format": "pdf"},
            source_coverage={
                "status": "complete",
                "accepted_row_count": 1,
                "rejected_row_count": 0,
                "quarantined_row_count": 0,
                "detected_pages": [2],
                "covered_pages": [2],
                "detected_regions": ["row-4"],
                "basis": "bounded-discovery",
                "discovery_complete": True,
            },
        )
        compile_request = request()
        compile_request["oem_map"] = source
        compile_request["selection_candidate"]["oem_map_hash"] = stable_input_hash(source)

        result = compile_user_map(compile_request, self.root / "stale-pdf-evidence")

        self.assertEqual("partial", result["state"])

    def test_clean_structured_source_reaches_offline_bundle_in_one_call(self) -> None:
        source = self.root / "clean.csv"
        source.write_text(
            "logical_point_id,name,protocol_offset,area,datatype,access\n"
            "temperature,Temperature,10,holding-register,uint16,read-only\n",
            encoding="utf-8",
        )
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "csv"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["temperature"],
                "included": [
                    {
                        "oem_point_id": "temperature",
                        "matched_intent": "temperature",
                        "match_quality": "exact",
                        "reason": "Typed exact selection",
                        "evidence_refs": ["csv:row:2"],
                    }
                ],
                "suggested": [],
                "excluded": [],
            },
            "targets": [],
            "target_options": {},
        }

        result = compile_user_map(compile_request, self.root / "source-case")

        self.assertEqual("offline-complete", result["state"])
        self.assertEqual("none", result["next_action"]["kind"])
        user_map = json.loads((self.root / "source-case" / "output" / "user-map.json").read_text())
        self.assertEqual(["temperature"], [point["oem_point_id"] for point in user_map["points"]])

    def test_complete_map_intent_selects_every_readable_source_point(self) -> None:
        source = self.root / "complete.csv"
        source.write_text(
            "logical_point_id,name,protocol_offset,area,datatype,access\n"
            "temperature,Temperature,10,holding-register,uint16,read-only\n"
            "power,Power,11,holding-register,uint16,read-write\n"
            "setpoint,Setpoint,12,holding-register,uint16,write-only\n",
            encoding="utf-8",
        )
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "csv"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["all documented Modbus read points"],
                "mode": "all-readable",
            },
            "targets": [],
            "target_options": {},
        }

        result = compile_user_map(compile_request, self.root / "complete-map-case")

        self.assertEqual("offline-complete", result["state"])
        user_map = json.loads(
            (self.root / "complete-map-case" / "output" / "user-map.json").read_text()
        )
        self.assertEqual(
            ["temperature", "power"],
            [point["oem_point_id"] for point in user_map["points"]],
        )
        self.assertEqual(
            ["setpoint"],
            [
                item["oem_point_id"]
                for item in user_map["exception_annex"]
                if item.get("kind") == "excluded"
            ],
        )

    def test_existing_candidate_map_records_are_valid_structured_input(self) -> None:
        source = self.root / "candidate-map.json"
        source.write_text(
            json.dumps(
                {
                    "schema_version": "candidate-map/v1",
                    "format": "json",
                    "records": [
                        {
                            "logical_point_id": "status",
                            "name": "Status",
                            "protocol_offset": 20,
                            "area": "holding-register",
                            "datatype": "uint16",
                            "access": "read-only",
                            "_source": {"format": "json", "index": 0},
                        }
                    ],
                    "warnings": [],
                    "rejected_rows": [],
                    "assumptions": [],
                }
            ),
            encoding="utf-8",
        )
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source)},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["status"],
                "included": [{
                    "oem_point_id": "status",
                    "matched_intent": "status",
                    "match_quality": "exact",
                    "reason": "Typed exact selection",
                    "evidence_refs": ["json:0"],
                }],
                "suggested": [],
                "excluded": [],
            },
            "targets": [],
            "target_options": {},
        }

        result = compile_user_map(compile_request, self.root / "candidate-source-case")

        self.assertEqual("offline-complete", result["state"])

    def test_clean_pdf_source_uses_bounded_ladder_without_page_question(self) -> None:
        source = self.root / "clean; map.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic rights-safe fixture\n")
        layout = subprocess.CompletedProcess(
            [], 0,
            b"Protocol Offset  Name  Data Type  Area  Access\n10  Temperature  uint16  holding-register  read-only\n",
            b"",
        )
        bbox = subprocess.CompletedProcess(
            [], 0,
            (
                b'<doc><page><word xMin="10" yMin="10" xMax="50" yMax="18">Protocol</word>'
                b'<word xMin="55" yMin="10" xMax="90" yMax="18">Offset</word>'
                b'<word xMin="100" yMin="10" xMax="140" yMax="18">Name</word>'
                b'<word xMin="200" yMin="10" xMax="230" yMax="18">Data</word>'
                b'<word xMin="235" yMin="10" xMax="270" yMax="18">Type</word>'
                b'<word xMin="300" yMin="10" xMax="340" yMax="18">Area</word>'
                b'<word xMin="430" yMin="10" xMax="470" yMax="18">Access</word>'
                b'<word xMin="10" yMin="25" xMax="30" yMax="33">10</word>'
                b'<word xMin="100" yMin="25" xMax="170" yMax="33">Temperature</word>'
                b'<word xMin="200" yMin="25" xMax="250" yMax="33">uint16</word>'
                b'<word xMin="300" yMin="25" xMax="390" yMax="33">holding-register</word>'
                b'<word xMin="430" yMin="25" xMax="490" yMax="33">read-only</word>'
                b'</page></doc>'
            ),
            b"",
        )
        effects = [
            subprocess.CompletedProcess([], 0, b"", b"pdftotext version 25.06.0\n"),
            subprocess.CompletedProcess([], 0, b"", b"-f -l -layout -bbox-layout -enc\n"),
            layout,
            bbox,
        ]
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "pdf"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["temperature"],
                "included": [{
                    "exact_name": "Temperature",
                    "matched_intent": "temperature",
                    "match_quality": "exact",
                    "reason": "Typed exact-name selection",
                    "evidence_refs": ["pdf:p1"],
                }],
                "suggested": [],
                "excluded": [],
            },
            "targets": [],
            "target_options": {},
        }
        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which", return_value="/usr/bin/pdftotext"
        ), mock.patch(
            "modbus_skills.pdf_extraction._call", side_effect=effects
        ) as run_mock:
            result = compile_user_map(compile_request, self.root / "pdf-case")
            replay = compile_user_map(copy.deepcopy(compile_request), self.root / "pdf-case")

        self.assertEqual("offline-complete", result["state"])
        self.assertEqual(result, replay)
        self.assertEqual("none", result["next_action"]["kind"])
        self.assertEqual(4, run_mock.call_count)

    def test_pdf_grid_recovery_and_complete_intent_emit_offline_map(self) -> None:
        source = self.root / "oem-grid.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic rights-safe fixture\n")
        effects = [
            subprocess.CompletedProcess([], 0, b"", b"pdftotext version 25.06.0\n"),
            subprocess.CompletedProcess([], 0, b"", b"-f -l -layout -bbox-layout -enc\n"),
            subprocess.CompletedProcess([], 0, b"Modbus Point Map\n", b""),
        ]
        grid_rows = parse_pdf_table_evidence(
            [
                ["Start", "Size", "R/W", "Type", "Units", "Scale Factor", "Description"],
                ["67", "1", "R", "int16", "uF", "0", "Capacitance"],
            ],
            page_number=1,
            table_index=0,
        )["records"]
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "pdf"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["all documented Modbus read points"],
                "mode": "all-readable",
            },
            "targets": [],
            "target_options": {},
        }
        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which", return_value="/usr/bin/pdftotext"
        ), mock.patch(
            "modbus_skills.pdf_extraction._call", side_effect=effects
        ), mock.patch(
            "modbus_skills.pdf_extraction.extract_pdf_table_evidence",
            return_value={"records": grid_rows, "quarantined_records": []},
        ):
            result = compile_user_map(compile_request, self.root / "grid-pdf-case")

        self.assertEqual("partial", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])
        user_map = json.loads(
            (self.root / "grid-pdf-case" / "output" / "user-map.json").read_text()
        )
        self.assertEqual(1, len(user_map["points"]))
        self.assertEqual("67", user_map["points"][0]["source_register"])
        self.assertEqual("int16", user_map["points"][0]["datatype"])
        self.assertEqual("uF", user_map["points"][0]["engineering_unit"])
        self.assertEqual(0.0, user_map["points"][0]["scale"])
        oem_map = json.loads(
            (self.root / "grid-pdf-case" / "artifacts" / "oem-map.json").read_text()
        )
        evidence = {
            item["field"]: item
            for item in oem_map["points"][0]["source_field_evidence"]
        }
        self.assertEqual("Type", evidence["datatype"]["raw_header"])
        self.assertEqual("int16", evidence["datatype"]["raw_value"])
        self.assertEqual("Units", evidence["engineering_unit"]["raw_header"])
        self.assertEqual("uF", evidence["engineering_unit"]["raw_value"])

    def test_symbolic_pdf_grid_compiles_display_addresses_as_a_partial_map(self) -> None:
        source = self.root / "symbolic-grid.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic rights-safe fixture\n")
        table = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "pdf-extraction"
                / "symbolic-register-table.json"
            ).read_text(encoding="utf-8")
        )
        grid_evidence = parse_pdf_table_evidence(
            table, page_number=4, table_index=0
        )
        effects = [
            subprocess.CompletedProcess([], 0, b"", b"pdftotext version 25.06.0\n"),
            subprocess.CompletedProcess([], 0, b"", b"-f -l -layout -bbox-layout -enc\n"),
            subprocess.CompletedProcess(
                [],
                0,
                b"Symbolic register name Register number Modbus Register type Description\n",
                b"",
            ),
            subprocess.CompletedProcess([], 0, b"<doc><page/></doc>", b""),
        ]
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "pdf"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["all documented Modbus read points"],
                "mode": "all-readable",
            },
            "targets": [],
            "target_options": {},
        }

        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which",
            return_value="/usr/bin/pdftotext",
        ), mock.patch(
            "modbus_skills.pdf_extraction._call", side_effect=effects
        ), mock.patch(
            "modbus_skills.pdf_extraction.extract_pdf_table_evidence",
            return_value=grid_evidence,
        ):
            result = compile_user_map(
                compile_request, self.root / "symbolic-grid-case"
            )

        self.assertEqual("partial", result["state"])
        oem_map = json.loads(
            (
                self.root
                / "symbolic-grid-case"
                / "artifacts"
                / "oem-map.json"
            ).read_text()
        )
        self.assertEqual(
            {"3x1000", "4x1161"},
            {point["source_register"] for point in oem_map["points"]},
        )
        self.assertEqual(
            {999, 1160}, {point["protocol_offset"] for point in oem_map["points"]}
        )
        hold_codes = {hold["code"] for hold in oem_map["holds"]}
        self.assertIn("pdf-grid-address-ambiguous", hold_codes)
        self.assertIn("point.datatype-unresolved", hold_codes)
        self.assertNotIn("pdf-structured-rows-unavailable", hold_codes)

    def test_same_pdf_address_in_two_tables_becomes_two_source_rows(self) -> None:
        source = self.root / "duplicate-address.pdf"
        source.write_bytes(b"%PDF synthetic")
        effects = [
            subprocess.CompletedProcess([], 0, b"", b"pdftotext version 25.06.0\n"),
            subprocess.CompletedProcess([], 0, b"", b"-f -l -layout -bbox-layout -enc\n"),
            subprocess.CompletedProcess([], 0, b"Modbus register map 001\n", b""),
            subprocess.CompletedProcess([], 0, b"<doc><page/></doc>", b""),
        ]
        grid_rows = [
            {
                "source_register": "001",
                "address": 1,
                "word_count": 1,
                "access": "R",
                "format": "UInt",
                "name": name,
                "description": name,
                "_source": {
                    "format": "pdf",
                    "page": 1,
                    "row": 2,
                    "region": region,
                    "parser_id": "pdfplumber-table/v1",
                    "method": "coordinate-derived",
                    "excerpt": f"001 | R | {name}",
                },
            }
            for name, region in (("Status A", "p1:t0:r2"), ("Status B", "p1:t1:r2"))
        ]
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "pdf"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["all documented Modbus read points"],
                "mode": "all-readable",
            },
            "targets": [],
            "target_options": {},
        }
        with mock.patch(
            "modbus_skills.pdf_extraction.shutil.which", return_value="/usr/bin/pdftotext"
        ), mock.patch(
            "modbus_skills.pdf_extraction._call", side_effect=effects
        ), mock.patch(
            "modbus_skills.pdf_extraction.extract_pdf_table_evidence",
            return_value={"records": grid_rows, "quarantined_records": []},
        ):
            result = compile_user_map(compile_request, self.root / "duplicate-case")

        self.assertEqual("partial", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])
        self.assertTrue(
            (self.root / "duplicate-case" / "output" / "user-map.json").is_file()
        )
        oem_artifact = json.loads(
            (self.root / "duplicate-case" / "artifacts" / "oem-map.json").read_text()
        )
        self.assertEqual(2, len(oem_artifact["points"]))
        self.assertEqual(
            2, len({point["oem_point_id"] for point in oem_artifact["points"]})
        )

    def test_source_normalization_exceptions_form_one_grouped_packet(self) -> None:
        source = self.root / "missing-datatype.csv"
        source.write_text(
            "logical_point_id,name,protocol_offset,area,access\n"
            "temperature,Temperature,10,holding-register,read-only\n",
            encoding="utf-8",
        )
        compile_request = {
            "schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "csv"},
            "selection_template": {
                "schema_version": "modbus-user-selection-template/v1",
                "requested_measurements": ["temperature"],
                "included": [{
                    "oem_point_id": "temperature",
                    "matched_intent": "temperature",
                    "match_quality": "exact",
                    "reason": "Typed exact selection",
                    "evidence_refs": ["csv:row:2"],
                }],
                "suggested": [],
                "excluded": [],
            },
            "targets": [],
            "target_options": {},
        }

        result = compile_user_map(compile_request, self.root / "held-source-case")

        self.assertEqual("awaiting-source-decision", result["state"])
        self.assertEqual("provide-corrected-source", result["next_action"]["kind"])
        packet = json.loads(
            (self.root / "held-source-case" / "control" / "source-packet.json").read_text()
        )
        self.assertEqual("source", packet["phase"])
        self.assertEqual(1, len(packet["decisions"]))
        self.assertFalse((self.root / "held-source-case" / "output" / "user-map.json").exists())

    def test_target_waits_for_one_binding_resume_without_losing_offline_map(self) -> None:
        case_root = self.root / "case"
        initial = compile_user_map(request(targets=["node-red"]), case_root)
        self.assertEqual(initial["state"], "awaiting-binding")
        self.assertEqual(initial["next_action"]["kind"], "provide-binding")
        self.assertEqual(initial["target_statuses"][0]["status"], "held")
        self.assertTrue((case_root / "output" / "user-map.json").is_file())

        case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
        source = request(targets=["node-red"])["oem_map"]
        binding = build_device_binding(source, route_id="plant-a", unit_id=7)
        resume = {
            "schema_version": "modbus-compile-resume/v1",
            "case_id": case["case_id"],
            "case_hash": stable_input_hash(case),
            "action": "provide-binding",
            "binding": binding,
        }
        completed = compile_user_map(None, case_root, resume=resume)
        self.assertEqual(completed["state"], "complete")
        self.assertEqual(completed["target_statuses"], [{"target": "node-red", "status": "generated"}])
        self.assertTrue((case_root / "targets" / "manifest.json").is_file())
        self.assertEqual(completed, compile_user_map(None, case_root, resume=resume))

    def test_typed_selection_decision_resumes_and_exact_replay_is_idempotent(self) -> None:
        case_root = self.root / "selection-case"
        initial = compile_user_map(selection_pause_request(), case_root)
        self.assertEqual(initial["state"], "awaiting-selection-decision")
        self.assertEqual(
            initial["next_action"]["accepted_schema"],
            "modbus-compiler-decision-candidate/v1",
        )
        case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
        packet = json.loads(
            (case_root / "control" / "selection-packet.json").read_text(
                encoding="utf-8"
            )
        )
        reply = selection_reply(case, packet)

        completed = compile_user_map(None, case_root, resume=reply)

        self.assertEqual(completed["case_id"], initial["case_id"])
        self.assertEqual(completed["state"], "offline-complete")
        self.assertEqual(completed["next_action"]["kind"], "none")
        self.assertEqual(completed, compile_user_map(None, case_root, resume=reply))

    def test_invalid_selection_decisions_do_not_mutate_case(self) -> None:
        case_root = self.root / "selection-case"
        compile_user_map(selection_pause_request(), case_root)
        case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
        packet = json.loads(
            (case_root / "control" / "selection-packet.json").read_text(
                encoding="utf-8"
            )
        )
        baseline = {
            path.relative_to(case_root).as_posix(): path.read_bytes()
            for path in case_root.rglob("*")
            if path.is_file()
        }
        variants = []
        unknown = selection_reply(case, packet)
        unknown["decision_candidate"]["decisions"][0]["decision_id"] = "selection.unknown"
        variants.append(unknown)
        broadened = selection_reply(case, packet)
        broadened["decision_candidate"]["decisions"][0]["selected_subject_ids"].append("not-offered")
        variants.append(broadened)
        stale = selection_reply(case, packet)
        stale["decision_candidate"]["packet_id"] = "f" * 64
        variants.append(stale)

        for reply in variants:
            with self.subTest(reply=reply), self.assertRaises(CompilerError):
                compile_user_map(None, case_root, resume=reply)
            self.assertEqual(
                baseline,
                {
                    path.relative_to(case_root).as_posix(): path.read_bytes()
                    for path in case_root.rglob("*")
                    if path.is_file()
                },
            )

    def test_stale_or_modified_resume_does_not_mutate_case(self) -> None:
        case_root = self.root / "case"
        compile_user_map(request(targets=["node-red"]), case_root)
        before = (case_root / "case.json").read_bytes()
        case = json.loads(before)
        source = request(targets=["node-red"])["oem_map"]
        binding = build_device_binding(source, route_id="plant-a", unit_id=7)
        stale = {
            "schema_version": "modbus-compile-resume/v1",
            "case_id": case["case_id"],
            "case_hash": "f" * 64,
            "action": "provide-binding",
            "binding": binding,
        }
        with self.assertRaisesRegex(CompilerError, "stale case hash"):
            compile_user_map(None, case_root, resume=stale)
        self.assertEqual(before, (case_root / "case.json").read_bytes())

    def test_unsafe_request_and_case_path_are_rejected_before_writes(self) -> None:
        unsafe = request()
        unsafe["endpoint"] = "tcp://controller.example"
        with self.assertRaisesRegex(CompilerError, "unsafe field"):
            compile_user_map(unsafe, self.root / "unsafe")
        self.assertFalse((self.root / "unsafe").exists())

        target = self.root / "real"
        target.mkdir()
        symlink = self.root / "case-link"
        symlink.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(CompilerError, "symbolic link"):
            compile_user_map(request(), symlink)

    def test_changed_request_cannot_reuse_an_existing_case_root(self) -> None:
        case_root = self.root / "case"
        compile_user_map(request(), case_root)
        changed = request()
        changed["selection_candidate"]["requested_measurements"] = ["different"]
        with self.assertRaisesRegex(CompilerError, "different request"):
            compile_user_map(changed, case_root)


if __name__ == "__main__":
    unittest.main()
