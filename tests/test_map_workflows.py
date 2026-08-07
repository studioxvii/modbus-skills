from __future__ import annotations

import sys
import unittest
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.map_workflows import (  # noqa: E402
    diagnose_map,
    lint_map,
    normalize_map,
    review_parse_evidence,
)
from modbus_skills.parsers import parse_csv, parse_json  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "maps"


class NormalizeMapTests(unittest.TestCase):
    def test_missing_route_is_a_hold_and_is_not_guessed(self) -> None:
        result = normalize_map(
            [
                {
                    "logical_point_id": "voltage",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "uint16",
                }
            ]
        )

        self.assertIsNone(result["points"][0]["route_id"])
        self.assertIn(
            "point.route-id-unresolved",
            {hold["code"] for hold in result["holds"]},
        )
        self.assertNotIn(
            "default-route-id",
            {assumption["code"] for assumption in result["assumptions"]},
        )

    def test_synthetic_csv_normalizes_without_blocking_holds(self) -> None:
        parsed = parse_csv((FIXTURES / "synthetic_registers.csv").read_bytes())
        result = normalize_map(parsed)
        self.assertEqual([], result["holds"])
        self.assertEqual(3, result["summary"]["confirmed_points"])
        flow = result["points"][1]
        self.assertEqual(2, flow["protocol_offset"])
        self.assertEqual("holding-register", flow["area"])
        self.assertEqual("float32", flow["datatype"])
        self.assertEqual("CDAB", flow["byte_order"])
        self.assertTrue(flow["byte_order_confirmed"])
        self.assertEqual("ABCDEFGH", result["points"][2]["byte_order"])

    def test_modicon_and_protocol_offsets_are_explicitly_resolved(self) -> None:
        parsed = parse_json((FIXTURES / "synthetic_registers.json").read_bytes())
        result = normalize_map(parsed)
        points = {point["logical_point_id"]: point for point in result["points"]}
        self.assertEqual(0, points["temperature"]["protocol_offset"])
        self.assertEqual(20, points["pump_running"]["protocol_offset"])
        self.assertEqual("40001", points["temperature"]["display_address"])
        self.assertEqual("00021", points["pump_running"]["display_address"])

    def test_ambiguous_address_and_unknown_fields_create_holds(self) -> None:
        result = normalize_map(
            [
                {
                    "address": 40001,
                    "area": "vendor table",
                    "unit_id": 1,
                    "datatype": "vendor float",
                    "byte_order": "ZYXW",
                }
            ]
        )
        point = result["points"][0]
        self.assertIsNone(point["area"])
        self.assertIsNone(point["protocol_offset"])
        self.assertIsNone(point["datatype"])
        self.assertIsNone(point["byte_order"])
        self.assertEqual("pending", point["normalization_status"])
        codes = {hold["code"] for hold in result["holds"]}
        self.assertIn("point.area-unrecognized", codes)
        self.assertIn("address.convention-unresolved", codes)
        self.assertIn("point.datatype-unrecognized", codes)
        self.assertIn("point.byte-order-unrecognized", codes)

    def test_caller_defaults_are_recorded_and_not_silent(self) -> None:
        result = normalize_map(
            [{"address": 1, "name": "Synthetic"}],
            defaults={
                "route_id": "lab-line-a",
                "address_convention": "one-based-offset",
                "area": "input-register",
                "unit_id": 7,
                "datatype": "uint16",
            },
        )
        self.assertEqual([], result["holds"])
        self.assertEqual(0, result["points"][0]["protocol_offset"])
        default_fields = {
            assumption.get("field")
            for assumption in result["assumptions"]
            if assumption["code"] == "workflow-default"
        }
        self.assertEqual(
            {"route_id", "source_address.convention", "area", "unit_id", "datatype"},
            default_fields,
        )

    def test_generated_point_id_is_stable_and_reported(self) -> None:
        source = [
            {
                "protocol_offset": 0,
                "area": "holding-register",
                "unit_id": 1,
                "route_id": "lab-line-a",
                "datatype": "uint16",
                "name": "Synthetic",
            }
        ]
        first = normalize_map(source)
        second = normalize_map(source)
        self.assertEqual(first["points"][0]["logical_point_id"], second["points"][0]["logical_point_id"])
        self.assertTrue(
            any(item["code"] == "generated-logical-point-id" for item in first["assumptions"])
        )

    def test_pending_byte_order_keeps_layout_as_blocked_evidence(self) -> None:
        base = {
            "protocol_offset": 0,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "datatype": "float32",
        }
        for byte_value in (
            {"byte_order": "ABCD", "byte_order_confirmed": False},
            {
                "byte_order": {"layout": "CDAB", "status": "pending"},
                "byte_order_status": None,
                "byte_order_confirmed": None,
            },
        ):
            with self.subTest(byte_value=byte_value):
                result = normalize_map([{**base, **byte_value}])
                point = result["points"][0]
                self.assertIn(point["byte_order"], {"ABCD", "CDAB"})
                self.assertFalse(point["byte_order_confirmed"])
                self.assertEqual("pending", point["byte_order_status"])
                self.assertIn(
                    "point.byte-order-unconfirmed",
                    {hold["code"] for hold in result["holds"]},
                )

    def test_all_address_representations_are_compared_and_preserved(self) -> None:
        record = {
            "protocol_offset": 0,
            "display_address": "40001",
            "source_address": {"raw": 0, "convention": "protocol-offset"},
            "address": 1,
            "address_convention": "one-based-offset",
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "datatype": "uint16",
        }
        result = normalize_map([record])

        self.assertEqual([], result["holds"])
        self.assertEqual(4, len(result["points"][0]["address_representations"]))
        self.assertEqual(
            {"protocol_offset", "display_address", "source_address", "address"},
            {
                item["source_field"]
                for item in result["points"][0]["address_representations"]
            },
        )

    def test_conflicting_or_unverifiable_secondary_addresses_block(self) -> None:
        base = {
            "protocol_offset": 0,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "datatype": "uint16",
        }
        conflict = normalize_map([{**base, "display_address": "40002"}])
        unverifiable = normalize_map([{**base, "address": 40001}])

        self.assertIn(
            "point.address-representation-conflict",
            {hold["code"] for hold in conflict["holds"]},
        )
        self.assertIn(
            "point.address-secondary-unverifiable",
            {hold["code"] for hold in unverifiable["holds"]},
        )

    def test_explicit_function_code_is_preserved_and_checked(self) -> None:
        base = {
            "protocol_offset": 0,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "datatype": "uint16",
        }
        valid = normalize_map([{**base, "function_code": 3}])
        unsafe = normalize_map([{**base, "function_code": 16}])
        mismatch = normalize_map([{**base, "function_code": 4}])
        fractional = normalize_map([{**base, "function_code": 3.5}])

        self.assertEqual(3, valid["points"][0]["function_code"])
        self.assertEqual(16, unsafe["points"][0]["function_code"])
        self.assertIn("function-code.write-forbidden", {item["code"] for item in unsafe["holds"]})
        self.assertIn("function-code.area-mismatch", {item["code"] for item in mismatch["holds"]})
        self.assertIn("function-code.invalid", {item["code"] for item in fractional["holds"]})

    def test_fractional_identity_span_address_and_nonfinite_scale_are_rejected(self) -> None:
        base = {
            "protocol_offset": 0,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "datatype": "uint16",
        }
        cases = (
            ({**base, "unit_id": 1.5}, "point.unit-id-invalid"),
            ({**base, "protocol_offset": 0.5}, "address.invalid"),
            ({**base, "word_span": 1.5}, "point.span-invalid"),
            ({**base, "scale": math.inf}, "point.scale-invalid"),
        )
        for record, code in cases:
            with self.subTest(code=code):
                result = normalize_map([record])
                self.assertIn(code, {hold["code"] for hold in result["holds"]})


