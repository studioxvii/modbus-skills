"""Prospective literal scalar identity controls; no engineering reinterpretation."""
import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))

from modbus_skills.user_map import build_literal_source_context


def entry(value, point):
    return {"field": "minimum", "literal": value, "source_field": "minimum",
            "oem_point_id": point, "source_ref": {"record_id": point}}


class SignedZeroContextTests(unittest.TestCase):
    def test_opposite_signed_float_zeros_keep_distinct_source_literals(self):
        entries = [entry(-0.0, "negative"), entry(0.0, "positive")]
        groups = build_literal_source_context(entries)
        self.assertEqual(len(groups), 2)
        by_literal = {json.dumps(g["literal"]): g for g in groups}
        self.assertEqual(set(by_literal), {"-0.0", "0.0"})
        for literal, point in (("-0.0", "negative"), ("0.0", "positive")):
            self.assertEqual([b["oem_point_id"] for b in by_literal[literal]["bindings"]], [point])
        self.assertNotEqual(groups[0]["context_id"], groups[1]["context_id"])
        self.assertEqual(json.dumps(groups, sort_keys=True),
                         json.dumps(build_literal_source_context(reversed(entries)), sort_keys=True))

    def test_identical_negative_zeros_still_share_one_payload(self):
        groups = build_literal_source_context([entry(-0.0, "a"), entry(-0.0, "b")])
        self.assertEqual(len(groups), 1)
        self.assertEqual(json.dumps(groups[0]["literal"]), "-0.0")
        self.assertEqual(len(groups[0]["bindings"]), 2)

    def test_integer_float_and_text_zeros_remain_distinct(self):
        groups = build_literal_source_context([entry(0, "integer"), entry(0.0, "float"), entry("0.0", "text")])
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({g["context_id"] for g in groups}), 3)


if __name__ == "__main__":
    unittest.main()
