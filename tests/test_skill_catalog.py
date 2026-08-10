from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_skills import validate


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

    def test_license_validation_rejects_missing_and_inconsistent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_root = Path(temp_dir) / "repo"
            shutil.copytree(
                ROOT,
                fixture_root,
                ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"),
            )

            root_license = fixture_root / "LICENSE"
            license_text = root_license.read_text(encoding="utf-8")
            root_license.unlink()
            self.assertIn("missing LICENSE", validate(fixture_root))
            root_license.write_text(license_text, encoding="utf-8")

            plugin_license = fixture_root / "plugins" / "modbus-skills" / "LICENSE"
            plugin_license.write_text("not the repository license\n", encoding="utf-8")
            self.assertIn(
                "plugin LICENSE must match the repository LICENSE",
                validate(fixture_root),
            )
            plugin_license.write_text(license_text, encoding="utf-8")

            root_license.write_text("not Apache-2.0\n", encoding="utf-8")
            plugin_license.write_text("not Apache-2.0\n", encoding="utf-8")
            self.assertIn(
                "LICENSE must contain the Apache License 2.0 text",
                validate(fixture_root),
            )
            root_license.write_text(license_text, encoding="utf-8")
            plugin_license.write_text(license_text, encoding="utf-8")

            root_notice = fixture_root / "NOTICE"
            plugin_notice = fixture_root / "plugins" / "modbus-skills" / "NOTICE"
            notice_text = root_notice.read_text(encoding="utf-8")
            root_notice.write_text("Modbus Skills\n", encoding="utf-8")
            plugin_notice.write_text("Modbus Skills\n", encoding="utf-8")
            self.assertIn(
                "NOTICE must contain Copyright 2026 Studio Seventeen",
                validate(fixture_root),
            )
            root_notice.write_text(notice_text, encoding="utf-8")
            plugin_notice.write_text(notice_text, encoding="utf-8")

            manifest_path = (
                fixture_root
                / "plugins"
                / "modbus-skills"
                / ".codex-plugin"
                / "plugin.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["license"] = "MIT"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIn("plugin license must be Apache-2.0", validate(fixture_root))
            manifest["license"] = "Apache-2.0"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            pyproject_path = fixture_root / "pyproject.toml"
            pyproject_text = pyproject_path.read_text(encoding="utf-8")
            pyproject_path.write_text(
                pyproject_text.replace(
                    'license = "Apache-2.0"',
                    'license = "MIT"',
                    1,
                ),
                encoding="utf-8",
            )
            self.assertIn(
                "Python project license must be Apache-2.0",
                validate(fixture_root),
            )

            skill_path = (
                fixture_root
                / "plugins"
                / "modbus-skills"
                / "skills"
                / "analyze-capture"
                / "SKILL.md"
            )
            skill_text = skill_path.read_text(encoding="utf-8")
            skill_path.write_text(
                skill_text.replace("license: Apache-2.0", "license: MIT", 1),
                encoding="utf-8",
            )
            self.assertIn(
                "analyze-capture: license must be Apache-2.0",
                validate(fixture_root),
            )


if __name__ == "__main__":
    unittest.main()
