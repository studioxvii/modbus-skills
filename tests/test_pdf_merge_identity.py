from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/modbus-skills/runtime"))
from modbus_skills.pdf_extraction import _identity, _reconcile  # noqa: E402


def point(raw: str, name: str, region: str, **fields):
    return {
        "source_register": raw,
        "source_address": {"raw": raw, "convention": "unknown"},
        "name": name,
        "access": "R",
        "_source": {"page": 1, "region": region},
        "_claims": [{"field": "source_register", "value": raw, "source_locator": {"page": 1, "region": region}}],
        **fields,
    }


class PdfMergeIdentityTests(unittest.TestCase):
    def test_source_address_and_source_register_are_identity_fields(self):
        row = point("7", "Sample", "p1:l2")
        self.assertEqual((1, "7", "sample"), _identity(row))
        row.pop("source_address")
        self.assertEqual((1, "7", "sample"), _identity(row))

    def test_same_raw_address_different_names_is_a_conflict_not_two_points(self):
        left = point("7", "sample.alpha Alarm condition", "p1:l2")
        right = point("7", "sample.alpha", "p1:t0:r1")
        accepted, quarantined, conflicts = _reconcile([left], [right])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual("7", conflicts[0]["identity"]["address"])
        self.assertEqual("name", conflicts[0]["fields"][0]["field"])
        self.assertEqual(2, len(quarantined[0]["_claims"]))

    def test_different_raw_addresses_same_name_never_silently_merge(self):
        left = point("7", "Sample", "p1:l2")
        right = point("8", "Sample", "p1:t0:r1")
        accepted, quarantined, conflicts = _reconcile([left], [right])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual([{"field": "address", "claims": ["7", "8"]}], conflicts[0]["fields"])
        self.assertEqual(["7", "8"], [claim["value"] for claim in quarantined[0]["_claims"]])

    def test_explicit_address_conflict_control_is_preserved(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "p1:l2", address="7")],
            [point("8", "Sample", "p1:t0:r1", address="8")],
        )
        self.assertEqual([], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual([{"field": "address", "claims": ["7", "8"]}], conflicts[0]["fields"])

    def test_exact_raw_address_agreement_merges_claims(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "p1:l2")],
            [point("7", "Sample", "p1:t0:r1")],
        )
        self.assertEqual(1, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)
        self.assertEqual(2, len(accepted[0]["_claims"]))

    def test_known_area_boundary_prevents_cross_association(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "p1:t0:r1", area="coil")],
            [point("7", "Sample", "p1:t0:r2", area="holding-register")],
        )
        self.assertEqual(2, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_same_physical_row_scope_disagreements_are_material_conflicts(self):
        for field, left_value, right_value in (
            ("area", "coil", "holding-register"),
            ("unit_id", 7, 8),
            ("route_id", "route-a", "route-b"),
        ):
            with self.subTest(field=field):
                left = point("7", "Sample", "p1:t0:r1", **{field: left_value})
                right = point("7", "Sample", "p1:t0:r1", **{field: right_value})
                accepted, quarantined, conflicts = _reconcile([left], [right])
                self.assertEqual([], accepted)
                self.assertEqual(1, len(quarantined))
                self.assertEqual(
                    [{"field": field, "claims": [left_value, right_value]}],
                    conflicts[0]["fields"],
                )
                self.assertEqual(2, len(quarantined[0]["_claims"]))

    def test_unique_physical_row_associates_before_all_semantic_labels(self):
        left = point("7", "First label", "p1:t0:r1", area="coil", unit_id=7, route_id="route-a")
        right = point("8", "Second label", "p1:t0:r1", area="holding-register", unit_id=8, route_id="route-b")
        accepted, quarantined, conflicts = _reconcile([left], [right])
        self.assertEqual([], accepted)
        self.assertEqual(1, len(quarantined))
        self.assertEqual(
            {"address", "name", "area", "unit_id", "route_id"},
            {item["field"] for item in conflicts[0]["fields"]},
        )
        self.assertEqual(2, len(quarantined[0]["_claims"]))

    def test_ambiguous_physical_locator_does_not_force_association(self):
        left = [point("7", "First label", "p1:t0:r1"), point("8", "Second label", "p1:t0:r1")]
        right = [point("9", "Third label", "p1:t0:r1")]
        accepted, quarantined, conflicts = _reconcile(left, right)
        self.assertEqual(3, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_duplicate_right_physical_locator_does_not_force_association(self):
        left = [point("7", "First label", "p1:t0:r1")]
        right = [point("8", "Second label", "p1:t0:r1"), point("9", "Third label", "p1:t0:r1")]
        accepted, quarantined, conflicts = _reconcile(left, right)
        self.assertEqual(3, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_distinct_physical_rows_with_equal_labels_remain_separate(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "p1:t0:r1", area="coil")],
            [point("7", "Sample", "p1:t0:r2", area="coil")],
        )
        self.assertEqual(2, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_distinct_rows_with_different_device_scopes_remain_separate(self):
        for field, left_value, right_value in (
            ("unit_id", 7, 8),
            ("route_id", "route-a", "route-b"),
        ):
            with self.subTest(field=field):
                accepted, quarantined, conflicts = _reconcile(
                    [point("7", "Sample", "p1:t0:r1", **{field: left_value})],
                    [point("7", "Sample", "p1:t0:r2", **{field: right_value})],
                )
                self.assertEqual(2, len(accepted))
                self.assertEqual([], quarantined)
                self.assertEqual([], conflicts)

    def test_unknown_physical_region_does_not_override_scope_boundaries(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "", area="coil")],
            [point("7", "Sample", "", area="holding-register")],
        )
        self.assertEqual(2, len(accepted))
        self.assertTrue(all(len(row["_claims"]) == 1 for row in accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_known_table_boundary_prevents_cross_association(self):
        accepted, quarantined, conflicts = _reconcile(
            [point("7", "Sample", "p1:t0:r1")],
            [point("7", "Sample", "p1:t1:r1")],
        )
        self.assertEqual(2, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)

    def test_different_physical_pages_do_not_merge(self):
        left = point("7", "Sample", "p1:l2")
        right = point("7", "Sample", "p2:l2")
        right["_source"]["page"] = 2
        accepted, quarantined, conflicts = _reconcile([left], [right])
        self.assertEqual(2, len(accepted))
        self.assertEqual([], quarantined)
        self.assertEqual([], conflicts)


if __name__ == "__main__":
    unittest.main()
