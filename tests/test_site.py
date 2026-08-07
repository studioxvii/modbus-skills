from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SiteTests(unittest.TestCase):
    def test_generated_site_is_current(self) -> None:
        result = subprocess.run([sys.executable, "scripts/build_site.py", "--check"], cwd=ROOT, check=False)
        self.assertEqual(0, result.returncode)

    def test_every_skill_has_html_and_markdown_pages(self) -> None:
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        for skill in catalog["skills"]:
            with self.subTest(skill=skill["id"]):
                html_page = ROOT / "site" / "skills" / f'{skill["id"]}.html'
                markdown_page = ROOT / "site" / "skills" / f'{skill["id"]}.md'
                self.assertTrue(html_page.exists())
                self.assertTrue(markdown_page.exists())
                self.assertIn(skill["display_name"], html_page.read_text(encoding="utf-8"))
                self.assertIn(skill["description"], markdown_page.read_text(encoding="utf-8"))

    def test_agent_discovery_files_exist(self) -> None:
        for name in ("llms.txt", "llms-full.txt", "skills.json", "workflows.json", "research.json", "sitemap.xml", "robots.txt"):
            self.assertTrue((ROOT / "site" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
