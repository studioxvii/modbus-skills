from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_activation_cases import build  # noqa: E402


class ActivationCaseTests(unittest.TestCase):
    def test_each_skill_has_required_cases(self) -> None:
        skill_root = ROOT / "plugins" / "modbus-skills" / "skills"
        skill_ids = {path.name for path in skill_root.iterdir() if path.is_dir()}
        activation = build()
        by_skill = {case["skill_id"]: case for case in activation["cases"]}
        self.assertEqual(skill_ids, set(by_skill))
        for skill_id, case in by_skill.items():
            with self.subTest(skill=skill_id):
                self.assertGreaterEqual(len(case["positive"]), 10)
                self.assertGreaterEqual(len(case["negative"]), 5)
                self.assertEqual(len(case["positive"]), len(set(case["positive"])))
                self.assertEqual(len(case["negative"]), len(set(case["negative"])))

    def test_positive_stems_are_distinct_requests_not_prefix_padding(self) -> None:
        intents = json.loads(
            (ROOT / "catalog" / "activation-intents.json").read_text(encoding="utf-8")
        )
        for skill_id, intent in intents.items():
            with self.subTest(skill=skill_id):
                stems = intent["positive_stems"]
                self.assertGreaterEqual(len(stems), 10)
                self.assertEqual(len(stems), len(set(stems)))

    def test_oem_outcome_is_distinct_from_specialist_intents(self) -> None:
        intents = json.loads(
            (ROOT / "catalog" / "activation-intents.json").read_text(encoding="utf-8")
        )
        outcome = intents["compile-user-map"]
        positives = " ".join(outcome["positive_stems"]).lower()
        negatives = " ".join(outcome["negative"]).lower()
        self.assertIn("oem", positives)
        self.assertIn("measurement intent", positives)
        for specialist_goal in ("only extract", "compare", "convert", "analyze", "only generate"):
            self.assertIn(specialist_goal, negatives)


if __name__ == "__main__":
    unittest.main()
