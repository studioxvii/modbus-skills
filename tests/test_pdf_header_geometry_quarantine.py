from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import _reconcile, extract_pdf  # noqa: E402
from modbus_skills.pdf_table_extraction import (  # noqa: E402
    _extract_pdf_table_rows_in_process, _recover_offset_header, parse_pdf_table_evidence,
)


def geometry(label="Offset"):
    cells = [["Name", "Access", None], ["Alpha", "R", "17"], ["Beta", "R", "18"]]
    boxes = [
        [(10, 10, 60, 30), (60, 10, 110, 30), None],
        [(10, 30, 60, 50), (60, 30, 110, 50), (110, 30, 160, 50)],
        [(10, 50, 60, 70), (60, 50, 110, 70), (110, 50, 160, 70)],
    ]
    table = SimpleNamespace(extract=lambda: deepcopy(cells), rows=[SimpleNamespace(cells=row, bbox=(10, 10, 110, 30)) for row in boxes])
    page = SimpleNamespace(chars=[{"text": char, "x0": 112+i*5, "x1": 117+i*5, "top": 15, "bottom": 23} for i, char in enumerate(label)])
    return page, table, cells


def point(address, name, region, **fields):
    return {"source_address": {"raw": address, "convention": "unknown"}, "name": name,
            "_source": {"page": 1, "region": region},
            "_claims": [{"field": "name", "value": name, "source_locator": {"page": 1, "region": region}}], **fields}


def synthetic_pdf(path, *, broken=True, label="Offset", header=True):
    name = "sample.long.channel.delay"
    x = [20, 22 + len(name)*4.8 + 2.8, 300, 350, 390]
    rows = [["Name", "Description", "Access", label] if header else ["", "", "", label],
            [name, "Example delay", "RW", "17"], ["sample.beta", "Example state", "R", "18"]]
    commands = ["0.4 w"]
    for pos in x:
        top = 60 if broken and pos == x[-1] else 40
        commands.append(f"{pos} {200-top} m {pos} 100 l S")
    for top in (40, 60, 80, 100):
        commands.append(f"20 {200-top} m 390 {200-top} l S")
    for ri, row in enumerate(rows):
        for ci, value in enumerate(row):
            commands.append(f"BT /F1 8 Tf 1 0 0 1 {x[ci]+2} {146-ri*20} Tm ({value}) Tj ET")
    data = ("\n".join(commands) + "\n").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 430 200] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
               b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"endstream"]
    result = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(result)


class HeaderGeometryTests(unittest.TestCase):
    def test_literal_aligned_offset_recovers_only_missing_cell(self):
        page, table, original = geometry()
        recovered, evidence = _recover_offset_header(page, table)
        self.assertIsNone(original[0][-1])
        self.assertEqual("Offset", recovered[0][-1])
        self.assertIsNone(evidence["original_cell"])
        self.assertIsNone(evidence["original_cell_bbox"])
        self.assertEqual("coordinate-derived", evidence["method"])
        parsed = parse_pdf_table_evidence(recovered, page_number=1, table_index=0)
        self.assertEqual(["17", "18"], [r["source_offset"] for r in parsed["records"]])
        self.assertTrue(all(r["address_convention"] == "unknown" and "protocol_offset" not in r for r in parsed["records"]))

    def test_missing_or_ambiguous_glyphs_are_not_repaired(self):
        for label in ("", "Offset?", "OffsetAddress", "17", "O f f s e t"):
            with self.subTest(label=label):
                page, table, cells = geometry(label)
                self.assertEqual((cells, None), _recover_offset_header(page, table))

    def test_headerless_table_cannot_borrow_numeric_column_meaning(self):
        page, table, cells = geometry()
        cells[0][:2] = ["Alpha", "R"]
        self.assertEqual((cells, None), _recover_offset_header(page, table))

    def test_multiple_missing_cells_are_not_repaired(self):
        page, table, cells = geometry()
        table.rows[0].cells[0] = None
        cells[0][0] = None
        self.assertEqual((cells, None), _recover_offset_header(page, table))

    def test_unstable_body_column_bounds_are_not_repaired(self):
        page, table, cells = geometry()
        table.rows[2].cells[-1] = (120, 50, 160, 70)
        self.assertEqual((cells, None), _recover_offset_header(page, table))

    def test_glyphs_in_another_band_or_two_lines_are_not_repaired(self):
        for change in ("outside", "two-lines"):
            with self.subTest(change=change):
                page, table, cells = geometry()
                if change == "outside":
                    for char in page.chars:
                        char["top"] += 30
                        char["bottom"] += 30
                else:
                    page.chars[-1]["top"] += 4
                self.assertEqual((cells, None), _recover_offset_header(page, table))

    def test_closed_header_cell_is_unchanged(self):
        page, table, cells = geometry()
        cells[0][-1] = "Offset"
        table.rows[0].cells[-1] = (110, 10, 160, 30)
        self.assertEqual((cells, None), _recover_offset_header(page, table))


