from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.artifacts import artifact_envelope, stable_input_hash
from modbus_skills.byte_order import RawSample, evaluate_byte_orders
from modbus_skills.decisions import ReviewDecisionError, apply_review_decisions
from modbus_skills.map_workflows import normalize_map


def draft_map() -> dict:
    return normalize_map(
        [
            {
                "logical_point_id": "runtime",
                "name": "Runtime",
                "route_id": "lab",
                "unit_id": 1,
                "area": "holding-register",
                "protocol_offset": 10,
                "datatype": "uint32",
                "word_span": 2,
                "access": "read-only",
            },
            {
                "logical_point_id": "reset",
                "name": "Reset",
                "route_id": "lab",
                "unit_id": 1,
                "area": "holding-register",
                "protocol_offset": 20,
                "datatype": "uint16",
                "word_span": 1,
                "access": "write-only",
            },
        ]
    )


def decision_record(
    *decisions: dict,
    approve_map: bool = True,
    canonical_map: dict | None = None,
) -> dict:
    return {
        "schema_version": "modbus-review-decisions/v1",
        "canonical_map_hash": stable_input_hash(canonical_map or draft_map()),
        "review_id": "review-001",
        "reviewed_at": "2026-08-07T12:00:00-04:00",
        "reviewer": "commissioning-engineer",
        "approve_map": approve_map,
        "hold_decisions": [],
        "decisions": list(decisions),
    }


def byte_order_evidence(canonical_map: dict, datatype: str = "uint32") -> dict:
    point = canonical_map["points"][0]
    evaluation = evaluate_byte_orders(
        RawSample("sample-001", (0, 3600)),
        datatypes=(datatype,),
        layouts=("ABCD",),
    ).to_dict()
    return artifact_envelope(
        {
            **evaluation,
            "sample_identity": {
                "sample_id": "sample-001",
                "point_id": point["logical_point_id"],
                "route_id": point["route_id"],
                "unit_id": point["unit_id"],
                "area": point["area"],
                "protocol_offset": point["protocol_offset"],
                "timestamp": "2026-08-07T11:50:00-04:00",
            },
        },
        schema_version="modbus-byte-order-evidence/v1",
        inputs={"capture": {"sample_id": "sample-001"}},
        assumptions=[],
        findings=[],
        holds=[
            {
                "code": "byte-order-human-confirmation-required",
                "severity": "hold",
                "blocking": True,
                "message": "A human must confirm one layout.",
            }
        ],
    )


