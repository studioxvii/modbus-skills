from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_frozen_skill_campaign import freeze, hashes


class FrozenEvaluatorTests(unittest.TestCase):
    def test_source_edits_cannot_change_frozen_evaluator_or_goldens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "copy"
            names = ("scripts/run.py", "scripts/skill_usability/oracles.py", "plugins/modbus-skills/SKILL.md",
                     "tests/skill_usability/scenarios/case.json", "catalog/skills.json", "catalog/workflows.json")
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            expected = freeze(source, destination)
            (source / names[1]).write_text("later changed oracle")
            self.assertEqual(expected, hashes(destination))
            self.assertNotEqual(expected, hashes(source))
            with self.assertRaisesRegex(ValueError, "must not exist"):
                freeze(source, destination)

    def test_symbolic_links_are_not_silently_resolved_into_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts/escape.py").symlink_to("/nonexistent-synthetic")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                freeze(root, root / "snapshot")


if __name__ == "__main__": unittest.main()
