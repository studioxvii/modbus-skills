from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillCatalogTests(unittest.TestCase):
    def test_catalog_contains_every_skill(self) -> None:
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        actual = {path.name for path in (ROOT / "plugins" / "modbus-skills" / "skills").iterdir() if path.is_dir()}
        recorded = {skill["id"] for skill in catalog["skills"]}
        self.assertEqual(recorded, actual)

    def test_skill_validation(self) -> None:
        result = subprocess.run([sys.executable, "scripts/validate_skills.py"], cwd=ROOT, check=False)
        self.assertEqual(0, result.returncode)

    def test_every_skill_explains_its_output_files(self) -> None:
        skills_root = ROOT / "plugins" / "modbus-skills" / "skills"
        missing = [
            path.parent.name
            for path in sorted(skills_root.glob("*/SKILL.md"))
            if "\n## Output files\n" not in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], missing)

    def test_catalog_marks_every_skill_as_apache_licensed(self) -> None:
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"Apache-2.0"},
            {skill.get("license") for skill in catalog["skills"]},
        )


if __name__ == "__main__":
    unittest.main()
