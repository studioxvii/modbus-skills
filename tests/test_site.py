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

    def test_homepage_leads_with_one_job(self) -> None:
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Turn a vendor Modbus manual into a usable map", page)
        self.assertIn("First 10 minutes", page)
        self.assertIn("plain language", page)
        self.assertNotIn("$compile-user-map", page)
        self.assertIn("examples/compile-user-map.html", page)

    def test_when_to_use_and_example_pages_exist(self) -> None:
        when_to_use = (ROOT / "site" / "when-to-use.html").read_text(encoding="utf-8")
        example = (ROOT / "site" / "examples" / "compile-user-map.html").read_text(encoding="utf-8")
        self.assertIn("This work", when_to_use)
        self.assertIn("Other work", when_to_use)
        self.assertIn("write-only", example)
        self.assertIn("pauses the job so you can confirm ABCD, CDAB, or another layout", example)
        self.assertNotIn("awaiting-source-decision", example)

    def test_llms_txt_leads_with_jobs(self) -> None:
        text = (ROOT / "site" / "llms.txt").read_text(encoding="utf-8")
        self.assertLess(text.index("When to use"), text.index("## Skills"))
        self.assertLess(text.index("Compile User Map"), text.index("## Skills"))
        self.assertIn("Register writes, network discovery, unbounded polling, and invented byte orders", text)


if __name__ == "__main__":
    unittest.main()
