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

from modbus_skills.modpoll import (  # noqa: E402
    WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND,
    WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS,
    export_modpoll,
    validate_witte_v12_xml,
)
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


def inputs(*points: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    values = list(points or (point(),))
    return (
        {"schema_version": "modbus-map/v1", "points": values},
        compile_read_plan(values).to_dict(),
    )


def text(result: object, suffix: str) -> str:
    return next(
        artifact.as_text()
        for artifact in result.artifacts  # type: ignore[attr-defined]
        if artifact.path.endswith(suffix)
    )


class ModpollExporterTests(unittest.TestCase):
    def test_gavinying_documented_csv_matches_golden(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modpoll(
            canonical_map, read_plan, profile="gavinying-cli"
        )
        self.assertEqual("generated", result.status)
        expected = (FIXTURES / "gavinying-modpoll.csv").read_text(encoding="utf-8")
        config = text(result, "default.csv")
        self.assertEqual(expected, config)
        self.assertIn("poll,holding_register,40100,2,BE_BE", config)
        self.assertIn("ref,discharge_pressure,40100,float32,r,bar,0.1", config)
        self.assertNotIn(",rw,", config)
        self.assertNotIn("mqtt", text(result, "commands.txt").lower())

    def test_all_four_byte_layouts_map_to_documented_endian_labels(self) -> None:
        layouts = (("ABCD", "BE_BE"), ("BADC", "LE_BE"), ("CDAB", "BE_LE"), ("DCBA", "LE_LE"))
        points = [
            point(
                logical_point_id=f"p-{layout.lower()}",
                name=f"P {layout}",
                protocol_offset=100 + index * 10,
                byte_order=layout,
            )
            for index, (layout, _) in enumerate(layouts)
        ]
        canonical_map, read_plan = inputs(*points)
        config = text(
            export_modpoll(canonical_map, read_plan, profile="gavinying-cli"),
            "default.csv",
        )
        for _, expected in layouts:
            with self.subTest(endian=expected):
                self.assertIn(f",{expected}\n", config)

    def test_probe_csv_exposes_raw_words_without_guessing(self) -> None:
        raw = point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map, read_plan = inputs(raw)
        result = export_modpoll(
            canonical_map, read_plan, profile="gavinying-cli", mode="probe"
        )
        self.assertEqual("generated", result.status)
        config = text(result, "default.csv")
        self.assertIn("raw_read_0001_word_000", config)
        self.assertIn("raw_read_0001_word_001", config)
        self.assertNotIn("float32", config)

    def test_gavinying_holds_mixed_endian_points_in_one_block(self) -> None:
        first = point(logical_point_id="first", protocol_offset=100, byte_order="ABCD")
        second = point(logical_point_id="second", protocol_offset=102, byte_order="CDAB")
        canonical_map = {"points": [first, second]}
        read_plan = compile_read_plan([first, second], max_gap=0).to_dict()
        result = export_modpoll(canonical_map, read_plan, profile="gavinying-cli")
        self.assertEqual("held", result.status)
        self.assertIn("MODPOLL_BLOCK_ENDIAN_CONFLICT", {finding.code for finding in result.findings})

    def test_witte_desktop_uses_documented_read_automation_only(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modpoll(canonical_map, read_plan, profile="witte-desktop")
        self.assertEqual("generated", result.status)
        self.assertFalse(any(artifact.path.endswith((".mbp", ".mbw")) for artifact in result.artifacts))
        script = text(result, ".ps1")
        self.assertIn("ReadHoldingRegisters", script)
        self.assertIn(".Save($savePath)", script)
        for forbidden in ("WriteSingle", "WriteMultiple", "AddressScan", "SlaveScan"):
            self.assertNotIn(forbidden, script)
        self.assertIn("Type READ to start a bounded live read", script)
        self.assertIn("$maximumLiveReadSeconds = 10", script)
        self.assertIn("$minimumScanIntervalMilliseconds = 1000", script)
        self.assertIn("$maximumRouteReadsPerSecond = 5", script)
        self.assertIn("$configuredReadsPerSecond", script)
        self.assertIn("try {", script)
        self.assertIn("finally {", script)
        self.assertIn("CloseConnection()", script)
        self.assertIn("FinalReleaseComObject($document)", script)
        self.assertNotIn("Press Enter to close", script)
        self.assertLess(script.index("$configuredReadsPerSecond"), script.index("Read-Host"))
        self.assertLess(script.index("Read-Host"), script.index("OpenConnection()"))
        expected = (FIXTURES / "witte-read-plan.csv").read_text(encoding="utf-8")
        self.assertEqual(expected, text(result, "read-plan.csv"))
        setup = json.loads(text(result, "setup-manifest.json"))
        self.assertEqual(
            WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS,
            setup["polling_safety"]["minimum_scan_interval_ms"],
        )
        self.assertEqual(
            WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND,
            setup["polling_safety"]["maximum_route_reads_per_second"],
        )

    def test_witte_desktop_holds_short_or_invalid_scan_intervals(self) -> None:
        canonical_map, base_plan = inputs()
        cases = (
            (WITTE_DESKTOP_MIN_SCAN_INTERVAL_MS - 1, "WITTE_SCAN_INTERVAL_TOO_SHORT"),
            ("fast", "WITTE_SCAN_INTERVAL_INVALID"),
            (1000.5, "WITTE_SCAN_INTERVAL_INVALID"),
        )
        for interval, expected_code in cases:
            with self.subTest(interval=interval):
                read_plan = json.loads(json.dumps(base_plan))
                read_plan["requests"][0]["poll_interval_ms"] = interval
                result = export_modpoll(
                    canonical_map,
                    read_plan,
                    profile="witte-desktop",
                )
                self.assertEqual("held", result.status)
                self.assertFalse(result.artifacts)
                self.assertIn(
                    expected_code,
                    {finding.code for finding in result.findings},
                )

    def test_witte_desktop_holds_excess_aggregate_route_read_rate(self) -> None:
        points = [
            point(
                logical_point_id=f"point-{index}",
                name=f"Point {index}",
                protocol_offset=100 + index * 10,
            )
            for index in range(WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND + 1)
        ]
        canonical_map, read_plan = inputs(*points)
        self.assertEqual(
            WITTE_DESKTOP_MAX_ROUTE_READS_PER_SECOND + 1,
            len(read_plan["requests"]),
        )
        result = export_modpoll(
            canonical_map,
            read_plan,
            profile="witte-desktop",
        )
        self.assertEqual("held", result.status)
        self.assertFalse(result.artifacts)
        self.assertIn(
            "WITTE_ROUTE_READ_RATE_EXCEEDED",
            {finding.code for finding in result.findings},
        )

    def test_witte_desktop_live_read_duration_is_short_and_validated(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modpoll(
            canonical_map,
            read_plan,
            profile="witte-desktop",
            options={"live_read_seconds": 5},
        )
        self.assertIn("$maximumLiveReadSeconds = 5", text(result, ".ps1"))
        for invalid in (0, 31, True, 1.5):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                export_modpoll(
                    canonical_map,
                    read_plan,
                    profile="witte-desktop",
                    options={"live_read_seconds": invalid},
                )

    def test_witte_v12_profile_generates_valid_disabled_xml(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_modpoll(canonical_map, read_plan, profile="witte-v12-xml")
        self.assertEqual("generated", result.status)
        xml_artifacts = [artifact for artifact in result.artifacts if artifact.path.endswith(".xml")]
        self.assertEqual(1, len(xml_artifacts))
        xml_text = xml_artifacts[0].as_text()
        self.assertEqual((), validate_witte_v12_xml(xml_text))
        self.assertIn("<Enable>0</Enable>", xml_text)
        self.assertIn("<Function>3</Function>", xml_text)
        self.assertIn("<Address>100</Address>", xml_text)

    def test_unit_zero_is_held_for_every_modpoll_profile(self) -> None:
        invalid = point(unit_id=0)
        canonical_map = {"points": [invalid]}
        read_plan = {
            "requests": [{"request_id": "r", "route_id": "default", "unit_id": 0, "area": "holding-register", "function_code": 3, "start_offset": 100, "quantity": 2, "points": [{"logical_point_id": "pressure"}]}]
        }
        for profile in ("gavinying-cli", "witte-desktop", "witte-v12-xml"):
            with self.subTest(profile=profile):
                self.assertEqual("held", export_modpoll(canonical_map, read_plan, profile=profile).status)

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
                result = export_modpoll(
                    canonical_map, read_plan, profile="gavinying-cli"
                )
                self.assertEqual("held", result.status)
                self.assertIn(expected_code, {finding.code for finding in result.findings})

    def test_all_modpoll_csv_cells_neutralize_formulas(self) -> None:
        malicious = point(
            logical_point_id="+POINT",
            name="@SUM(1,1)",
            route_id="=ROUTE",
            engineering_unit="=1+1",
        )
        canonical_map, read_plan = inputs(malicious)
        for profile in ("gavinying-cli", "witte-desktop"):
            with self.subTest(profile=profile):
                result = export_modpoll(canonical_map, read_plan, profile=profile)
                self.assertEqual("generated", result.status)
                for artifact in result.artifacts:
                    if artifact.path.endswith(".csv"):
                        self._assert_formula_safe_csv(artifact.as_text())

    def _assert_formula_safe_csv(self, value: str) -> None:
        for row in csv.reader(StringIO(value)):
            for cell in row:
                stripped = cell.lstrip(" \t\r\n\ufeff")
                self.assertFalse(
                    stripped.startswith(("=", "+", "-", "@", "\t", "\r")),
                    cell,
                )


if __name__ == "__main__":
    unittest.main()
