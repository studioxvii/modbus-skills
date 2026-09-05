"""Literal compound headings convey area/description, not new device facts."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.compiler import compile_user_map
from modbus_skills.map_workflows import normalize_map
from modbus_skills.parsers import parse_csv, parse_xlsx
from modbus_skills import parsers
from modbus_skills.source_intake import compile_source_descriptor
from tests.test_source_workbook_fidelity import workbook


HEADER = ["Name", "Address", "Modbus Table", "Semantics/Description", "Type", "Units/Notes"]
AREAS = [("Coils", "coil", 1), ("Discrete Inputs", "discrete-input", 2),
         ("Input Registers", "input-register", 4), ("Holding Registers", "holding-register", 3)]


def encoded(kind, rows):
    if kind == "xlsx":
        return workbook([("Readings", rows)])
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode()


def parse(kind, rows):
    return (parse_xlsx if kind == "xlsx" else parse_csv)(encoded(kind, rows))


class LiteralModbusHeaderTests(unittest.TestCase):
    def test_ordinary_tables_skip_compound_row_work_and_resolve_headers_once(self):
        headers = ["Name", "Protocol Offset", "Area", "Datatype", "Description"]
        for kind in ("csv", "xlsx"):
            counts = []
            for size in (1, 40):
                with self.subTest(kind=kind, size=size), \
                     patch.object(parsers, "_compound_header_field", wraps=parsers._compound_header_field) as fields, \
                     patch.object(parsers, "_compound_header_claims", side_effect=AssertionError("ordinary row claim work")), \
                     patch.object(parsers, "_compound_area_conflicts", side_effect=AssertionError("ordinary row conflict work")):
                    parsed = parse(kind, [headers] + [[f"Point {i}", i, "Input Registers", "uint16", "ordinary"] for i in range(size)])
                    self.assertEqual(size, len(parsed["records"]))
                    self.assertNotIn("source_holds", parsed)
                    self.assertTrue(all("_claims" not in row for row in parsed["records"]))
                    counts.append(fields.call_count)
            self.assertEqual([len(headers), len(headers)], counts)

    def test_four_literal_areas_and_descriptions_csv_xlsx_keep_unknown_binding(self):
        for kind in ("csv", "xlsx"):
            for literal, area, fc in AREAS:
                with self.subTest(kind=kind, area=area):
                    parsed = parse(kind, [HEADER, ["Status", 40001, literal, "0=Idle; 1=Ready", "BR", "configured units / note"]])
                    row, = parsed["records"]
                    self.assertEqual(literal, row["area"])
                    self.assertEqual("0=Idle; 1=Ready", row["description"])
                    point, = normalize_map(parsed)["points"]
                    self.assertEqual(area, point["area"])
                    self.assertEqual(fc, point["function_code"])
                    self.assertEqual("unknown", point["source_address"]["convention"])
                    self.assertIsNone(point["protocol_offset"])
                    for field in ("unit_id", "route_id", "datatype", "word_span", "engineering_unit", "access", "byte_order"):
                        self.assertIsNone(point[field], field)
                    self.assertEqual([], point["write_function_codes"])
                    self.assertEqual("configured units / note", point["unmapped_fields"]["units_notes"])
                    self.assertEqual(row["_claims"], point["source_claims"])

    def test_raw_heading_spelling_value_and_locations_survive(self):
        headings = ["Name", "Address", "  MODBUS Table  ", "Semantics / Description"]
        for kind in ("csv", "xlsx"):
            with self.subTest(kind=kind):
                parsed = parse(kind, [headings, ["Status", 1, " Coils ", " literal state text "]])
                area, description = parsed["records"][0]["_claims"]
                self.assertEqual("  MODBUS Table  ", area["raw_header"])
                self.assertEqual(" Coils ", area["raw_value"])
                self.assertEqual("Semantics / Description", description["raw_header"])
                self.assertEqual(" literal state text ", description["raw_value"])
                self.assertEqual("literal state text", description["value"])
                self.assertEqual(2, description["source_locator"]["row"])
                self.assertEqual(1, description["header_locator"]["row"])
                self.assertEqual(4, description["source_locator"]["column"])
                self.assertEqual(kind, description["source_locator"]["format"])

    def test_unknown_or_blank_table_does_not_borrow_area_from_reference(self):
        for kind in ("csv", "xlsx"):
            for value in ("custom table", "", "Input Registers or Coils", "40001"):
                with self.subTest(kind=kind, value=value):
                    parsed = parse(kind, [HEADER, ["Reading", 40001, value, "Source text", "AR", "V or percent"]])
                    canonical = normalize_map(parsed)
                    point, = canonical["points"]
                    self.assertIsNone(point["area"])
                    self.assertIsNone(point["protocol_offset"])
                    self.assertIsNone(point["function_code"])
                    self.assertIn("point.area-unrecognized" if value else "point.area-unresolved",
                                  {h["code"] for h in canonical["holds"]})
                    self.assertEqual(value, point["source_claims"][0]["raw_value"])

    def test_explicit_write_only_or_unreadable_suppresses_derived_read_function(self):
        for kind in ("csv", "xlsx"):
            for table, _, _ in AREAS:
                for access_header, access in (("Access", "write-only"), ("Access Readable", "false")):
                    with self.subTest(kind=kind, table=table, access=access):
                        parsed = parse(kind, [[*HEADER, access_header],
                                              ["Setting", 1, table, "Do not read", "AW", "", access]])
                        point, = normalize_map(parsed)["points"]
                        self.assertIsNone(point["function_code"])
                        self.assertEqual([], point["write_function_codes"])
                        self.assertIsNone(point["datatype"])
                        self.assertIsNone(point["protocol_offset"])

    def test_type_codes_do_not_gain_width_or_readability(self):
        for kind in ("csv", "xlsx"):
            for code, table in (("BW", "Coils"), ("BR", "Discrete Inputs"), ("AR", "Input Registers"), ("AW", "Holding Registers")):
                with self.subTest(kind=kind, code=code):
                    canonical = normalize_map(parse(kind, [HEADER, ["Reading", 1, table, "Literal description", code, ""]]))
                    point, = canonical["points"]
                    self.assertIsNone(point["datatype"])
                    self.assertIsNone(point["word_span"])
                    self.assertIsNone(point["access"])
                    self.assertNotIn(True, [point.get("readable"), point.get("access_readable")])
                    self.assertIn("point.datatype-unrecognized", {h["code"] for h in canonical["holds"]})

    def test_duplicate_compound_headers_do_not_claim_or_overwrite_first_column(self):
        headings = ["Name", "Address", "Area", "Modbus Table", "Description", "Semantics/Description"]
        for kind in ("csv", "xlsx"):
            with self.subTest(kind=kind):
                parsed = parse(kind, [headings, ["Reading", 1, "Input Registers", "Holding Registers", "First text", "Second text"]])
                row, = parsed["records"]
                self.assertEqual("Input Registers", row["area"])
                self.assertEqual("First text", row["description"])
                self.assertEqual(["area_2", "description_2"], [c["field"] for c in row["_claims"]])
                point, = normalize_map(parsed)["points"]
                self.assertEqual("input-register", point["area"])
                self.assertEqual("First text", point["description"])
                self.assertEqual("Holding Registers", point["unmapped_fields"]["area_2"])
                self.assertEqual("Second text", point["unmapped_fields"]["description_2"])
                self.assertEqual("source.area-columns-conflict", parsed["source_holds"][0]["code"])

    def test_otherwise_ready_conflicting_areas_block_compile_in_both_header_orders(self):
        for kind in ("csv", "xlsx"):
            for headings in (("Area", "Modbus Table"), ("Modbus Table", "Area"),
                             ("Area", "Modbus Table (source note)"), ("Modbus Table (source note)", "Area")):
                with self.subTest(kind=kind, headings=headings), tempfile.TemporaryDirectory() as temporary:
                    rows = [["Name", "Protocol Offset", *headings, "Datatype", "Access", "Unit ID", "Route ID"],
                            ["Reading", 0, "Input Registers", "Holding Registers", "uint16", "read-only", 7, "synthetic"]]
                    parsed = parse(kind, rows)
                    self.assertEqual("Input Registers", parsed["records"][0]["area"])
                    self.assertEqual("Holding Registers", parsed["records"][0]["area_2"])
                    hold, = parsed["source_holds"]
                    self.assertTrue(hold["blocking"])
                    self.assertEqual(2, hold["source"]["row"])
                    self.assertEqual(list(headings), [c["raw_header"] for c in hold["details"]["columns"]])
                    self.assertEqual(["Input Registers", "Holding Registers"],
                                     [c["raw_value"] for c in hold["details"]["columns"]])
                    self.assertEqual([3, 4], [c["source_locator"]["column"] for c in hold["details"]["columns"]])
                    canonical = normalize_map(parsed)
                    self.assertTrue(any(h["code"] == hold["code"] for h in canonical["holds"]))
                    directory = Path(temporary)
                    source = directory / ("synthetic." + kind)
                    source.write_bytes(encoded(kind, rows))
                    request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)},
                               "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["readings"]},
                               "targets": [], "target_options": {}}
                    result = compile_user_map(request, directory / "case")
                    self.assertEqual("partial", result["state"])
                    user = json.loads((directory / "case/output/user-map.json").read_text())
                    self.assertTrue(any(h["code"] == hold["code"] for h in user["holds"]))

    def test_equivalent_area_aliases_blank_alternatives_and_description_duplicates_do_not_conflict(self):
        pairs = [(literal, area) for literal, area, _ in AREAS]
        pairs.extend([("FC04", "3x input"), ("03", "Holding Registers"),
                      ("Input Registers", ""), ("", "Holding Registers"), ("", "")])
        for kind in ("csv", "xlsx"):
            for headings in (("Area", "Modbus Table"), ("Modbus Table", "Area")):
                for first, second in pairs:
                    with self.subTest(kind=kind, headings=headings, values=(first, second)):
                        parsed = parse(kind, [["Name", "Protocol Offset", *headings, "Datatype", "Description", "Semantics/Description"],
                                              ["Reading", 0, first, second, "uint16", "First description", "Second description"]])
                        self.assertNotIn("source_holds", parsed)
                        self.assertEqual("Second description", parsed["records"][0]["description_2"])

    def test_multiple_conflicts_keep_row_evidence_and_source_hold_scope_is_conservative(self):
        for kind in ("csv", "xlsx"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                rows = [["Name", "Protocol Offset", "Area", "Modbus Table", "Datatype", "Access"],
                        ["Conflict one", 0, "Input Registers", "Holding Registers", "uint16", "read-only"],
                        ["Good", 1, "Input Registers", "input-register", "uint16", "read-only"],
                        ["Conflict two", 2, "custom first", "custom second", "uint16", "read-only"]]
                parsed = parse(kind, rows)
                self.assertEqual([2, 4], [h["source"]["row"] for h in parsed["source_holds"]])
                self.assertTrue(all("point_ids" not in h for h in parsed["source_holds"]))
                canonical = normalize_map(parsed)
                self.assertEqual([2, 4], [h["source"]["row"] for h in canonical["holds"]
                                         if h["code"] == "source.area-columns-conflict"])
                directory = Path(temporary)
                source = directory / ("synthetic." + kind)
                source.write_bytes(encoded(kind, rows))
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)},
                           "selection_template": {"schema_version": "modbus-user-selection-template/v1", "requested_measurements": ["Good only"],
                                                  "included": [{"exact_name": "Good", "matched_intent": "Good only", "match_quality": "exact",
                                                                "reason": "Only the unaffected reading is requested", "evidence_refs": ["synthetic-row:3"]}],
                                                  "suggested": [], "excluded": []},
                           "targets": [], "target_options": {}}
                result = compile_user_map(request, directory / "case")
                # The existing source-hold interface is source-level, not point-
                # scoped. Do not invent IDs or silently clear unselected conflicts.
                self.assertEqual("partial", result["state"])
                user = json.loads((directory / "case/output/user-map.json").read_text())
                self.assertEqual(["Good"], [p["name"] for p in user["points"]])
                self.assertTrue(any(h["code"] == "source.area-columns-conflict" for h in user["holds"]))

    def test_downstream_offline_outputs_preserve_description_and_source_identity(self):
        for kind in ("csv", "xlsx"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source = directory / ("synthetic." + kind)
                description = "0=Idle; 1=Ready"
                source.write_bytes(encoded(kind, [HEADER, ["Ready", 10001, "Discrete Inputs", description, "BR", "state / context"]]))
                oem, _ = compile_source_descriptor({"path": str(source)})
                evidence = next(e for e in oem["points"][0]["source_field_evidence"] if e["field"] == "description")
                self.assertEqual("Semantics/Description", evidence["raw_header"])
                self.assertEqual(description, evidence["raw_value"])
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)},
                           "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["all readings"]},
                           "targets": [], "target_options": {}}
                result = compile_user_map(request, directory / "case")
                self.assertEqual("partial", result["state"])
                user = json.loads((directory / "case/output/user-map.json").read_text())
                point, = user["points"]
                self.assertEqual(description, point["description"])
                self.assertEqual("discrete-input", point["area"])
                self.assertIsNone(point["protocol_offset"])
                rows = list(csv.DictReader(io.StringIO((directory / "case/output/user-map.csv").read_text())))
                self.assertEqual(description, rows[0]["description"])
                # The named human entry stays compact; description is present in
                # JSON/CSV, and a description-only point is tested separately below.
                self.assertIn("Ready", (directory / "case/output/user-map.md").read_text())
                self.assertEqual(1, len(point["source_refs"]))

    def test_description_only_point_uses_literal_text_in_markdown_without_unused_filter(self):
        for kind in ("csv", "xlsx"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source = directory / ("synthetic." + kind)
                source.write_bytes(encoded(kind, [HEADER, ["", 1, "Coils", "Not Used", "BW", ""]]))
                request = {"schema_version": "modbus-compile-request/v1", "source": {"path": str(source)},
                           "selection_template": {"schema_version": "modbus-user-selection-template/v1", "mode": "all-readable", "requested_measurements": ["source catalog"]},
                           "targets": [], "target_options": {}}
                compile_user_map(request, directory / "case")
                user = json.loads((directory / "case/output/user-map.json").read_text())
                self.assertEqual(1, len(user["points"]))
                self.assertEqual("Not Used", user["points"][0]["description"])
                self.assertIn("Not Used", (directory / "case/output/user-map.md").read_text())


if __name__ == "__main__":
    unittest.main()
