import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_skills import missing_skill_references


class SkillReferenceTests(unittest.TestCase):
    def test_routed_backtick_and_markdown_references_must_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary)
            (skill / "references").mkdir()
            (skill / "references/present.md").write_text("Confirmed source")
            text = "Read `references/present.md`, `references/missing.md`, and [guide](references/also-missing.md)."
            self.assertEqual(["references/also-missing.md", "references/missing.md"], missing_skill_references(skill, text))

    def test_example_outputs_and_external_urls_are_not_routed_local_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual([], missing_skill_references(Path(temporary),
                "Create `output/user-map.md`; see [API](https://example.test/references/api.md)."))

    def test_all_shipped_skill_references_resolve(self):
        for path in (ROOT / "plugins/modbus-skills/skills").glob("*/SKILL.md"):
            self.assertEqual([], missing_skill_references(path.parent, path.read_text()), str(path))
