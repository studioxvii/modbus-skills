from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.comparison import compare_maps, composite_identity  # noqa: E402


def point(identifier: str, *, area: str = "holding-register", offset: int = 0, **values: object) -> dict:
    return {
        "logical_point_id": identifier,
        "route_id": "lab",
        "unit_id": 1,
        "area": area,
        "protocol_offset": offset,
        "datatype": "uint16",
        "name": identifier,
        **values,
    }


class MapComparisonTests(unittest.TestCase):
    def test_contract_matches_public_workflow_schema(self) -> None:
        result = compare_maps([], [])
        self.assertEqual("modbus-map-diff/v1", result["contract"])

    def test_composite_identity_keeps_areas_separate(self) -> None:
        holding = point("status", area="holding-register")
        input_register = point("status", area="input-register")
        self.assertNotEqual(composite_identity(holding), composite_identity(input_register))

    def test_reports_added_removed_and_field_level_changes(self) -> None:
        before = [point("kept", name="Old"), point("removed", offset=2)]
        after = [point("kept", name="New"), point("added", offset=3)]
        result = compare_maps(before, after)
        self.assertEqual(
            {"added": 1, "removed": 1, "changed": 1, "unchanged": 0, "ambiguous": 0},
            result["summary"],
        )
        self.assertEqual(
            [{"field": "name", "before": "Old", "after": "New"}],
            result["changed"][0]["changes"],
        )

    def test_logical_id_change_is_add_and_remove_not_rename_guess(self) -> None:
        result = compare_maps([point("old")], [point("new")])
        self.assertEqual(1, result["summary"]["added"])
        self.assertEqual(1, result["summary"]["removed"])
        self.assertEqual(0, result["summary"]["changed"])

    def test_duplicate_identity_is_ambiguous_and_not_collapsed(self) -> None:
        duplicate = point("same")
        result = compare_maps([duplicate, dict(duplicate)], [duplicate])
        self.assertEqual(1, result["summary"]["ambiguous"])
        self.assertEqual(2, result["duplicates"][0]["before_count"])
        self.assertEqual([], result["changed"])

    def test_canonical_span_and_engineering_offset_are_compared(self) -> None:
        before = point("same", word_span=1, engineering_offset=0)
        after = point("same", word_span=2, engineering_offset=10)

        changes = compare_maps([before], [after])["changed"][0]["changes"]

        self.assertEqual(
            [
                {"field": "word_span", "before": 1, "after": 2},
                {"field": "engineering_offset", "before": 0, "after": 10},
            ],
            changes,
        )


if __name__ == "__main__":
    unittest.main()
