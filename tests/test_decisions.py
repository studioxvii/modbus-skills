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