class ReviewDecisionTests(unittest.TestCase):
    def test_resolves_one_global_source_hold_for_a_complete_batch(self) -> None:
        canonical = normalize_map(
            {
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
                        "logical_point_id": "status",
                        "route_id": "lab",
                        "unit_id": 1,
                        "area": "holding-register",
                        "protocol_offset": 0,
                        "datatype": "uint16",
                        "access": "read-only",
                    }
                ],
            }
        )
        record = decision_record(canonical_map=canonical)
        record["hold_decisions"] = [
            {
                "code": "pdf-human-review-required",
                "reason": "The bounded three-page extraction matches the source.",
                "evidence_refs": [
                    "source-sha256:111111;pages:10-12;records:3;exceptions:none"
                ],
            }
        ]

        result = apply_review_decisions(canonical, record)

        self.assertEqual("approved", result["review_status"])
        self.assertEqual([], result["holds"])
        self.assertEqual(
            "resolved", result["source_holds"][0]["disposition"]["status"]
        )
        self.assertEqual(
            "resolve-hold",
            next(
                item
                for item in result["review_decisions"]
                if item.get("hold_code") == "pdf-human-review-required"
            )["action"],
        )

    def test_batch_hold_decision_cannot_bypass_point_specific_holds(self) -> None:
        canonical = draft_map()
        record = decision_record(canonical_map=canonical, approve_map=False)
        record["hold_decisions"] = [
            {
                "code": "point.byte-order-unresolved",
                "reason": "Unsafe attempt to resolve all point holds by code.",
                "evidence_refs": ["source-map"],
            }
        ]

        with self.assertRaisesRegex(ReviewDecisionError, "unknown hold code"):
            apply_review_decisions(canonical, record)

    def test_confirms_byte_order_excludes_write_only_point_and_approves(self) -> None:
        canonical = draft_map()
        evidence = byte_order_evidence(canonical)
        evidence_hash = stable_input_hash(evidence)
        result = apply_review_decisions(
            canonical,
            decision_record(
                {
                    "point_id": "runtime",
                    "field": "byte_order",
                    "value": "ABCD",
                    "reason": "One bounded raw sample decoded to the expected seconds value.",
                    "evidence_refs": [f"sha256:{evidence_hash}"],
                },
                {
                    "point_id": "reset",
                    "action": "exclude",
                    "reason": "The source declares this point as write-only.",
                    "evidence_refs": [],
                },
                canonical_map=canonical,
            ),
            evidence_artifacts={evidence_hash: evidence},
        )

        self.assertEqual("approved", result["review_status"])
        self.assertEqual([], result["holds"])
        self.assertEqual(1, len(result["points"]))
        self.assertEqual(1, len(result["excluded_points"]))
        self.assertEqual("ABCD", result["points"][0]["byte_order"])
        self.assertTrue(result["points"][0]["byte_order_confirmed"])
        self.assertEqual("confirmed", result["points"][0]["byte_order_status"])

    def test_applies_bit_order_and_clears_packed_bit_hold(self) -> None:
        canonical = normalize_map(
            [
                {
                    "logical_point_id": "alarm-bits",
                    "name": "Alarm bits",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "datatype": "bool",
                    "access": "read-only",
                    "function_code": 3,
                }
            ]
        )
        result = apply_review_decisions(
            canonical,
            decision_record(
                {
                    "point_id": "alarm-bits",
                    "field": "bit_order",
                    "value": "lsb0",
                    "reason": "The OEM table numbers packed bits with bit 0 as LSB.",
                    "evidence_refs": ["manual-bit-numbering"],
                },
                canonical_map=canonical,
            ),
        )

        self.assertEqual("lsb0", result["points"][0]["bit_order"])
        self.assertEqual("approved", result["review_status"])
        self.assertNotIn(
            "point.bit-order-unresolved",
            {hold["code"] for hold in result["holds"]},
        )

    def test_does_not_approve_while_an_unresolved_hold_remains(self) -> None:
        result = apply_review_decisions(
            draft_map(),
            decision_record(
                {
                    "point_id": "reset",
                    "action": "exclude",
                    "reason": "The point is outside this read-only map.",
                }
            ),
        )

        self.assertEqual("blocked", result["review_status"])
        self.assertIn(
            "point.byte-order-unresolved", {hold["code"] for hold in result["holds"]}
        )

    def test_rejects_write_function_and_timezone_free_review(self) -> None:
        unsafe = decision_record(
            {
                "point_id": "runtime",
                "field": "function_code",
                    "value": 16,
                    "reason": "Unsafe test.",
                    "evidence_refs": ["source-map"],
                }
        )
        with self.assertRaises(ReviewDecisionError):
            apply_review_decisions(draft_map(), unsafe)

        missing_timezone = decision_record(approve_map=False)
        missing_timezone["reviewed_at"] = "2026-08-07T12:00:00"
        with self.assertRaises(ReviewDecisionError):
            apply_review_decisions(draft_map(), missing_timezone)

    def test_rejects_tcp_gateway_unit_ids_with_scope_disclosure(self) -> None:
        for unit_id in (0, 255):
            with self.subTest(unit_id=unit_id):
                canonical = draft_map()
                decision = decision_record(
                    {
                        "point_id": "runtime",
                        "field": "unit_id",
                        "value": unit_id,
                        "reason": "Synthetic unit-ID boundary test.",
                        "evidence_refs": ["source-map"],
                    },
                    approve_map=False,
                    canonical_map=canonical,
                )

                with self.assertRaisesRegex(
                    ReviewDecisionError,
                    "1 through 247.*broadcast requests.*Modbus TCP gateway unit IDs 0 and 255",
                ):
                    apply_review_decisions(canonical, decision)

    def test_rejects_missing_schema_evidence_and_write_only_conversion(self) -> None:
        missing_schema = decision_record(approve_map=False)
        missing_schema.pop("schema_version")
        with self.assertRaisesRegex(ReviewDecisionError, "schema_version"):
            apply_review_decisions(draft_map(), missing_schema)

        missing_evidence = decision_record(
            {
                "point_id": "runtime",
                "field": "byte_order",
                "value": "ABCD",
                "reason": "A value without cited evidence is not reviewable.",
            }
        )
        with self.assertRaisesRegex(ReviewDecisionError, "evidence_refs"):
            apply_review_decisions(draft_map(), missing_evidence)

        unsafe_access = decision_record(
            {
                "point_id": "reset",
                "field": "access",
                "value": "read-only",
                "reason": "Unsafe conversion test.",
                "evidence_refs": ["source-map"],
            }
        )
        with self.assertRaisesRegex(ReviewDecisionError, "write-only"):
            apply_review_decisions(draft_map(), unsafe_access)

        unsafe_active_write_only = decision_record(
            {
                "point_id": "runtime",
                "field": "access",
                "value": "write-only",
                "reason": "Unsafe active write-only transition test.",
                "evidence_refs": ["source-map"],
            }
        )
        with self.assertRaisesRegex(ReviewDecisionError, "exclude"):
            apply_review_decisions(draft_map(), unsafe_active_write_only)

    def test_cannot_approve_an_empty_map(self) -> None:
        result = apply_review_decisions(
            draft_map(),
            decision_record(
                {
                    "point_id": "runtime",
                    "action": "exclude",
                    "reason": "Out of scope.",
                },
                {
                    "point_id": "reset",
                    "action": "exclude",
                    "reason": "Write-only point.",
                },
            ),
        )

        self.assertEqual("blocked", result["review_status"])
        self.assertIn("map.no-active-points", {hold["code"] for hold in result["holds"]})

    def test_rejects_stale_map_hash_and_mismatched_byte_order_identity(self) -> None:
        canonical = draft_map()
        evidence = byte_order_evidence(canonical)
        evidence_hash = stable_input_hash(evidence)
        decision = decision_record(
            {
                "point_id": "runtime",
                "field": "byte_order",
                "value": "ABCD",
                "reason": "Bound evidence test.",
                "evidence_refs": [f"sha256:{evidence_hash}"],
            },
            canonical_map=canonical,
        )
        decision["canonical_map_hash"] = "0" * 64
        with self.assertRaisesRegex(ReviewDecisionError, "does not match"):
            apply_review_decisions(
                canonical, decision, evidence_artifacts={evidence_hash: evidence}
            )

        decision["canonical_map_hash"] = stable_input_hash(canonical)
        mismatched = byte_order_evidence(canonical)
        mismatched["sample_identity"]["unit_id"] = 2
        mismatched_hash = stable_input_hash(mismatched)
        decision["decisions"][0]["evidence_refs"] = [
            f"sha256:{mismatched_hash}"
        ]
        with self.assertRaisesRegex(ReviewDecisionError, "unit_id"):
            apply_review_decisions(
                canonical,
                decision,
                evidence_artifacts={mismatched_hash: mismatched},
            )

    def test_byte_order_identity_uses_exact_point_and_route_ids(self) -> None:
        canonical = draft_map()
        for field, different_value in (
            ("point_id", "Runtime"),
            ("route_id", "LAB"),
            ("route_id", "l_a_b"),
        ):
            with self.subTest(field=field, value=different_value):
                evidence = byte_order_evidence(canonical)
                evidence["sample_identity"][field] = different_value
                evidence_hash = stable_input_hash(evidence)
                decision = decision_record(
                    {
                        "point_id": "runtime",
                        "field": "byte_order",
                        "value": "ABCD",
                        "reason": "Exact identity test.",
                        "evidence_refs": [f"sha256:{evidence_hash}"],
                    },
                    canonical_map=canonical,
                )
                with self.assertRaisesRegex(ReviewDecisionError, field):
                    apply_review_decisions(
                        canonical,
                        decision,
                        evidence_artifacts={evidence_hash: evidence},
                    )

    def test_byte_order_requires_one_identical_non_empty_sample_id(self) -> None:
        canonical = draft_map()
        mutations = (
            ("identity blank", lambda item: item["sample_identity"].update(sample_id=" ")),
            ("sample blank", lambda item: item["sample"].update(sample_id="")),
            (
                "candidate blank",
                lambda item: item["candidates"][0].update(sample_id=""),
            ),
            (
                "sample mismatch",
                lambda item: item["sample"].update(sample_id="sample-002"),
            ),
            (
                "candidate mismatch",
                lambda item: item["candidates"][0].update(sample_id="sample-002"),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                evidence = byte_order_evidence(canonical)
                mutate(evidence)
                evidence_hash = stable_input_hash(evidence)
                decision = decision_record(
                    {
                        "point_id": "runtime",
                        "field": "byte_order",
                        "value": "ABCD",
                        "reason": "Sample identity validation test.",
                        "evidence_refs": [f"sha256:{evidence_hash}"],
                    },
                    canonical_map=canonical,
                )
                with self.assertRaisesRegex(ReviewDecisionError, "sample_id"):
                    apply_review_decisions(
                        canonical,
                        decision,
                        evidence_artifacts={evidence_hash: evidence},
                    )

    def test_byte_order_rejects_invalid_words_width_and_tampered_candidate(self) -> None:
        canonical = draft_map()
        mutations = (
            (
                "invalid raw word",
                lambda item: item["sample"].update(words=[0, 70_000]),
                "raw words",
            ),
            (
                "wrong bit width",
                lambda item: item["sample"].update(bit_width=16),
                "bit_width",
            ),
            (
                "tampered decoded value",
                lambda item: item["candidates"][0].update(decoded_value=999),
                "decoded_value",
            ),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label):
                evidence = byte_order_evidence(canonical)
                mutate(evidence)
                evidence_hash = stable_input_hash(evidence)
                decision = decision_record(
                    {
                        "point_id": "runtime",
                        "field": "byte_order",
                        "value": "ABCD",
                        "reason": "Raw evidence validation test.",
                        "evidence_refs": [f"sha256:{evidence_hash}"],
                    },
                    canonical_map=canonical,
                )
                with self.assertRaisesRegex(ReviewDecisionError, message):
                    apply_review_decisions(
                        canonical,
                        decision,
                        evidence_artifacts={evidence_hash: evidence},
                    )

    def test_normalizes_fields_before_duplicate_detection(self) -> None:
        canonical = draft_map()
        record = decision_record(
            {
                "point_id": "runtime",
                "field": " byte-order ",
                "value": "ABCD",
                "reason": "First spelling.",
                "evidence_refs": ["source-map"],
            },
            {
                "point_id": "runtime",
                "field": "BYTE_ORDER",
                "value": "ABCD",
                "reason": "Second spelling.",
                "evidence_refs": ["source-map"],
            },
            canonical_map=canonical,
        )
        with self.assertRaisesRegex(ReviewDecisionError, "duplicates"):
            apply_review_decisions(canonical, record)

    def test_rejects_set_and_exclude_for_the_same_point_in_any_order(self) -> None:
        canonical = draft_map()
        set_decision = {
            "point_id": "runtime",
            "field": "scale",
            "value": 1,
            "reason": "Set scale.",
            "evidence_refs": ["source-map"],
        }
        exclude_decision = {
            "point_id": "runtime",
            "action": "exclude",
            "reason": "Exclude point.",
        }
        for decisions in (
            (set_decision, exclude_decision),
            (exclude_decision, set_decision),
        ):
            with self.subTest(order=decisions[0].get("action", "set")):
                with self.assertRaisesRegex(ReviewDecisionError, "set and exclude"):
                    apply_review_decisions(
                        canonical,
                        decision_record(*decisions, canonical_map=canonical),
                    )

    def test_related_field_decisions_are_independent_of_array_order(self) -> None:
        canonical = draft_map()
        evidence = byte_order_evidence(canonical, datatype="int32")
        evidence_hash = stable_input_hash(evidence)
        byte_decision = {
            "point_id": "runtime",
            "field": "byte_order",
            "value": "ABCD",
            "reason": "The selected raw sample decodes as a signed value.",
            "evidence_refs": [f"sha256:{evidence_hash}"],
        }
        datatype_decision = {
            "point_id": "runtime",
            "field": "datatype",
            "value": "int32",
            "reason": "The source manual declares a signed value.",
            "evidence_refs": ["source-map"],
        }
        exclude_write_only = {
            "point_id": "reset",
            "action": "exclude",
            "reason": "The source declares this point as write-only.",
        }
        results = []
        for decisions in (
            (byte_decision, datatype_decision, exclude_write_only),
            (datatype_decision, byte_decision, exclude_write_only),
        ):
            results.append(
                apply_review_decisions(
                    canonical,
                    decision_record(*decisions, canonical_map=canonical),
                    evidence_artifacts={evidence_hash: evidence},
                )
            )
        for result in results:
            self.assertEqual("approved", result["review_status"])
            self.assertEqual("int32", result["points"][0]["datatype"])
            self.assertEqual("ABCD", result["points"][0]["byte_order"])
            self.assertEqual([], result["holds"])


if __name__ == "__main__":
    unittest.main()
