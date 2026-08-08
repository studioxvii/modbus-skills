from __future__ import annotations

import csv
from io import StringIO
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
FIXTURES = ROOT / "tests" / "fixtures" / "outputs"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import artifact_envelope  # noqa: E402
from modbus_skills.modscan import export_modscan  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402


def point(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "logical_point_id": "pressure",
        "name": "Discharge Pressure",
        "route_id": "default",
        "unit_id": 1,
        "area": "holding-register",
        "protocol_offset": 100,
        "source_address": {"raw": "40101", "convention": "modicon-reference"},
        "datatype": "float32",
        "word_span": 2,
        "byte_order": "ABCD",
        "byte_order_confirmed": True,
        "normalization_status": "confirmed",
        "scale": 0.1,
        "engineering_offset": 0.0,
        "engineering_unit": "bar",
    }
    value.update(updates)
    return value


def inputs() -> tuple[dict[str, object], dict[str, object]]:
    value = point()
    canonical_map = {"points": [value]}
    return canonical_map, artifact_envelope(
        compile_read_plan([value]).to_dict(),
        schema_version="modbus-read-plan/v1",
        inputs={"canonical_map": canonical_map},
    )


def text(result: object, suffix: str) -> str:
    return next(
        artifact.as_text()
        for artifact in result.artifacts  # type: ignore[attr-defined]
        if artifact.path.endswith(suffix)
    )


class ModscanExporterTests(unittest.TestCase):
    def test_read_plan_and_message_plan_match_golden_fixtures(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modscan(canonical_map, read_plan)
        self.assertEqual("generated", result.status)
        self.assertEqual(
            (FIXTURES / "modscan-read-plan.csv").read_text(encoding="utf-8"),
            text(result, "read-plan.csv"),
        )
        self.assertEqual(
            (FIXTURES / "modscan-message-plan.csv").read_text(encoding="utf-8"),
            text(result, "test-message-plan.csv"),
        )

    def test_message_plan_contains_only_bounded_read_pdu(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modscan(canonical_map, read_plan)
        message_plan = text(result, "test-message-plan.csv")
        self.assertIn("03 00 64 00 02", message_plan)
        self.assertIn(",03,4,03 04,", message_plan)
        for code in ("05 ", "06 ", "0F ", "10 "):
            self.assertNotIn(code, message_plan)

    def test_no_opaque_native_file_is_claimed_or_generated(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modscan(canonical_map, read_plan)
        self.assertFalse(any(artifact.path.endswith((".tst", ".cfg")) for artifact in result.artifacts))
        setup = json.loads(text(result, "setup-manifest.json"))
        self.assertFalse(setup["opaque_native_files_bundled"])
        self.assertFalse(setup["native_import_claim"])
        self.assertTrue(setup["operator_entry_required"])

    def test_output_is_deterministic(self) -> None:
        canonical_map, read_plan = inputs()
        left = export_modscan(canonical_map, read_plan)
        right = export_modscan(canonical_map, read_plan)
        self.assertEqual(
            [(artifact.path, artifact.content) for artifact in left.artifacts],
            [(artifact.path, artifact.content) for artifact in right.artifacts],
        )

    def test_probe_still_rejects_unit_zero_and_missing_route(self) -> None:
        raw = point(unit_id=0, route_id=None, datatype=None, byte_order=None)
        canonical_map = {"points": [raw]}
        read_plan = {"requests": [{"request_id": "raw", "route_id": None, "unit_id": 0, "area": "holding-register", "function_code": 3, "start_offset": 100, "quantity": 2, "points": [{"logical_point_id": "pressure"}]}]}
        result = export_modscan(canonical_map, read_plan, mode="probe")
        self.assertEqual("held", result.status)
        self.assertFalse(result.artifacts)

    def test_explicit_point_trace_must_match_block_scope(self) -> None:
        canonical_map, base_plan = inputs()
        cases = (
            ({"route_id": "other-route"}, "BLOCK_POINT_ROUTE_MISMATCH"),
            ({"unit_id": 2}, "BLOCK_POINT_UNIT_MISMATCH"),
            (
                {"area": "input-register", "function_code": 4},
                "BLOCK_POINT_AREA_MISMATCH",
            ),
        )
        for updates, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                read_plan = json.loads(json.dumps(base_plan))
                read_plan["requests"][0].update(updates)
                result = export_modscan(canonical_map, read_plan)
                self.assertEqual("held", result.status)
                self.assertIn(expected_code, {finding.code for finding in result.findings})

    def test_every_csv_cell_neutralizes_spreadsheet_formulas(self) -> None:
        malicious = point(
            logical_point_id="+POINT",
            name="@SUM(1,1)",
            route_id="=ROUTE",
            engineering_unit="=1+1",
        )
        canonical_map = {"points": [malicious]}
        read_plan = compile_read_plan([malicious]).to_dict()
        read_plan["requests"][0]["request_id"] = "-REQUEST"
        read_plan = artifact_envelope(
            read_plan,
            schema_version="modbus-read-plan/v1",
            inputs={"canonical_map": canonical_map},
        )
        result = export_modscan(canonical_map, read_plan)
        self.assertEqual("generated", result.status)
        for artifact in result.artifacts:
            if not artifact.path.endswith(".csv"):
                continue
            for row in csv.reader(StringIO(artifact.as_text())):
                for cell in row:
                    stripped = cell.lstrip(" \t\r\n\ufeff")
                    self.assertFalse(
                        stripped.startswith(("=", "+", "-", "@", "\t", "\r")),
                        cell,
                    )


if __name__ == "__main__":
    unittest.main()
