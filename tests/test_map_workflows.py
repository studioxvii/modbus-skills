from __future__ import annotations

import sys
import unittest
import math
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.map_workflows import (  # noqa: E402
    MapWorkflowError,
    diagnose_map,
    lint_map,
    normalize_map,
    review_parse_evidence,
)
from modbus_skills.parsers import parse_csv, parse_json  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "maps"


class NormalizeMapTests(unittest.TestCase):
    def test_empty_object_is_not_verified_map_evidence(self) -> None:
        with self.assertRaises(MapWorkflowError):
            review_parse_evidence({})

    def test_canonical_area_spelling_is_recognized_by_parser(self) -> None:
        result = parse_json('[{"area": "holding-register", "protocol_offset": 0}]')
        self.assertNotIn("unrecognized_area", {finding["code"] for finding in result["warnings"]})

    def test_datatype_width_must_match_explicit_register_span(self) -> None:
        result = normalize_map(
            [
                {
                    "logical_point_id": "bad-width",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "uint32",
                    "word_span": 1,
                }
            ]
        )

        self.assertIn(
            "point.datatype-span-mismatch",
            {hold["code"] for hold in result["holds"]},
        )
        self.assertEqual("pending", result["points"][0]["normalization_status"])

    def test_area_is_derived_from_a_declared_read_function_code(self) -> None:
        # Vendor register lists sometimes state only a Modbus function code
        # (e.g. "Modbus Function Code" = 2) with no separate area/register
        # type column. FC01-FC04 and the register area are the same fact in
        # the protocol, so this is a deterministic lookup, not a guess.
        result = normalize_map(
            [
                {
                    "logical_point_id": "di0",
                    "route_id": "lab",
                    "unit_id": 1,
                    "protocol_offset": 0,
                    "datatype": "bool",
                    "function_code": 2,
                }
            ]
        )

        point = result["points"][0]
        self.assertEqual("discrete-input", point["area"])
        self.assertNotIn(
            "point.area-unresolved",
            {hold["code"] for hold in result["holds"]},
        )
        self.assertIn(
            "area-from-function-code",
            {assumption["code"] for assumption in result["assumptions"]},
        )

    def test_area_from_function_code_ignores_write_and_out_of_range_codes(self) -> None:
        result = normalize_map(
            [
                {
                    "logical_point_id": "coil-write",
                    "route_id": "lab",
                    "unit_id": 1,
                    "protocol_offset": 0,
                    "datatype": "bool",
                    "function_code": 6,
                }
            ]
        )

        point = result["points"][0]
        self.assertIsNone(point["area"])
        self.assertIn(
            "point.area-unresolved",
            {hold["code"] for hold in result["holds"]},
        )

    def test_one_register_integer_byte_order_is_not_applicable(self) -> None:
        result = normalize_map(
            [
                {
                    "logical_point_id": "one-word",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "uint16",
                    "byte_order": "BA",
                }
            ]
        )

        point = result["points"][0]
        self.assertIsNone(point["byte_order"])
        self.assertIsNone(point["byte_order_confirmed"])
        self.assertEqual("not-applicable", point["byte_order_status"])
        self.assertNotIn(
            "point.byte-order-unrecognized",
            {hold["code"] for hold in result["holds"]},
        )

    def test_source_bit_order_is_copied_onto_canonical_points(self) -> None:
        declared = normalize_map(
            [
                {
                    "logical_point_id": "alarm-bits",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "bool",
                    "function_code": 3,
                    "bit_order": "LSB_0",
                }
            ]
        )
        missing = normalize_map(
            [
                {
                    "logical_point_id": "alarm-bits",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "bool",
                    "function_code": 3,
                }
            ]
        )

        self.assertEqual("lsb0", declared["points"][0]["bit_order"])
        self.assertNotIn("bit_order", declared["points"][0]["unmapped_fields"])
        self.assertIsNone(missing["points"][0]["bit_order"])
        self.assertNotIn(
            "point.bit-order-unresolved",
            {item["code"] for item in lint_map(declared)["findings"]},
        )
        self.assertIn(
            "point.bit-order-unresolved",
            {item["code"] for item in lint_map(missing)["findings"]},
        )

    def test_simulator_profile_and_runtime_provenance_are_validated_and_preserved(self) -> None:
        source_hash = "a" * 64
        source = {
            "records": [
                {
                    "logical_point_id": "simulated-point",
                    "route_id": "lab",
                    "unit_id": 2,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "uint16",
                }
            ],
            "source_map_hash": source_hash,
            "simulator_profile": {
                "schema_version": "modbus-simulator-profile/v1",
                "profile_id": "synthetic-read-only",
                "supported_areas": ["holding-register"],
                "supported_datatypes": ["uint16"],
                "max_unit_id": 10,
                "max_word_span": 1,
            },
            "runtime_observation": {
                "source_map_hash": source_hash,
                "observation_id": "observation-001",
                "observed_values": {"simulated-point": 17},
            },
        }

        result = normalize_map(source)

        self.assertEqual(source_hash, result["source_map_hash"])
        self.assertEqual(source["simulator_profile"], result["simulator_profile"])
        self.assertEqual(source["runtime_observation"], result["runtime_observation"])
        self.assertEqual([], result["holds"])

    def test_simulator_semantics_and_stale_runtime_observation_are_held(self) -> None:
        source = {
            "records": [
                {
                    "logical_point_id": "unsupported-point",
                    "route_id": "lab",
                    "unit_id": 11,
                    "area": "input-register",
                    "protocol_offset": 0,
                    "datatype": "float32",
                    "byte_order": "ABCD",
                }
            ],
            "source_map_hash": "a" * 64,
            "simulator_profile": {
                "schema_version": "modbus-simulator-profile/v1",
                "profile_id": "synthetic-read-only",
                "supported_areas": ["holding-register"],
                "supported_datatypes": ["uint16"],
                "max_unit_id": 10,
                "max_word_span": 1,
            },
            "runtime_observation": {
                "source_map_hash": "b" * 64,
                "observation_id": "stale-observation",
            },
        }

        result = normalize_map(source)
        codes = {hold["code"] for hold in result["holds"]}
        self.assertIn("simulator.point-area-unsupported", codes)
        self.assertIn("simulator.point-datatype-unsupported", codes)
        self.assertIn("simulator.point-unit-id-unsupported", codes)
        self.assertIn("simulator.point-span-unsupported", codes)
        self.assertIn("runtime-observation.source-map-hash-mismatch", codes)

        lint_codes = {finding["code"] for finding in lint_map(result)["findings"]}
        self.assertTrue(codes.issubset(lint_codes))

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

    def test_tcp_gateway_unit_ids_are_held_with_scope_disclosure(self) -> None:
        for unit_id in (0, 255):
            with self.subTest(unit_id=unit_id):
                result = normalize_map(
                    [
                        {
                            "logical_point_id": "unsupported-unit",
                            "route_id": "lab",
                            "unit_id": unit_id,
                            "area": "holding-register",
                            "protocol_offset": 0,
                            "datatype": "uint16",
                        }
                    ]
                )
                hold = next(
                    item
                    for item in result["holds"]
                    if item["code"] == "point.unit-id-invalid"
                )

                self.assertIsNone(result["points"][0]["unit_id"])
                self.assertIn("1 through 247", hold["message"])
                self.assertIn("broadcast requests", hold["message"])
                self.assertIn(
                    "Modbus TCP gateway unit IDs 0 and 255", hold["message"]
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

    def test_generated_point_id_does_not_change_when_source_row_moves(self) -> None:
        base = {
            "protocol_offset": 10,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab-line-a",
            "datatype": "uint16",
            "name": "Stable point",
        }
        first = normalize_map([{**base, "_source": {"format": "csv", "row": 2}}])
        second = normalize_map([{**base, "_source": {"format": "csv", "row": 200}}])

        self.assertEqual(
            first["points"][0]["logical_point_id"],
            second["points"][0]["logical_point_id"],
        )

    def test_generated_point_id_collision_requires_explicit_unique_ids(self) -> None:
        base = {
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab-line-a",
            "datatype": "uint16",
            "name": "Duplicate label",
        }

        result = normalize_map(
            [
                {**base, "protocol_offset": 10},
                {**base, "protocol_offset": 20},
            ]
        )

        self.assertEqual(
            result["points"][0]["logical_point_id"],
            result["points"][1]["logical_point_id"],
        )
        matching = [
            hold
            for hold in result["holds"]
            if hold["code"] == "point.generated-logical-id-collision"
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual(2, matching[0]["details"]["record_count"])
        self.assertEqual(0, result["summary"]["confirmed_points"])
        self.assertEqual(2, result["summary"]["pending_points"])

    def test_string_and_access_flags_from_review_csv_are_normalized(self) -> None:
        result = normalize_map(
            [
                {
                    "address": 40001,
                    "address_convention": "modicon-reference",
                    "area": "holding_register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "name": "Device label",
                    "datatype": "STRING",
                    "word_count": 8,
                    "access_readable": "true",
                    "access_writable": "false",
                    "function_read_codes": "03; 04",
                }
            ]
        )

        self.assertEqual([], result["holds"])
        point = result["points"][0]
        self.assertEqual("string", point["datatype"])
        self.assertEqual(8, point["word_span"])
        self.assertIsNone(point["byte_order"])
        self.assertEqual("read-only", point["access"])
        self.assertEqual([3, 4], point["read_function_codes"])
        self.assertEqual(3, point["function_code"])

    def test_source_include_and_review_flags_are_retained_without_implied_approval(self) -> None:
        base = {
            "address": 40001,
            "address_convention": "modicon-reference",
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "name": "Review state",
            "datatype": "uint16",
            "access": "read-only",
        }
        reviewed = normalize_map([{**base, "include": "yes", "reviewed": "yes"}])
        pending = normalize_map([{**base, "include": "yes", "reviewed": "no"}])
        excluded = normalize_map([{**base, "include": "no", "reviewed": "yes"}])

        self.assertTrue(reviewed["points"][0]["source_include"])
        self.assertTrue(reviewed["points"][0]["source_reviewed"])
        self.assertNotEqual("approved", reviewed.get("review_status"))
        self.assertFalse(pending["points"][0]["source_reviewed"])
        self.assertIn(
            "point.source-review-incomplete",
            {hold["code"] for hold in pending["holds"]},
        )
        self.assertFalse(excluded["points"][0]["source_include"])
        self.assertIn(
            "point.source-excluded",
            {hold["code"] for hold in excluded["holds"]},
        )

    def test_write_only_source_is_held_and_not_given_a_read_function(self) -> None:
        result = normalize_map(
            [
                {
                    "protocol_offset": 12,
                    "area": "holding-register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "name": "Reset command",
                    "datatype": "uint16",
                    "access_readable": "false",
                    "access_writable": "true",
                    "function_write_codes": "06",
                }
            ]
        )

        self.assertIn(
            "point.write-only-not-readable",
            {hold["code"] for hold in result["holds"]},
        )
        self.assertEqual("write-only", result["points"][0]["access"])
        self.assertIsNone(result["points"][0]["function_code"])
        self.assertEqual([6], result["points"][0]["write_function_codes"])

    def test_partial_access_flags_without_proven_readability_are_held(self) -> None:
        base = {
            "protocol_offset": 12,
            "area": "holding-register",
            "unit_id": 1,
            "route_id": "lab",
            "name": "Partial access",
            "datatype": "uint16",
        }
        cases = (
            ({"access_readable": "false"}, "point.not-readable"),
            ({"access_writable": "true"}, "point.access-unresolved"),
        )

        for access_fields, expected_hold in cases:
            with self.subTest(access_fields=access_fields):
                result = normalize_map([{**base, **access_fields}])

                self.assertIn(
                    expected_hold,
                    {hold["code"] for hold in result["holds"]},
                )
                self.assertEqual("pending", result["points"][0]["normalization_status"])
                self.assertIsNone(result["points"][0]["function_code"])
                self.assertEqual(
                    (),
                    compile_read_plan(result["points"]).requests,
                )

    def test_vendor_hex_function_code_suffixes_are_parsed(self) -> None:
        result = normalize_map(
            [
                {
                    "protocol_offset": 0,
                    "area": "holding-register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "datatype": "uint16",
                    "function_read_codes": "FC03h; 04h",
                    "function_write_codes": "06h",
                }
            ]
        )

        self.assertEqual([], result["holds"])
        self.assertEqual([3, 4], result["points"][0]["read_function_codes"])
        self.assertEqual([6], result["points"][0]["write_function_codes"])
        self.assertEqual(3, result["points"][0]["function_code"])

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
    def test_lint_deduplicates_the_same_workflow_and_core_hold(self) -> None:
        normalized = normalize_map(
            [
                {
                    "logical_point_id": "multiword",
                    "protocol_offset": 0,
                    "area": "holding-register",
                    "unit_id": 1,
                    "route_id": "lab",
                    "datatype": "uint32",
                }
            ]
        )

        lint = lint_map(normalized)

        matching = [
            finding
            for finding in lint["findings"]
            if finding["code"] == "point.byte-order-unresolved"
        ]
        self.assertEqual(1, len(matching))

    def test_lint_preserves_distinct_workflow_holds_with_the_same_code(self) -> None:
        holds = [
            {
                "code": "point.address-secondary-unverifiable",
                "severity": "hold",
                "blocking": True,
                "message": "Review this address representation.",
                "field": "address",
                "point_ids": ["point-a"],
                "details": {"source_field": source_field},
            }
            for source_field in ("display_address", "source_address")
        ]

        lint = lint_map({"points": [], "holds": holds})
        matching = [
            finding
            for finding in lint["findings"]
            if finding["code"] == "point.address-secondary-unverifiable"
        ]

        self.assertEqual(2, len(matching))
        self.assertEqual(
            {"display_address", "source_address"},
            {finding["details"]["source_field"] for finding in matching},
        )

    def test_pdf_candidate_review_and_normalization_preserve_ocr_hold(self) -> None:
        ocr_hold = {
            "code": "pdf-ocr-human-review-required",
            "severity": "hold",
            "blocking": True,
            "message": "Confirm or correct the bounded extraction as one batch.",
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
        self.assertEqual("batch-exceptions", candidate_review["review_mode"])
        self.assertEqual(1, candidate_review["summary"]["blocking_decisions"])
        self.assertEqual(1, len(candidate_review["decision_groups"]))
        self.assertFalse(candidate_review["items"][0]["action_required"])
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
        self.assertEqual("ready", disposed["review_status"])

    def test_diagnose_map_runs_the_complete_chain(self) -> None:
        result = diagnose_map(
            "Address,Address Convention,Area,Unit ID,Route ID,Data Type\n1,one-based-offset,coil,1,lab-line-a,bool\n",
            source_format="csv",
            delimiter=",",
        )
        self.assertEqual(0, result["canonical_map"]["points"][0]["protocol_offset"])
        self.assertEqual(0, result["lint"]["summary"]["blocking"])
        self.assertEqual("ready", result["review"]["review_status"])

    def test_diagnose_map_reviews_a_pdf_source_in_one_invocation(self) -> None:
        extraction = {
            "schema_version": "modbus-pdf-extraction/v1",
            "artifact_type": "modbus-pdf-extraction",
            "status": "extracted",
            "records": [
                {
                    "address": "40001",
                    "name": "Tank Level",
                    "datatype": "uint16",
                    "_source": {"format": "pdf", "page": 2, "line": 1, "method": "layout"},
                }
            ],
            "rejected_rows": [],
            "warnings": [],
            "holds": [],
            "findings": [],
        }
        with mock.patch(
            "modbus_skills.map_workflows.extract_pdf",
            return_value=extraction,
        ) as extract:
            result = diagnose_map(
                b"%PDF-1.4 synthetic register map",
                filename="manual.pdf",
                defaults={
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "address_convention": "modicon-reference",
                },
            )
        extract.assert_called_once()
        self.assertEqual("Tank Level", result["canonical_map"]["points"][0]["name"])
        self.assertIn("lint", result)
        self.assertIn("review", result)
        self.assertEqual(extraction, result["parsed"])

    def test_global_source_confirmation_is_one_batch_not_one_decision_per_page(self) -> None:
        source = {
            "schema_version": "modbus-pdf-extraction/v1",
            "source": {"filename": "synthetic.pdf", "sha256": "1" * 64},
            "page_selection": {"first_page": 10, "last_page": 12},
            "holds": [
                {
                    "code": "pdf-human-review-required",
                    "severity": "hold",
                    "blocking": True,
                    "message": "Confirm the bounded extraction as one batch.",
                }
            ],
            "records": [
                {
                    "id": f"register-{page}",
                    "name": f"Register {page}",
                    "address": str(40000 + page),
                    "_source": {"page": page, "method": "coordinate-derived"},
                }
                for page in (10, 11, 12)
            ],
            "rejected_rows": [],
        }

        review = review_parse_evidence(source)

        self.assertEqual("blocked", review["review_status"])
        self.assertEqual(1, review["summary"]["blocking_decisions"])
        self.assertEqual(3, review["decision_groups"][0]["affected_count"])
        self.assertEqual("artifact", review["decision_groups"][0]["scope"])
        self.assertTrue(all(not item["action_required"] for item in review["items"]))
        self.assertEqual(
            {"first_page": 10, "last_page": 12},
            review["batch_scope"]["page_selection"],
        )


if __name__ == "__main__":
    unittest.main()
