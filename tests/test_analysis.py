from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.analysis import CaptureAnalysisError, analyze_capture  # noqa: E402


class CaptureAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = {
            "capture_id": "synthetic-capture",
            "points": [
                {
                    "logical_point_id": "temperature",
                    "expected_interval_seconds": 10,
                    "stale_after_seconds": 15,
                    "minimum": 0,
                    "maximum": 100,
                    "rate_of_change_limit": 2,
                },
                {"logical_point_id": "counter", "counter": True, "counter_modulus": 100},
                {"logical_point_id": "switch", "datatype": "bool"},
                {"logical_point_id": "flat"},
                {"logical_point_id": "missing"},
            ],
            "samples": [
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:00Z", "value": 10, "response_ms": 8},
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:10Z", "value": 20, "response_ms": 10},
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:30Z", "value": 80, "response_ms": 12},
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:40Z", "value": 120, "response_ms": 9, "sample_id": "t4"},
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:40Z", "value": 120, "response_ms": 9, "sample_id": "t4"},
                {"point_id": "temperature", "timestamp": "2026-01-01T00:00:50Z", "error": "timeout", "response_ms": 100},
                {"point_id": "counter", "timestamp": "2026-01-01T00:00:00Z", "value": 95},
                {"point_id": "counter", "timestamp": "2026-01-01T00:00:10Z", "value": 3},
                {"point_id": "counter", "timestamp": "2026-01-01T00:00:20Z", "value": 1},
                {"point_id": "switch", "timestamp": "2026-01-01T00:00:00Z", "value": 0},
                {"point_id": "switch", "timestamp": "2026-01-01T00:00:10Z", "value": 1},
                {"point_id": "switch", "timestamp": "2026-01-01T00:00:20Z", "value": 1},
                {"point_id": "switch", "timestamp": "2026-01-01T00:00:30Z", "value": 0},
                {"point_id": "flat", "timestamp": "2026-01-01T00:00:00Z", "value": 5},
                {"point_id": "flat", "timestamp": "2026-01-01T00:00:10Z", "value": 5},
                {"point_id": "flat", "timestamp": "2026-01-01T00:00:20Z", "value": 5},
                {"point_id": "bad-time", "timestamp": "not-time", "value": 1},
            ],
        }

    def test_full_bounded_analysis(self) -> None:
        result = analyze_capture(self.capture, now="2026-01-01T00:01:40Z")
        summary = result["summary"]
        self.assertTrue(result["read_only"])
        self.assertEqual(1, summary["missing_points"])
        self.assertEqual(1, summary["duplicate_samples"])
        self.assertEqual(1, summary["estimated_missing_intervals"])
        self.assertEqual(1, summary["flatline_points"])
        self.assertEqual(1, summary["range_violations"])
        self.assertEqual(2, summary["rate_of_change_violations"])
        self.assertEqual(1, summary["counter_wraps"])
        self.assertEqual(1, summary["counter_resets"])
        self.assertEqual(2, result["points"]["switch"]["discrete_transitions"]["count"])
        self.assertTrue(result["points"]["temperature"]["stale"])
        self.assertEqual(1, result["communications"]["error_count"])
        self.assertEqual(1, len(result["rejected_samples"]))

    def test_analysis_is_deterministic_without_wall_clock(self) -> None:
        small = {
            "points": [{"point_id": "p", "stale_after_seconds": 1}],
            "samples": [{"point_id": "p", "timestamp": "2026-01-01T00:00:00Z", "value": 1}],
        }
        first = analyze_capture(small)
        second = analyze_capture(small)
        self.assertEqual(first, second)
        self.assertEqual("ANALYSIS_TIME_FROM_CAPTURE_END", first["assumptions"][0]["code"])
        self.assertFalse(first["points"]["p"]["stale"])

    def test_rejects_sample_count_over_bound(self) -> None:
        with self.assertRaises(CaptureAnalysisError):
            analyze_capture({"samples": [{"point_id": "p", "timestamp": 0}] * 2}, max_samples=1)

    def test_counter_specs_can_be_supplied_outside_capture(self) -> None:
        capture = {
            "samples": [
                {"point_id": "count", "timestamp": 0, "value": 250},
                {"point_id": "count", "timestamp": 1, "value": 2},
            ]
        }
        result = analyze_capture(capture, counter_specs={"count": {"modulus": 256}})
        self.assertEqual(1, result["points"]["count"]["counter"]["wraps"])

    def test_expected_intervals_can_be_supplied_outside_capture(self) -> None:
        capture = {
            "samples": [
                {"point_id": "voltage", "timestamp": 0, "value": 230},
                {"point_id": "voltage", "timestamp": 30, "value": 231},
            ]
        }

        result = analyze_capture(
            capture, expected_interval_seconds={"voltage": 10}
        )

        self.assertEqual(2, result["summary"]["estimated_missing_intervals"])

    def test_naive_timestamps_are_rejected_not_assigned_utc(self) -> None:
        result = analyze_capture(
            {
                "samples": [
                    {"point_id": "p", "timestamp": "2026-01-01T00:00:00", "value": 1}
                ]
            }
        )
        self.assertEqual("TIMESTAMP_INVALID", result["rejected_samples"][0]["code"])
        self.assertIn("timezone", result["rejected_samples"][0]["message"])
        with self.assertRaises(CaptureAnalysisError):
            analyze_capture({"samples": []}, now="2026-01-01T00:00:00")

    def test_byte_order_stability_is_evidence_only_for_every_candidate(self) -> None:
        result = analyze_capture(
            {
                "points": [
                    {
                        "point_id": "raw",
                        "datatype": "float32",
                        "minimum": 0,
                        "maximum": 10,
                    }
                ],
                "samples": [
                    {
                        "point_id": "raw",
                        "sample_id": "raw-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "raw_words": [0x3F80, 0],
                    },
                    {
                        "point_id": "raw",
                        "sample_id": "raw-2",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "raw_words": [0x4000, 0],
                    },
                ],
            }
        )
        evidence = result["points"]["raw"]["byte_order_evidence"]
        abcd = next(item for item in evidence["candidates"] if item["layout"] == "ABCD")

        self.assertFalse(evidence["automatic_selection"])
        self.assertEqual(4, evidence["candidate_count"])
        self.assertEqual(1, abcd["change_count"])
        self.assertEqual(2, abcd["plausible_range"]["in_range_count"])
        self.assertNotIn("winner", evidence)
        self.assertNotIn("selected_layout", evidence)

    def test_invalid_raw_words_hold_byte_order_evidence(self) -> None:
        result = analyze_capture(
            {
                "samples": [
                    {
                        "point_id": "raw",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "raw_words": [1, 2, 3],
                    }
                ]
            }
        )
        evidence = result["points"]["raw"]["byte_order_evidence"]
        self.assertEqual("partial", evidence["status"])
        self.assertIn(
            "BYTE_ORDER_EVIDENCE_INVALID",
            {finding["code"] for finding in result["findings"]},
        )

    def test_campaign_completeness_reports_missing_requests(self) -> None:
        result = analyze_capture(
            {
                "expected_request_ids": ["run-1:block-1", "run-1:block-2"],
                "samples": [
                    {
                        "sample_id": "run-1:block-1:p1",
                        "request_id": "run-1:block-1",
                        "block_id": "block-1",
                        "unit_id": 1,
                        "point_id": "p1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "raw_words": [1],
                    }
                ],
            }
        )
        self.assertEqual(2, result["campaign"]["expected_requests"])
        self.assertEqual(1, result["campaign"]["observed_requests"])
        self.assertEqual(1, result["campaign"]["missing_requests"])
        self.assertEqual(["run-1:block-2"], result["campaign"]["missing_request_ids"])
        self.assertIn("CAMPAIGN_REQUESTS_MISSING", {item["code"] for item in result["findings"]})

    def test_clean_capture_reports_complete_batch_and_runtime_evidence(self) -> None:
        result = analyze_capture(
            {
                "schema_version": "capture/v1",
                "expected_request_ids": ["run:block-1", "run:block-2"],
                "completed_request_ids": ["run:block-1", "run:block-2"],
                "expected_unit_ids": [1, 2],
                "runtime_metadata": {
                    "target": "node-red",
                    "terminal_state": "drained",
                    "queue_depth": 0,
                    "max_in_flight": 1,
                },
                "samples": [
                    {
                        "request_id": "run:block-1",
                        "unit_id": 1,
                        "point_id": "p1",
                        "timestamp": "2026-01-01T00:00:00Z",
                    },
                    {
                        "request_id": "run:block-2",
                        "unit_id": 2,
                        "point_id": "p2",
                        "timestamp": "2026-01-01T00:00:01Z",
                    },
                ],
            }
        )

        self.assertTrue(result["campaign"]["requests_complete"])
        self.assertTrue(result["campaign"]["batch_complete"])
        self.assertEqual([], result["campaign"]["duplicate_completed_request_ids"])
        self.assertEqual([], result["campaign"]["missing_unit_ids"])
        self.assertEqual([], result["campaign"]["duplicate_unit_ids"])
        self.assertEqual(
            {
                "available": True,
                "valid": True,
                "evidence_only": True,
                "target": "node-red",
                "terminal_state": "drained",
                "queue_depth": 0,
                "max_in_flight": 1,
            },
            result["runtime_evidence"],
        )
        self.assertNotIn("safe", result["runtime_evidence"])

    def test_anomalous_capture_reports_duplicate_ids_and_invalid_runtime_evidence(self) -> None:
        result = analyze_capture(
            {
                "schema_version": "capture/v1",
                "expected_request_ids": ["run:block-1", "run:block-2"],
                "completed_request_ids": [
                    "run:block-1",
                    "run:block-1",
                    "run:unexpected",
                ],
                "expected_unit_ids": [1, 1, 2],
                "runtime_metadata": {
                    "target": "node-red",
                    "terminal_state": "drained",
                    "queue_depth": -1,
                    "max_in_flight": "one",
                },
                "samples": [
                    {
                        "request_id": "run:block-1",
                        "unit_id": 1,
                        "point_id": "p1",
                        "timestamp": "2026-01-01T00:00:00Z",
                    },
                    {
                        "request_id": "run:unexpected",
                        "point_id": "p3",
                        "timestamp": "2026-01-01T00:00:02Z",
                    },
                ],
            }
        )

        campaign = result["campaign"]
        self.assertFalse(campaign["requests_complete"])
        self.assertFalse(campaign["batch_complete"])
        self.assertEqual(["run:block-1"], campaign["duplicate_completed_request_ids"])
        self.assertEqual(["run:unexpected"], campaign["unexpected_request_ids"])
        self.assertEqual([2], campaign["missing_unit_ids"])
        self.assertEqual([1], campaign["duplicate_unit_ids"])
        self.assertEqual(["run:unexpected"], campaign["missing_unit_id_request_ids"])
        self.assertEqual(
            {
                "available": True,
                "valid": False,
                "evidence_only": True,
                "target": "node-red",
                "terminal_state": "drained",
                "queue_depth": None,
                "max_in_flight": None,
            },
            result["runtime_evidence"],
        )
        codes = {item["code"] for item in result["findings"]}
        self.assertIn("CAMPAIGN_COMPLETED_REQUESTS_DUPLICATED", codes)
        self.assertIn("CAMPAIGN_UNITS_MISSING", codes)
        self.assertIn("CAMPAIGN_UNITS_DUPLICATED", codes)
        self.assertIn("CAMPAIGN_UNIT_ID_MISSING", codes)
        self.assertIn("RUNTIME_METADATA_INVALID", codes)


if __name__ == "__main__":
    unittest.main()
