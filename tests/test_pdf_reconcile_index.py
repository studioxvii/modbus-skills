from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/modbus-skills/runtime"))
from modbus_skills import pdf_extraction as pdf  # noqa: E402


def point(address, name, region, **fields):
    return {
        "source_address": {"raw": address, "convention": "unknown"},
        "name": name,
        "_source": {"page": 1, "region": region},
        "_claims": [{"field": "name", "value": name, "region": region}],
        **fields,
    }


class PdfReconcileIndexTests(unittest.TestCase):
    def test_identity_and_scope_are_computed_once_per_input_row(self):
        left = [point(str(i), f"Point {i}", f"p1:l{i}") for i in range(80)]
        right = [point(str(i), f"Point {i}", f"p1:t0:r{i}") for i in range(80)]
        original = deepcopy((left, right))
        with patch.object(pdf, "_identity", wraps=pdf._identity) as identity, patch.object(
            pdf, "_merge_scope", wraps=pdf._merge_scope
        ) as scope:
            accepted, quarantined, conflicts = pdf._reconcile(left, right)
        self.assertEqual(160, identity.call_count)
        self.assertEqual(160, scope.call_count)
        self.assertEqual(original, (left, right))
        self.assertEqual(80, len(accepted))
        self.assertTrue(all(len(row["_claims"]) == 2 for row in accepted))
        self.assertEqual(([], []), (quarantined, conflicts))

    def test_scope_filtering_does_not_scan_unrelated_keys(self):
        left = [point(str(i), f"Point {i}", f"p1:l{i}") for i in range(80)]
        right = [point(str(i), f"Point {i}", f"p1:t0:r{i}") for i in reversed(range(80))]
        with patch.object(pdf, "_merge_scopes_compatible", wraps=pdf._merge_scopes_compatible) as scope:
            accepted, quarantined, conflicts = pdf._reconcile(left, right)
        self.assertEqual(80, scope.call_count)
        self.assertEqual([f"Point {i}" for i in range(80)], [row["name"] for row in accepted])
        self.assertEqual(([], []), (quarantined, conflicts))

    def test_consumed_exact_candidate_is_not_reused_by_duplicate_left(self):
        first = point("0", "Zero", "p1:l1")
        second = point("0", "Zero", "p1:l2")
        right = point("0", "Zero", "p1:t0:r1")
        accepted, quarantined, conflicts = pdf._reconcile([first, second], [right])
        expected_first = {**first, "_claims": first["_claims"] + right["_claims"]}
        self.assertEqual([expected_first, second], accepted)
        self.assertEqual(([], []), (quarantined, conflicts))

    def test_ambiguous_exact_bucket_preserves_all_rows_and_order(self):
        left = [point("7", "Same", "p1:l1")]
        right = [point("7", "Same", "p1:t0:r1"), point("7", "Same", "p1:t0:r2")]
        self.assertEqual((left + right, [], []), pdf._reconcile(left, right))

    def test_all_left_rows_still_count_for_address_fallback_uniqueness(self):
        left = [point("7", "First", "p1:l1"), point("7", "Second", "p1:l2")]
        right = [point("7", "First", "p1:t0:r1"), point("7", "Other", "p1:t0:r2")]
        accepted, quarantined, conflicts = pdf._reconcile(left, right)
        self.assertEqual(["First", "Second", "Other"], [row["name"] for row in accepted])
        self.assertEqual([2, 1, 1], [len(row["_claims"]) for row in accepted])
        self.assertEqual(([], []), (quarantined, conflicts))

    def test_scope_normalization_and_unknown_values_retain_compatibility(self):
        for fields in (
            ({"area": " Holding_Register "}, {"area": "holding-register"}),
            ({"unit_id": 7}, {"unit_id": "7"}),
            ({"route_id": "ROUTE_A"}, {"route_id": "route-a"}),
            ({"unit_id": None}, {"unit_id": 7}),
        ):
            with self.subTest(fields=fields):
                accepted, quarantined, conflicts = pdf._reconcile(
                    [point("7", "Same", "p1:l1", **fields[0])],
                    [point("7", "Same", "p1:t0:r1", **fields[1])],
                )
                self.assertEqual(1, len(accepted))
                self.assertEqual(([], []), (quarantined, conflicts))

    def test_physical_candidate_precedes_conflicting_semantic_candidate(self):
        left = point("7", "First", "p1:t0:r1", area="coil")
        physical = point("8", "Other", "p1:t0:r1", area="holding-register")
        semantic = point("7", "First", "p1:l1", area="coil")
        accepted, quarantined, conflicts = pdf._reconcile([left], [semantic, physical])
        self.assertEqual([semantic], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual({"address", "name", "area"}, {item["field"] for item in conflicts[0]["fields"]})

    def test_duplicate_physical_scope_is_not_made_unique_by_consumption(self):
        left = [point("7", "First", "p1:t0:r1"), point("8", "Second", "p1:t0:r1")]
        right = [point("7", "First", "p1:t0:r1"), point("9", "Other", "p1:t0:r1")]
        accepted, quarantined, conflicts = pdf._reconcile(left, right)
        self.assertEqual(["First", "Second", "Other"], [row["name"] for row in accepted])
        self.assertEqual(([], []), (quarantined, conflicts))

    def test_empty_sides_preserve_rows(self):
        rows = [point(None, "Unnamed", "p1:l1")]
        self.assertEqual(([], [], []), pdf._reconcile([], []))
        self.assertEqual((rows, [], []), pdf._reconcile(rows, []))
        self.assertEqual((rows, [], []), pdf._reconcile([], rows))


if __name__ == "__main__":
    unittest.main()
