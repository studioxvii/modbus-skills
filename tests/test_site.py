from __future__ import annotations

import json
import subprocess
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if tag == "a" and values.get("href"):
            self.hrefs.append(str(values["href"]))


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
                html_text = html_page.read_text(encoding="utf-8")
                markdown_text = markdown_page.read_text(encoding="utf-8")
                self.assertIn(skill["display_name"], html_text)
                self.assertIn("Use this when", html_text)
                self.assertIn("What you get back", html_text)
                self.assertEqual(1, html_text.count("Example request"))
                self.assertIn("View the skill source on GitHub", html_text)
                self.assertIn("## What you get back", markdown_text)
                self.assertIn("View the skill source on GitHub", markdown_text)
                self.assertNotIn("Common requests", html_text)
                self.assertNotIn(skill["default_prompt"], html_text)

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
        self.assertIn("Install in Codex", page)
        self.assertIn("Build for another client in the README", page)
        self.assertIn('aria-current="page"', page)

    def test_when_to_use_and_example_pages_exist(self) -> None:
        when_to_use = (ROOT / "site" / "when-to-use.html").read_text(encoding="utf-8")
        example = (ROOT / "site" / "examples" / "compile-user-map.html").read_text(encoding="utf-8")
        self.assertIn("This work", when_to_use)
        self.assertIn("Out of scope", when_to_use)
        self.assertNotIn("Other work", when_to_use)
        self.assertIn("write-only", example)
        self.assertIn("No blocking exception remains in this completed result", example)
        self.assertIn("What a pause looks like", example)
        self.assertIn("Flow Rate and Energy Total have no byte order", example)
        self.assertIn("40001 becomes holding-register offset 0", example)
        self.assertIn("40003 becomes holding-register offset 2", example)
        self.assertIn("30001 becomes input-register offset 0", example)
        self.assertNotIn("awaiting-source-decision", example)

    def test_tables_scroll_inside_the_page_on_small_screens(self) -> None:
        home = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        example = (ROOT / "site" / "examples" / "compile-user-map.html").read_text(encoding="utf-8")
        self.assertIn(".table-scroll", home)
        self.assertIn("overflow-x: auto", home)
        self.assertIn('aria-label="Skill catalog"', home)
        self.assertIn('aria-label="Finished user map"', example)

    def test_skill_pages_use_one_real_request_and_human_source_link(self) -> None:
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        activation = json.loads((ROOT / "catalog" / "activation-cases.json").read_text(encoding="utf-8"))
        first_prompts = {case["skill_id"]: case["positive"][0] for case in activation["cases"]}
        for skill in catalog["skills"]:
            with self.subTest(skill=skill["id"]):
                page = (ROOT / "site" / "skills" / f'{skill["id"]}.html').read_text(encoding="utf-8")
                self.assertIn(first_prompts[skill["id"]], page)
                self.assertNotIn("Source: <code", page)
                self.assertNotIn("Common requests", page)
        help_page = (ROOT / "site" / "skills" / "modbus-help.html").read_text(encoding="utf-8")
        self.assertIn("Help me choose the right Modbus workflow.", help_page)
        self.assertNotIn("Help me help me", help_page)

    def test_problem_pages_use_human_titles_labels_and_skill_names(self) -> None:
        page = (ROOT / "site" / "problems" / "protocol-offset-vs-reference-number.html").read_text(encoding="utf-8")
        self.assertIn("<h1>Address offset vs. reference number</h1>", page)
        self.assertIn("Modbus application protocol", page)
        self.assertIn("Pymodbus issue", page)
        self.assertIn(">Normalize Map</a>", page)
        self.assertNotIn("official-specification", page)
        self.assertNotIn(">normalize-map</a>", page)

    def test_workflow_pages_lead_with_human_content(self) -> None:
        page = (ROOT / "site" / "workflows" / "compile-user-map.html").read_text(encoding="utf-8")
        self.assertIn("Workflow · 1 step", page)
        self.assertIn("You start with", page)
        self.assertIn("You receive", page)
        self.assertIn("How it works", page)
        self.assertIn(">Compile User Map</a>", page)
        details_at = page.index("Show machine-readable contracts")
        schema_at = page.index("modbus-compile-request/v1")
        self.assertLess(details_at, schema_at)

    def test_navigation_marks_the_current_page(self) -> None:
        checks = {
            ROOT / "site" / "when-to-use.html": "When to use",
            ROOT / "site" / "skills" / "check-map.html": "Skills",
            ROOT / "site" / "workflows" / "build-tool-pack.html": "Workflows",
            ROOT / "site" / "problems" / "word-and-byte-order.html": "Problems",
        }
        for path, label in checks.items():
            with self.subTest(path=path):
                page = path.read_text(encoding="utf-8")
                self.assertIn(f'class="is-current" aria-current="page"', page)
                self.assertIn(f'>{label}</a>', page)

    def test_custom_404_exists_and_is_not_indexed(self) -> None:
        page = (ROOT / "site" / "404.html").read_text(encoding="utf-8")
        sitemap = (ROOT / "site" / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("Page not found", page)
        self.assertIn('name="robots" content="noindex"', page)
        self.assertIn("https://studioxvii.github.io/modbus-skills/", page)
        self.assertNotIn("404.html", sitemap)

    def test_internal_page_links_resolve(self) -> None:
        site = ROOT / "site"
        parsed_pages: dict[Path, _PageParser] = {}
        for page_path in site.rglob("*.html"):
            parser = _PageParser()
            parser.feed(page_path.read_text(encoding="utf-8"))
            parsed_pages[page_path.resolve()] = parser

        for page_path, parser in parsed_pages.items():
            for href in parser.hrefs:
                parts = urlsplit(href)
                if parts.scheme or parts.netloc:
                    continue
                target = (page_path.parent / unquote(parts.path or page_path.name)).resolve()
                with self.subTest(page=page_path.relative_to(site), href=href):
                    self.assertTrue(target.exists(), f"Missing link target: {target}")
                    if parts.fragment and target.suffix == ".html":
                        self.assertIn(parts.fragment, parsed_pages[target].ids)

    def test_llms_txt_leads_with_jobs(self) -> None:
        text = (ROOT / "site" / "llms.txt").read_text(encoding="utf-8")
        self.assertLess(text.index("When to use"), text.index("## Skills"))
        self.assertLess(text.index("Compile User Map"), text.index("## Skills"))
        self.assertIn("Register writes, network discovery, unbounded polling, and invented byte orders", text)
        self.assertIn("are out of scope", text)


if __name__ == "__main__":
    unittest.main()
