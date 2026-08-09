from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.decision_packets import (
    DecisionPacketError,
    build_compiler_decision_packet,
    decision_reply_fingerprint,
    validate_decision_candidate,
)


def packet() -> dict[str, object]:
    return build_compiler_decision_packet(
        case_id="case-001",
        phase="source",
        source_hash="a" * 64,
        input_hashes={"oem_map": "b" * 64},
        decisions=[
            {
                "decision_id": "source.region-2.address",
                "subject_ids": ["region-2"],
                "prompt": "Choose the supported address claim for region 2.",
                "permitted_dispositions": ["accept-primary", "accept-secondary", "exclude"],
                "evidence_refs": ["page-4-region-2", "page-4-layout-2"],
            }
        ],
    )


def candidate(source_packet: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "modbus-compiler-decision-candidate/v1",
        "case_id": source_packet["case_id"],
        "phase": source_packet["phase"],
        "packet_id": source_packet["packet_id"],
        "source_hash": source_packet["source_hash"],
        "input_hashes": copy.deepcopy(source_packet["input_hashes"]),
        "decisions": [
            {
                "decision_id": "source.region-2.address",
                "disposition": "accept-primary",
                "reason": "The primary claim matches the labeled row.",
                "evidence_refs": ["page-4-region-2"],
            }
        ],
    }


class DecisionPacketTests(unittest.TestCase):
    def test_typed_reply_is_bound_and_exact_replay_has_same_fingerprint(self) -> None:
        source_packet = packet()
        reply = candidate(source_packet)

        normalized = validate_decision_candidate(source_packet, reply)
        first = decision_reply_fingerprint(source_packet, reply)
        second = decision_reply_fingerprint(source_packet, copy.deepcopy(reply))

        self.assertEqual(normalized["decisions"][0]["decision_id"], "source.region-2.address")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_packet_is_distinct_from_legacy_map_review(self) -> None:
        source_packet = packet()
        self.assertEqual(source_packet["schema_version"], "modbus-compiler-decision-packet/v1")
        self.assertNotEqual(source_packet["schema_version"], "modbus-review-decisions/v1")
        self.assertEqual(source_packet["input_hashes"]["source"], "a" * 64)

    def test_stale_or_cross_case_reply_is_rejected(self) -> None:
        source_packet = packet()
        for field, value in (
            ("case_id", "case-999"),
            ("phase", "selection"),
            ("packet_id", "0" * 64),
            ("source_hash", "c" * 64),
        ):
            with self.subTest(field=field):
                reply = candidate(source_packet)
                reply[field] = value
                with self.assertRaises(DecisionPacketError):
                    validate_decision_candidate(source_packet, reply)

        stale = candidate(source_packet)
        stale["input_hashes"]["oem_map"] = "c" * 64
        with self.assertRaisesRegex(DecisionPacketError, "stale input hashes"):
            validate_decision_candidate(source_packet, stale)

    def test_unknown_broadened_or_missing_evidence_is_rejected(self) -> None:
        source_packet = packet()
        unknown = candidate(source_packet)
        unknown["decisions"][0]["decision_id"] = "source.other"
        with self.assertRaisesRegex(DecisionPacketError, "unknown decision"):
            validate_decision_candidate(source_packet, unknown)

        broadened = candidate(source_packet)
        broadened["decisions"][0]["disposition"] = "rewrite-anything"
        with self.assertRaisesRegex(DecisionPacketError, "permitted"):
            validate_decision_candidate(source_packet, broadened)

        missing_evidence = candidate(source_packet)
        missing_evidence["decisions"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(DecisionPacketError, "evidence"):
            validate_decision_candidate(source_packet, missing_evidence)

        invented_evidence = candidate(source_packet)
        invented_evidence["decisions"][0]["evidence_refs"] = ["not-in-packet"]
        with self.assertRaisesRegex(DecisionPacketError, "packet evidence"):
            validate_decision_candidate(source_packet, invented_evidence)

    def test_unparseable_prose_and_unknown_fields_have_no_authority(self) -> None:
        source_packet = packet()
        for reply in ("use the first one", ["accept-primary"], None):
            with self.subTest(reply=reply), self.assertRaisesRegex(
                DecisionPacketError, "typed object"
            ):
                validate_decision_candidate(source_packet, reply)

        broadened = candidate(source_packet)
        broadened["also_apply_to"] = ["every other region"]
        with self.assertRaisesRegex(DecisionPacketError, "unknown fields"):
            validate_decision_candidate(source_packet, broadened)


if __name__ == "__main__":
    unittest.main()
