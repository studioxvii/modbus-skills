from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ActivationCaseTests(unittest.TestCase):
    def test_each_skill_has_required_cases(self) -> None:
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        activation = json.loads((ROOT / "catalog" / "activation-cases.json").read_text(encoding="utf-8"))
        by_skill = {case["skill_id"]: case for case in activation["cases"]}
        self.assertEqual({skill["id"] for skill in catalog["skills"]}, set(by_skill))
        for skill_id, case in by_skill.items():
            with self.subTest(skill=skill_id):
                self.assertGreaterEqual(len(case["positive"]), 10)
                self.assertGreaterEqual(len(case["negative"]), 5)
                self.assertEqual(len(case["positive"]), len(set(case["positive"])))
                self.assertEqual(len(case["negative"]), len(set(case["negative"])))


if __name__ == "__main__":
    unittest.main()
