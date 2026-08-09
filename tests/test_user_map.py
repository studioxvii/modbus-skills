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

    def test_repeated_selected_holds_are_grouped_by_root_cause(self) -> None:
        source = build_oem_map(
            [
                {
                    "oem_point_id": point_id,
                    "name": point_id,
                    "area": None,
                    "protocol_offset": None,
                    "datatype": "uint16",
                    "word_span": 1,
                    "source_refs": [{"record_id": f"row:{index}"}],
                }
                for index, point_id in enumerate(("one", "two"), start=1)
            ],
            source_hash="b" * 64,
            holds=[
                {
                    "code": "point.area-unresolved",
                    "message": "Declare the Modbus area.",
                    "point_ids": [point_id],
                }
                for point_id in ("one", "two")
            ],
        )
        selection = {
            "oem_map_hash": stable_input_hash(source),
            "requested_measurements": ["all"],
            "included": [
                {
                    "oem_point_id": point_id,
                    "matched_intent": "all",
                    "match_quality": "exact",
                    "reason": "Explicit all selection",
                    "evidence_refs": [f"row:{index}"],
                }
                for index, point_id in enumerate(("one", "two"), start=1)
            ],
            "suggested": [],
            "excluded": [],
        }

        result = compile_user_map_bundle(source, selection, case_id="case-grouped")

        self.assertEqual(1, len(result["user_map"]["holds"]))
        self.assertEqual(2, result["user_map"]["holds"][0]["affected_count"])
        self.assertEqual(["one", "two"], result["user_map"]["holds"][0]["subject_ids"])
        self.assertEqual(
            1,
            result["human_summary"].count(
                "Declare the Modbus area before address conversion."
            ),
        )

    def test_point_ids_hold_for_excluded_point_is_annex_only(self) -> None:
        source = build_oem_map(
            [
                {
                    "oem_point_id": point_id,
                    "name": point_id,
                    "area": "holding-register",
                    "protocol_offset": index,
                    "datatype": "uint16",
                    "word_span": 1,
                    "source_refs": [{"record_id": f"row:{index}"}],
                }
                for index, point_id in enumerate(("one", "two"), start=1)
            ],
            source_hash="c" * 64,
            holds=[
                {
                    "code": "point.example-unresolved",
                    "message": "Resolve excluded point evidence.",
                    "point_ids": ["two"],
                }
            ],
        )
        selection = {
            "oem_map_hash": stable_input_hash(source),
            "requested_measurements": ["one"],
            "included": [
                {
                    "oem_point_id": "one",
                    "matched_intent": "one",
                    "match_quality": "exact",
                    "reason": "Explicit selection",
                    "evidence_refs": ["row:1"],
                }
            ],
            "suggested": [],
            "excluded": [
                {
                    "oem_point_id": "two",
                    "reason": "Write-only",
                    "evidence_refs": ["row:2"],
                }
            ],
        }

        result = compile_user_map_bundle(source, selection, case_id="case-annex")

        self.assertEqual([], result["user_map"]["holds"])
        self.assertEqual(
            ["point.example-unresolved"],
            [
                item["code"]
                for item in result["user_map"]["exception_annex"]
                if item.get("kind") == "unselected-hold"
            ],
        )


if __name__ == "__main__":
    unittest.main()
