"""Public source-evidence controls: no inferred reciprocal or workbook aliases."""
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
from modbus_skills.compiler import compile_user_map
from modbus_skills.map_workflows import normalize_map
from modbus_skills.parsers import ParseError, parse_xlsx

HEADERS = ["Name", "Protocol Offset", "Area", "Datatype", "Access", "Scale"]


def workbook(sheets, hidden=()):
    """Small stdlib XLSX with literal cells; caller owns every source fact."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    book = ET.Element("workbook", xmlns=ns)
    sheet_list = ET.SubElement(book, "sheets")
    rels = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for index, (name, values) in enumerate(sheets, 1):
            ET.SubElement(sheet_list, "sheet", {"name": name, "sheetId": str(index), "{" + relns + "}id": f"rId{index}",
                **({"state": "hidden"} if name in hidden else {})})
            ET.SubElement(rels, "Relationship", {"Id": f"rId{index}", "Type": relns + "/worksheet", "Target": f"worksheets/sheet{index}.xml"})
            sheet = ET.Element("worksheet", xmlns=ns)
            data = ET.SubElement(sheet, "sheetData")
            for row_number, row_values in enumerate(values, 1):
                row = ET.SubElement(data, "row", r=str(row_number))
                for column, value in enumerate(row_values):
                    cell = ET.SubElement(row, "c", r=f"{chr(65 + column)}{row_number}")
                    if isinstance(value, dict):
                        cell.set("t", "str")
                        ET.SubElement(cell, "f").text = value["formula"]
                        ET.SubElement(cell, "v").text = value["cached"]
                    elif isinstance(value, (int, float)):
                        ET.SubElement(cell, "v").text = str(value)
                    else:
                        cell.set("t", "inlineStr")
                        ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(value)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(book))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
    return output.getvalue()


def row(name="Frequency", scale=10, offset=0):
    return [name, offset, "Input Registers", "uint16", "read-only", scale]


class XlsxScaleDirectionTests(unittest.TestCase):
    def parse(self, notes=(), rows=None, *, first=False, header=None):
        sheets = [("Registers", [header or HEADERS, *(rows or [row()])])]
        if notes:
            guidance = ("Guidance", [["Topic", "Details"], *[["Conversion", note] for note in notes]])
            sheets.insert(0, guidance) if first else sheets.append(guidance)
        data = workbook(sheets)
        return data, parse_xlsx(data)

    def scales(self, candidate, **kwargs):
        return [p["scale"] for p in normalize_map(candidate, **kwargs)["points"]]

    def compile(self, data, selection=None, targets=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "synthetic.xlsx"
        source.write_bytes(data)
        result = compile_user_map({"schema_version": "modbus-compile-request/v1",
            "source": {"path": str(source), "format": "xlsx"},
            "selection_template": selection or {"schema_version": "modbus-user-selection-template/v1",
                "mode": "all-readable", "requested_measurements": ["source readings"]},
            "targets": targets or [], "target_options": {}}, root / "case")
        user = json.loads((root / "case/output/user-map.json").read_text())
        return result, user, root / "case"

    def test_division_conflict_preserves_literal_and_both_locators(self):
        for note in ("Frequency engineering value = raw / 10.", "Integer scaling for Frequency (÷10)."):
            with self.subTest(note=note):
                data, candidate = self.parse([note])
                self.assertEqual(10, candidate["records"][0]["scale"])
                canonical = normalize_map(candidate)
                self.assertIsNone(canonical["points"][0]["scale"])
                hold = next(h for h in canonical["holds"] if h["field"] == "scale")
                self.assertTrue(hold["blocking"])
                claim = hold["details"]["scale_evidence"][0]
                self.assertEqual(("Scale", 10), (claim["raw_header"], claim["raw_value"]))
                self.assertEqual({"format": "xlsx", "sheet": "Registers", "row": 2, "column": 6}, claim["source_locator"])
                self.assertEqual(1, claim["header_locator"]["row"])
                self.assertEqual(note, claim["conversion_notes"][0]["literal"])
                self.assertEqual({"format": "xlsx", "sheet": "Guidance", "row": 2, "column": 2}, claim["conversion_notes"][0]["source_locator"])
                result, user, _ = self.compile(data)
                self.assertEqual("partial", result["state"])
                self.assertIsNone(user["points"][0]["scale"])
                self.assertEqual("provide-corrected-source", result["next_action"]["kind"])

    def test_consistent_existing_factors_are_not_reciprocated(self):
        for factor, operator, operand in ((0.1, "/", 10), (100000, "*", 100000), (0, "×", 0), (1, "÷", 1), (-2, "*", -2), (-0.5, "/", -2)):
            with self.subTest(factor=factor):
                data, candidate = self.parse([f"Frequency engineering value = raw {operator} {operand}."], [row(scale=factor)])
                self.assertEqual([factor], self.scales(candidate))
                self.assertFalse(any(h["field"] == "scale" for h in normalize_map(candidate)["holds"]))
                self.assertEqual("offline-complete", self.compile(data)[0]["state"])

    def test_sheet_order_and_all_same_point_claims(self):
        notes = ["Frequency engineering value = raw * 10.", "Frequency engineering value = raw / 10."]
        for first in (False, True):
            for ordered in (notes, list(reversed(notes))):
                with self.subTest(first=first, notes=ordered):
                    _, candidate = self.parse(ordered, first=first)
                    self.assertEqual([None], self.scales(candidate))
                    claims = candidate["records"][0]["_claims"][-1]["conversion_notes"]
                    self.assertEqual(set(notes), {c["literal"] for c in claims})

    def test_explicit_unknown_direction_does_not_lose_to_matching_note(self):
        _, candidate = self.parse(["Frequency engineering value = raw * 10.",
            "Frequency conversion direction is unspecified: factor 10 may be a multiplier or divisor."])
        self.assertEqual([None], self.scales(candidate))

    def test_exact_name_scopes_to_one_row_not_substring(self):
        _, candidate = self.parse(["Frequency engineering value = raw / 10."], [row(), row("Other", 100000, 1)])
        self.assertEqual([None, 100000], self.scales(candidate))
        _, ambiguous = self.parse(["Frequency engineering value = raw / 10."], [row("Bus Frequency"), row("Other", 100000, 1)])
        self.assertEqual([None, None], self.scales(ambiguous))
        self.assertTrue(all(h["details"]["possible_scope"] for h in normalize_map(ambiguous)["holds"] if h["field"] == "scale"))

    def test_duplicate_names_and_mixed_configured_scope_are_not_guessed(self):
        for note in ("Frequency engineering value = raw / 10.", "Integer scaling for Frequency (÷10), rates (×100 as configured)."):
            data = workbook([("A", [HEADERS, row()]), ("B", [HEADERS, row()]),
                ("Guidance", [["Conversion", note]])])
            self.assertEqual([None, None], self.scales(parse_xlsx(data)))

    def test_row_local_notes_bind_physical_row(self):
        note = "Frequency engineering value = raw / 10."
        _, candidate = self.parse(rows=[row() + [note], row("Frequency", 100000, 1) + [""]], header=HEADERS + ["Notes"])
        self.assertEqual([None, 100000], self.scales(candidate))
        self.assertEqual("physical-row", candidate["records"][0]["_claims"][-1]["conversion_notes"][0]["binding"])

    def test_large_factors_and_unrelated_display_prose(self):
        for note in (None, "Divide the display into 10 columns.", "See page 10 for the scale."):
            for heading in ("Scale", "Multiplier", "Gain", "Slope"):
                with self.subTest(note=note, heading=heading):
                    _, candidate = self.parse([note] if note else [], [row(scale=100000)], header=HEADERS[:-1] + [heading])
                    self.assertEqual([100000], self.scales(candidate))
                    self.assertNotIn("_claims", candidate["records"][0])

    def test_explicit_multiplier_does_not_override_contradictory_note(self):
        _, candidate = self.parse(["Frequency engineering value = raw / 10."], header=HEADERS[:-1] + ["Multiplier"])
        self.assertEqual([None], self.scales(candidate))

    def test_qualified_generic_scale_header_uses_normalized_source_role(self):
        note = "Integer scaling for readings (÷10 or ×100 as configured)."
        for header in ("Scale (raw factor)", " Scale ", "SCALE (factor)"):
            with self.subTest(header=header):
                _, candidate = self.parse([note], header=HEADERS[:-1] + [header])
                self.assertEqual([None], self.scales(candidate))
                self.assertEqual(header, candidate["records"][0]["_claims"][-1]["raw_header"])
        for header in ("Multiplier (engineering)", "Gain", "Slope"):
            _, candidate = self.parse([note], header=HEADERS[:-1] + [header])
            self.assertEqual([10], self.scales(candidate))

    def test_named_configured_compact_note_keeps_selector_not_operand(self):
        for suffix in ("÷10 as configured", "÷NaN", "×10 or ÷100"):
            for header in ("Multiplier", "Gain", "Slope"):
                with self.subTest(suffix=suffix, header=header):
                    _, candidate = self.parse([f"Integer scaling for Frequency ({suffix})."],
                        [row(), row("Other", 100000, 1)], header=HEADERS[:-1] + [header])
                    self.assertEqual([None, 100000], self.scales(candidate))
                    clause = candidate["records"][0]["_claims"][-1]["conversion_notes"][0]["clauses"][0]
                    self.assertEqual({"selector": "Frequency", "direction": "unknown"}, clause)

    def test_absent_scale_column_holds_bound_conversion_without_fake_cell_or_default(self):
        note = "Frequency engineering value = raw / 10."
        for local in (False, True):
            header = HEADERS[:-1] + (["Notes"] if local else [])
            data, candidate = self.parse([] if local else [note],
                rows=[row()[:-1] + ([note] if local else [])], header=header)
            for defaults in ({}, {"scale": 1}, {"scale": 0.1}):
                canonical = normalize_map(candidate, defaults=defaults)
                self.assertIsNone(canonical["points"][0]["scale"])
                scale_hold = next(h for h in canonical["holds"] if h["field"] == "scale")
                claim = scale_hold["details"]["scale_evidence"][0]
                self.assertEqual("absent-column", claim["scale_source"])
                self.assertNotIn("column", claim["source_locator"])
                for field in ("raw_header", "raw_value", "value", "header_locator"):
                    self.assertNotIn(field, claim)
                evidence = next(e for e in canonical["points"][0]["source_evidence"] if e["field"] == "scale")
                self.assertIsNone(evidence["source_field"])
                self.assertIsNone(evidence["source_value"])
            result, user, case = self.compile(data)
            self.assertEqual("partial", result["state"])
            self.assertIsNone(user["points"][0]["scale"])
            self.assertIn("absent-column", (case / "output/user-map.json").read_text())
            self.assertIn("no Scale column; no source cell is invented", (case / "output/user-map.md").read_text())
        _, candidate = self.parse(rows=[row(scale=0)])
        self.assertEqual([0], self.scales(candidate, defaults={"scale": 17}))

    def test_unsupported_named_equation_cannot_disappear_on_multiplier_header(self):
        for tail in ("/ NaN.", "/ 10 + 2.", "/ 10 as configured."):
            _, candidate = self.parse([f"Frequency engineering value = raw {tail}"], header=HEADERS[:-1] + ["Multiplier"])
            self.assertEqual([None], self.scales(candidate))
        _, candidate = self.parse(["Frequency engineering value = raw / 10. Other engineering value = raw / NaN."],
                                  [row(), row("Other", 100, 1)], header=HEADERS[:-1] + ["Multiplier"])
        self.assertEqual([None, None], self.scales(candidate))

    def test_name_matching_only_normalizes_case_and_whitespace(self):
        _, candidate = self.parse(["  FREQUENCY   engineering value = raw / 10.  "], [row(), row("Else", 2, 1)])
        self.assertEqual([None, 2], self.scales(candidate))

    def test_cached_formula_note_is_not_confirmed_and_hidden_notes_not_used(self):
        text = "Frequency engineering value = raw * 10."
        sheets = [("Registers", [HEADERS, row()]), ("Guidance", [["Conversion", {"formula": '"cached"', "cached": text}]])]
        candidate = parse_xlsx(workbook(sheets))
        self.assertEqual([None], self.scales(candidate))
        self.assertTrue(candidate["records"][0]["_claims"][-1]["conversion_notes"][0]["cached_formula_row"])
        hidden = parse_xlsx(workbook(sheets, hidden=["Guidance"]))
        self.assertEqual([10], self.scales(hidden))
        self.assertNotIn("_claims", hidden["records"][0])

    def test_malformed_and_nonfinite_source_factor_remain_invalid(self):
        for value in ("invalid", "NaN", "Infinity", "1e9999"):
            _, candidate = self.parse(["Frequency engineering value = raw * 10."], [row(scale=value)])
            canonical = normalize_map(candidate)
            self.assertIsNone(canonical["points"][0]["scale"])
            self.assertTrue(any(h["code"] == "point.scale-invalid" for h in canonical["holds"]))

    def test_matching_note_and_conflicting_duplicate_notes_both_retained(self):
        note = "Frequency engineering value = raw / 10."
        data, _ = self.parse([note, note])
        _, user, case = self.compile(data)
        hold = next(h for h in user["holds"] if h["field"] == "scale")
        notes = hold["details"]["scale_evidence"][0]["conversion_notes"]
        self.assertEqual({2, 3}, {n["source_locator"]["row"] for n in notes})
        self.assertEqual(1, (case / "output/user-map.md").read_text().count("Frequency engineering value"))

    def test_curated_unaffected_selection_does_not_inherit_exact_row_hold(self):
        data, _ = self.parse(["Frequency engineering value = raw / 10."], [row(), row("Other", 100000, 1)])
        selection = {"schema_version": "modbus-user-selection-template/v1", "requested_measurements": ["Other"],
            "included": [{"exact_name": "Other", "reason": "Explicit synthetic request", "evidence_refs": ["xlsx:sheet:Registers:row:3"],
                "matched_intent": "Other", "match_quality": "exact"}], "suggested": [], "excluded": []}
        result, user, _ = self.compile(data, selection=selection)
        self.assertEqual("offline-complete", result["state"])
        self.assertEqual(100000, user["points"][0]["scale"])
        self.assertFalse(user["holds"])
        self.assertTrue(any(h["code"] == "source.scale-conversion-unresolved" for h in user["exception_annex"]))

    def test_unselected_scale_evidence_is_grouped_only_in_presentation(self):
        data, _ = self.parse(["Integer scaling for readings (÷10 or ×100 as configured)."],
                            [row(f"Unselected {i}", 10, i) for i in range(20)] + [row("Chosen", "", 20)])
        selection = {"schema_version": "modbus-user-selection-template/v1", "requested_measurements": ["Chosen"],
            "included": [{"exact_name": "Chosen", "reason": "Explicit synthetic request", "evidence_refs": ["xlsx:sheet:Registers:row:22"],
                "matched_intent": "Chosen", "match_quality": "exact"}], "suggested": [], "excluded": []}
        _, user, case = self.compile(data, selection=selection)
        raw = [h for h in user["exception_annex"] if h.get("code") == "source.scale-conversion-unresolved"]
        self.assertEqual(20, len(raw))
        self.assertEqual(20, len({h["details"]["scale_evidence"][0]["source_locator"]["row"] for h in raw}))
        summary = (case / "output/user-map.md").read_text()
        self.assertEqual(1, summary.count("source.scale-conversion-unresolved"))
        self.assertIn("20 unselected source records", summary)

    def test_unsupported_tail_nonfinite_zero_divisor_and_defaults_stay_held(self):
        for tail in ("/ 0.", "/ NaN.", "/ inf.", "/ 1e9999.", "/ 10 + 2.", "/ 10 as configured."):
            with self.subTest(tail=tail):
                _, candidate = self.parse([f"Frequency engineering value = raw {tail}"])
                self.assertEqual([None], self.scales(candidate, defaults={"scale": 1}))
        _, candidate = self.parse(["Frequency engineering value = raw / 10."], [row(scale="")])
        self.assertEqual([None], self.scales(candidate, defaults={"scale": 0.1}))

    def test_many_equations_in_one_cell_and_decimal_operand(self):
        _, candidate = self.parse(["Frequency engineering value = raw * 0.5. Other engineering value = raw / 2."], [row(scale=0.5), row("Other", 0.5, 1)])
        self.assertEqual([0.5, 0.5], self.scales(candidate))

    def test_bounded_note_count_and_size_fail_explicitly(self):
        with self.assertRaisesRegex(ParseError, "256"):
            self.parse(["Frequency engineering value = raw / 10."] * 257)
        with self.assertRaisesRegex(ParseError, "4096"):
            self.parse(["Frequency engineering value = raw / 10 " + "x" * 4096])
        with self.assertRaisesRegex(ParseError, "100000"):
            self.parse(["Integer scaling for readings (÷10 or ×100 as configured)."] * 200,
                       [row(f"Point {index}", 10, index) for index in range(501)])

    def test_user_output_retains_grouped_evidence_and_escapes_notes(self):
        note = "Integer scaling for readings (÷10 or ×100 as configured). <script>[click](bad)</script>"
        data, _ = self.parse([note], [row(), row("Other", 100, 1)])
        result, user, case = self.compile(data)
        self.assertEqual("partial", result["state"])
        holds = [h for h in user["holds"] if h["code"] == "source.scale-conversion-unresolved"]
        self.assertEqual(1, len(holds)); self.assertEqual(2, holds[0]["affected_count"])
        evidence = holds[0]["details"]["scale_evidence"]
        self.assertEqual({2, 3}, {e["source_locator"]["row"] for e in evidence})
        self.assertTrue(all(e["raw_header"] == "Scale" and e["source_locator"]["column"] == 6 for e in evidence))
        self.assertTrue(all(e["conversion_notes"][0]["literal"] == note for e in evidence))
        summary = (case / "output/user-map.md").read_text()
        self.assertEqual(1, summary.count("## Unresolved source scaling"))
        self.assertEqual(1, summary.count("Integer scaling for readings"))
        self.assertNotIn("<script>", summary); self.assertNotIn("[click](bad)", summary)
        self.assertIn("&lt;script&gt;", summary); self.assertIn("possible workbook scope", summary)
        self.assertTrue((case / "output/user-map.json").exists())
        self.assertFalse(any("scale" == h for h in (case / "output/user-map.csv").read_text().splitlines()[0].split(",")))

    def test_target_request_cannot_launch_with_held_scaling(self):
        data, _ = self.parse(["Frequency engineering value = raw / 10."])
        result, user, case = self.compile(data, targets=["node-red"])
        self.assertNotEqual("offline-complete", result["state"])
        self.assertIsNone(user["points"][0]["scale"])
        self.assertFalse((case / "output/node-red").exists())


if __name__ == "__main__":
    unittest.main()
