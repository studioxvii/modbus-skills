from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class ResearchRecordTests(unittest.TestCase):
    def test_records_use_primary_https_sources_and_known_skills(self) -> None:
        research = json.loads((ROOT / "research" / "issues.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / "catalog" / "skills.json").read_text(encoding="utf-8"))
        skills = {skill["id"] for skill in catalog["skills"]}
        allowed_types = {"official-specification", "official-tool-documentation", "official-tool-example", "project-documentation", "project-issue", "project-discussion"}
        self.assertGreaterEqual(len(research["records"]), 10)
        for record in research["records"]:
            with self.subTest(record=record["id"]):
                self.assertTrue(record["title"])
                self.assertLessEqual(len(record["title"].split()), 7)
                self.assertTrue(record["problem"])
                self.assertTrue(record["evidence"])
                self.assertTrue(set(record["skills"]).issubset(skills))
                for source in record["sources"]:
                    self.assertTrue(source["label"])
                    self.assertIn(source["type"], allowed_types)
                    parsed = urlparse(source["url"])
                    self.assertEqual("https", parsed.scheme)
                    self.assertTrue(parsed.netloc)


if __name__ == "__main__":
    unittest.main()
