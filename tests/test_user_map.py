from __future__ import annotations

import copy
import csv
import io
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler_contracts import build_oem_map
from modbus_skills.user_map import (
    UserMapError,
    apply_selection_override,
    compile_user_map_bundle,
    validate_selection_candidate,
)


def oem_map() -> dict[str, object]:
    return build_oem_map(
        [
            {
                "oem_point_id": "ambient-temperature",
                "name": "Ambient temperature",
                "area": "holding-register",
                "protocol_offset": 256,
                "datatype": "int16",
                "word_span": 1,
                "engineering_unit": "degC",
                "source_refs": [{"page_index": 2, "row_index": 4, "region_id": "r1"}],
            },
            {
                "oem_point_id": "operating-status",
                "name": "Operating status",
                "area": "holding-register",
                "protocol_offset": 300,
                "datatype": "uint16",
                "word_span": 1,
                "source_refs": [{"page_index": 3, "row_index": 1, "region_id": "r2"}],
            },
            {
                "oem_point_id": "internal-diagnostic",
                "name": "Internal diagnostic",
                "area": "holding-register",
                "protocol_offset": 400,
                "datatype": "uint16",
                "word_span": 1,
                "source_refs": [{"page_index": 4, "row_index": 8, "region_id": "r3"}],
            },
        ],
        source_hash="a" * 64,
        holds=[
            {
                "code": "DIAGNOSTIC_UNCERTAIN",
                "oem_point_id": "internal-diagnostic",
                "message": "Meaning is unclear",
                "evidence_refs": ["r3"],
            }
        ],
    )


def candidate(source: dict[str, object]) -> dict[str, object]:
    return {
        "oem_map_hash": stable_input_hash(source),
        "requested_measurements": ["temperature", "status"],
        "included": [
            {
                "oem_point_id": "ambient-temperature",
                "matched_intent": "temperature",
                "match_quality": "exact",
                "reason": "Exact temperature measurement",
                "evidence_refs": ["r1"],
                "group": "Temperature",
                "alias": "ambient_temp",
                "confidence": 0.99,
            }
        ],
        "suggested": [
            {
                "oem_point_id": "operating-status",
                "matched_intent": "status",
                "match_quality": "near",
                "reason": "Related status register",
                "evidence_refs": ["r2"],
                "confidence": 0.7,
            }
        ],
        "excluded": [
            {
                "oem_point_id": "internal-diagnostic",
                "reason": "Not requested",
                "evidence_refs": ["r3"],
            }
        ],
    }


class UserMapTests(unittest.TestCase):
    def test_bundle_is_deterministic_and_includes_each_point_once(self) -> None:
        source = oem_map()
        result = compile_user_map_bundle(source, candidate(source), case_id="case-001")

        self.assertEqual(result["status"], "offline-complete")
        self.assertEqual(result, compile_user_map_bundle(source, candidate(source), case_id="case-001"))
        self.assertEqual(
            [point["oem_point_id"] for point in result["user_map"]["points"]],
            ["ambient-temperature"],
        )
        self.assertEqual(result["human_summary"].count("`ambient-temperature`"), 1)
        rows = list(csv.DictReader(io.StringIO(result["csv"])))
        self.assertEqual([row["oem_point_id"] for row in rows], ["ambient-temperature"])
        self.assertEqual(result["json"].count('"oem_point_id": "ambient-temperature"'), 1)
        self.assertEqual(result["manifest"]["point_count"], 1)

    def test_near_or_ambiguous_match_cannot_be_auto_included(self) -> None:
        source = oem_map()
        raw = candidate(source)
        raw["included"].append(raw["suggested"].pop())

        selection = validate_selection_candidate(source, raw)

        self.assertEqual(
            [entry["oem_point_id"] for entry in selection["included"]],
            ["ambient-temperature"],
        )
        self.assertEqual(
            [entry["oem_point_id"] for entry in selection["suggested"]],
            ["operating-status"],
        )

    def test_typed_override_can_promote_or_exclude_suggestions(self) -> None:
        source = oem_map()
        selection = validate_selection_candidate(source, candidate(source))

        promoted = apply_selection_override(
            source,
            selection,
            [
                {
                    "oem_point_id": "operating-status",
                    "disposition": "included",
                    "reason": "User explicitly requested this status",
                    "evidence_refs": ["r2"],
                }
            ],
        )
        self.assertEqual(
            {entry["oem_point_id"] for entry in promoted["included"]},
            {"ambient-temperature", "operating-status"},
        )
        excluded = apply_selection_override(
            source,
            selection,
            [
                {
                    "oem_point_id": "operating-status",
                    "disposition": "excluded",
                    "reason": "User does not want status",
                    "evidence_refs": ["r2"],
                }
            ],
        )
        self.assertNotIn("operating-status", {x["oem_point_id"] for x in excluded["suggested"]})
        self.assertIn("operating-status", {x["oem_point_id"] for x in excluded["excluded"]})

    def test_zero_defensible_inclusions_yields_one_bounded_packet(self) -> None:
        source = oem_map()
        raw = candidate(source)
        raw["suggested"].extend(raw["included"])
        raw["included"] = []

        result = compile_user_map_bundle(source, raw, case_id="case-001")

        self.assertEqual(result["status"], "needs-selection-decision")
        self.assertEqual(result["user_map"], None)
        self.assertEqual(result["decision_packet"]["phase"], "selection")
        self.assertEqual(len(result["decision_packet"]["decisions"]), 1)
        self.assertEqual(
            set(result["decision_packet"]["decisions"][0]["subject_ids"]),
            {"ambient-temperature", "operating-status"},
        )

    def test_hash_unknown_id_and_unsupported_auto_inclusion_are_rejected(self) -> None:
        source = oem_map()
        stale = candidate(source)
        stale["oem_map_hash"] = "b" * 64
        with self.assertRaisesRegex(UserMapError, "OEM map hash"):
            validate_selection_candidate(source, stale)

        unknown = candidate(source)
        unknown["included"][0]["oem_point_id"] = "not-real"
        with self.assertRaisesRegex(UserMapError, "unknown OEM point"):
            validate_selection_candidate(source, unknown)

        unsupported = candidate(source)
        unsupported["included"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(UserMapError, "evidence"):
            validate_selection_candidate(source, unsupported)

    def test_unselected_hold_is_annex_only_and_portable_outputs_are_safe(self) -> None:
        source = oem_map()
        result = compile_user_map_bundle(source, candidate(source), case_id="case-001")

        self.assertEqual(result["user_map"]["holds"], [])
        self.assertIn("DIAGNOSTIC_UNCERTAIN", result["human_summary"])
        self.assertNotIn("DIAGNOSTIC_UNCERTAIN", result["csv"])
        combined = result["json"] + result["csv"] + result["human_summary"]
        self.assertNotIn("/Users/", combined)
        self.assertNotIn("password", combined.lower())


if __name__ == "__main__":
    unittest.main()
