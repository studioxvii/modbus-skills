from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/modbus-skills/runtime"))
from modbus_skills.cli import _append_plan_source_holds


def previous_merge(findings, holds):
    """Frozen prior merge for differential compatibility, not a source oracle."""
    def key(item):
        ids = item.get("point_ids", ())
        return item.get("code"), item.get("field"), tuple(str(x) for x in ids) if isinstance(ids, Sequence) and not isinstance(ids, (str, bytes, bytearray)) else ()
    for hold in holds:
        if isinstance(hold, Mapping) and hold.get("blocking", True) is not False:
            candidate = dict(hold)
            keys = {key(item) for item in findings if isinstance(item, Mapping)}
            if key(candidate) not in keys:
                findings.append(candidate)


class PlanHoldMergeTests(unittest.TestCase):
    def test_preserves_first_disposition_order_and_every_distinct_point_hold(self):
        first = {"code": "unresolved", "field": "scale", "point_ids": ["a"], "reason": "original"}
        holds = [{**first, "reason": "duplicate"}, {**first, "point_ids": ["b"]},
                 {"code": "global", "reason": "global source uncertainty"},
                 {"code": "not-blocking", "blocking": False}]
        before = copy.deepcopy(holds)
        output = [first]
        _append_plan_source_holds(output, holds)
        self.assertEqual([first, holds[1], holds[2]], output)
        self.assertEqual(before, holds)
        self.assertIsNot(output[1], holds[1])

    def test_malformed_nonmapping_and_point_id_forms_match_prior_behavior(self):
        holds = [None, False, "not a hold", {"code": "a", "point_ids": "abc"},
                 {"code": "a", "point_ids": []}, {"code": "b", "point_ids": [1, None]},
                 {"code": "b", "point_ids": ["1", "None"]}, {"code": "c", "blocking": 0}]
        old, new = ["existing legacy entry"], ["existing legacy entry"]
        previous_merge(old, holds)
        _append_plan_source_holds(new, holds)
        self.assertEqual(old, new)

    def test_no_blocking_holds_does_not_eagerly_hash_unusual_existing_values(self):
        output = [{"code": []}]
        _append_plan_source_holds(output, [None, {"blocking": False}])
        self.assertEqual([{"code": []}], output)

    def test_seeded_differential_cases_preserve_all_fields_and_inputs(self):
        rng = random.Random(20260905)
        for case in range(250):
            holds = [{"code": str(rng.randrange(5)), "field": rng.choice([None, "area", "datatype"]),
                      "point_ids": rng.choice([[], ["a"], ["b"], ["a", "b"], "a"]),
                      "blocking": rng.choice([True, False, None]), "reason": f"case-{case}-hold-{index}",
                      "details": {"source_row": index}} for index in range(rng.randrange(60))]
            initial = copy.deepcopy(holds[:rng.randrange(5)])
            old, new = copy.deepcopy(initial), copy.deepcopy(initial)
            before = copy.deepcopy(holds)
            previous_merge(old, holds)
            _append_plan_source_holds(new, holds)
            self.assertEqual(old, new)
            self.assertEqual(before, holds)


if __name__ == "__main__":
    unittest.main()