class LintAndReviewTests(unittest.TestCase):
    def test_pdf_candidate_review_and_normalization_preserve_ocr_hold(self) -> None:
        ocr_hold = {
            "code": "pdf-ocr-human-review-required",
            "severity": "hold",
            "blocking": True,
            "message": "Review every OCR-derived candidate against its source page before normalization.",
        }
        pdf_candidate = {
            "schema_version": "modbus-pdf-extraction/v1",
            "artifact_type": "modbus-pdf-extraction",
            "input_hashes": {"pdf": "0" * 64},
            "assumptions": [],
            "findings": [],
            "holds": [ocr_hold],
            "status": "held",
            "records": [
                {
                    "address": "40001",
                    "name": "Tank Level",
                    "datatype": "float32",
                    "_source": {
                        "format": "pdf",
                        "page": 42,
                        "line": 2,
                        "method": "ocr-derived",
                        "printed_page_label": "A-7",
                    },
                }
            ],
            "rejected_rows": [],
            "warnings": [],
        }

        candidate_review = review_parse_evidence(
            pdf_candidate,
            lint_result=lint_map(pdf_candidate),
        )
        normalized = normalize_map(
            pdf_candidate,
            defaults={
                "route_id": "lab",
                "unit_id": 1,
                "area": "holding-register",
                "address_convention": "modicon-reference",
                "byte_order": {"layout": "ABCD", "confirmed": True},
            },
        )
        normalized_review = review_parse_evidence(normalized)

        self.assertEqual("extraction-candidate", candidate_review["input_stage"])
        self.assertFalse(candidate_review["normalization_performed"])
        self.assertEqual("blocked", candidate_review["review_status"])
        self.assertEqual(42, candidate_review["items"][0]["source_location"]["page"])
        self.assertEqual(
            "pdf-ocr-human-review-required",
            candidate_review["global_findings"][0]["code"],
        )
        self.assertIn(
            "pdf-ocr-human-review-required",
            {hold["code"] for hold in normalized["holds"]},
        )
        self.assertEqual([ocr_hold], normalized["source_holds"])
        self.assertEqual("blocked", normalized_review["review_status"])

        disposed_candidate = dict(pdf_candidate)
        disposed_candidate["holds"] = [
            {
                **ocr_hold,
                "disposition": {
                    "status": "resolved",
                    "reason": "The OCR values were checked against page A-7.",
                },
            }
        ]
        disposed = normalize_map(
            disposed_candidate,
            defaults={
                "route_id": "lab",
                "unit_id": 1,
                "area": "holding-register",
                "address_convention": "modicon-reference",
                "byte_order": {"layout": "ABCD", "confirmed": True},
            },
        )
        self.assertNotIn(
            "pdf-ocr-human-review-required",
            {hold["code"] for hold in disposed["holds"]},
        )
        self.assertEqual([], disposed["holds"])
        self.assertEqual("resolved", disposed["source_holds"][0]["disposition"]["status"])

    def test_lint_calls_core_validation_for_physical_overlap(self) -> None:
        normalized = normalize_map(
            [
                {
                    "protocol_offset": 0,
                    "area": "holding-register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "logical_point_id": "a",
                    "datatype": "uint32",
                    "byte_order": "ABCD",
                },
                {
                    "protocol_offset": 1,
                    "area": "holding-register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "logical_point_id": "b",
                    "datatype": "uint16",
                },
            ]
        )
        lint = lint_map(normalized)
        self.assertTrue(any(finding["code"] == "point.overlapping-range" for finding in lint["findings"]))
        self.assertGreater(lint["summary"]["errors"], 0)

    def test_review_preserves_source_evidence_and_rejected_rows(self) -> None:
        parsed = parse_csv("Address,Name\n,Missing\n0,Present\n", delimiter=",")
        normalized = normalize_map(parsed)
        review = review_parse_evidence(normalized)
        self.assertEqual(1, review["summary"]["rejected_rows"])
        self.assertTrue(review["items"][0]["source_evidence"])
        self.assertEqual("blocked", review["review_status"])

    def test_rejected_rows_block_review_until_disposition(self) -> None:
        parsed = parse_csv(
            "Protocol Offset,Area,Unit ID,Route ID,Data Type\n"
            "0,holding-register,1,lab,uint16\n"
            ",holding-register,1,lab,uint16\n",
            delimiter=",",
        )
        normalized = normalize_map(parsed)
        blocked = review_parse_evidence(normalized)
        normalized["rejected_rows"][0]["disposition"] = {
            "status": "excluded",
            "reason": "The synthetic row has no address.",
        }
        disposed = review_parse_evidence(normalized)

        self.assertEqual("blocked", blocked["review_status"])
        self.assertEqual(1, blocked["summary"]["unresolved_rejected_rows"])
        self.assertEqual("ready-for-human-review", disposed["review_status"])

    def test_diagnose_map_runs_the_complete_chain(self) -> None:
        result = diagnose_map(
            "Address,Address Convention,Area,Unit ID,Route ID,Data Type\n1,one-based-offset,coil,1,lab-line-a,bool\n",
            source_format="csv",
            delimiter=",",
        )
        self.assertEqual(0, result["canonical_map"]["points"][0]["protocol_offset"])
        self.assertEqual(0, result["lint"]["summary"]["blocking"])
        self.assertEqual("ready-for-human-review", result["review"]["review_status"])


if __name__ == "__main__":
    unittest.main()