class QuarantinePersistenceTests(unittest.TestCase):
    def test_third_parser_agreement_does_not_release_existing_conflict(self):
        left = point("17", "Alpha combined description", "p1:l3")
        middle = point("17", "Alpha", "p1:y60")
        accepted, held, first_conflicts = _reconcile([left], [middle])
        final, held, _conflicts = _reconcile(accepted, [point("17", "Alpha", "p1:t0:r1")], quarantined_records=held)
        self.assertEqual([], final)
        self.assertEqual(1, len(held))
        self.assertEqual(3, len(held[0]["_claims"]))
        self.assertEqual("name", first_conflicts[0]["fields"][0]["field"])

    def test_agreeing_later_claim_still_keeps_the_hold_and_code(self):
        held = point("17", "Alpha", "p1:t0:r1", code="pdf-grid-type-unresolved")
        accepted, quarantined, conflicts = _reconcile([], [point("17", "Alpha", "p1:t0:r1")], quarantined_records=[held])
        self.assertEqual([], accepted)
        self.assertEqual("pdf-grid-type-unresolved", quarantined[0]["code"])
        self.assertEqual(2, len(quarantined[0]["_claims"]))
        self.assertEqual([], conflicts)

    def test_same_physical_row_stays_held_when_all_labels_and_scope_disagree(self):
        held = point("17", "Alpha", "p1:t0:r1", area="coil", unit_id=1, route_id="route-a")
        later = point("18", "Beta", "p1:t0:r1", area="holding-register", unit_id=2, route_id="route-b")
        accepted, quarantined, conflicts = _reconcile([], [later], quarantined_records=[held])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual({"name", "address", "area", "unit_id", "route_id"}, {f["field"] for f in conflicts[0]["fields"]})

    def test_distinct_source_rows_tables_and_device_scopes_remain_separate(self):
        for region, fields in (("p1:t0:r2", {}), ("p1:t1:r1", {}), ("p1:l3", {"unit_id": 2}), ("p1:l3", {"area": "coil"})):
            with self.subTest(region=region, fields=fields):
                held = point("17", "Alpha", "p1:t0:r1", unit_id=1, area="holding-register")
                later = point("17", "Alpha", region, **fields)
                accepted, quarantined, _ = _reconcile([], [later], quarantined_records=[held])
                self.assertEqual([later], accepted)
                self.assertEqual([held], quarantined)

    def test_duplicate_physical_locator_cannot_bypass_prior_hold(self):
        held = point("17", "Alpha", "p1:t0:r1")
        duplicate = point("18", "Beta", "p1:t0:r1")
        accepted, quarantined, _ = _reconcile([duplicate], [duplicate], quarantined_records=[held])
        self.assertEqual([], accepted)
        self.assertEqual(2, len(quarantined))
        self.assertEqual("pdf-prior-source-quarantine", quarantined[1]["code"])
        self.assertEqual([{"page": 1, "region": "p1:t0:r1"}], quarantined[1]["_quarantine_source_locators"])

    def test_exact_locator_in_prior_claims_stays_held_without_semantic_match(self):
        held = point("17", "Alpha", "p1:l3")
        held["_claims"].append({"field": "name", "value": "Alpha", "source_locator": {"page": 1, "region": "p1:t0:r1"}})
        later = point("18", "Beta", "p1:t0:r1")
        accepted, quarantined, _ = _reconcile([], [later], quarantined_records=[held])
        self.assertEqual([], accepted)
        self.assertEqual(2, len(quarantined))
        self.assertEqual(held, quarantined[0])
        self.assertEqual(later["_claims"], quarantined[1]["_claims"])

    def test_unknown_locator_and_nonmatching_labels_do_not_invent_association(self):
        held = point("17", "Alpha", "")
        later = point("18", "Beta", "")
        accepted, quarantined, _ = _reconcile([], [later], quarantined_records=[held])
        self.assertEqual([later], accepted)
        self.assertEqual([held], quarantined)

    def test_shared_coordinate_band_does_not_override_distinct_table_rows(self):
        held = point("17", "Alpha", "p1:t0:r1")
        later = point("17", "Alpha", "p1:t1:r1")
        for row in (held, later):
            row["_claims"].append({"field": "name", "value": "Alpha", "source_locator": {"page": 1, "region": "p1:y30.0"}})
        accepted, quarantined, _ = _reconcile([], [later], quarantined_records=[held])
        self.assertEqual([later], accepted)
        self.assertEqual([held], quarantined)


@unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "pdfplumber is unavailable")
class NativePdfGeometryTests(unittest.TestCase):
    def test_generated_broken_border_recovers_rows_and_preserves_header_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pdf-header-regression-") as directory:
            path = Path(directory) / "source.pdf"
            synthetic_pdf(path)
            value = _extract_pdf_table_rows_in_process(path)
        self.assertEqual(2, len(value["records"]))
        for row in value["records"]:
            evidence = row["_source"]["header_recovery"]
            self.assertIsNone(evidence["original_cell"])
            self.assertEqual("Offset", evidence["recovered_text"])
            self.assertEqual("p1:t0:r0", evidence["source_locator"]["region"])
            self.assertTrue(any(c.get("header_evidence") == evidence for c in row["_claims"]))
            self.assertNotIn("protocol_offset", row)

    def test_generated_negative_headers_cannot_be_repaired(self):
        for label, header in (("", True), ("Offset?", True), ("Offset", False)):
            with self.subTest(label=label, header=header), tempfile.TemporaryDirectory(prefix="pdf-header-negative-") as directory:
                path = Path(directory) / "source.pdf"
                synthetic_pdf(path, label=label, header=header)
                value = _extract_pdf_table_rows_in_process(path)
                self.assertEqual({"records": [], "quarantined_records": []}, value)

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext is unavailable")
    def test_full_pdf_third_parser_never_reaccepts_held_source_row(self):
        for broken in (False, True):
            with self.subTest(broken=broken), tempfile.TemporaryDirectory(prefix="pdf-quarantine-regression-") as directory:
                path = Path(directory) / "source.pdf"
                synthetic_pdf(path, broken=broken)
                result = extract_pdf(path, path.read_bytes())
            self.assertEqual(["18"], [row["source_register"] for row in result["records"]])
            held = result["quarantined_records"]
            self.assertEqual(["17"], [row["source_register"] for row in held])
            self.assertGreaterEqual(len(held[0]["_claims"]), 3)
            self.assertEqual("held", result["status"])
            if broken:
                self.assertTrue(any(c.get("header_evidence") for c in held[0]["_claims"]))


if __name__ == "__main__":
    unittest.main()
