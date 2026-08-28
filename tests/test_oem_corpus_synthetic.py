from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.parsers import parse_source  # noqa: E402


CORPUS = ROOT / "tests" / "fixtures" / "oem-corpus"
MANIFEST = CORPUS / "manifest.json"
SYNTHETIC = CORPUS / "synthetic"


class OemCorpusSyntheticTests(unittest.TestCase):
    def test_manifest_lists_synthetic_fixtures(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        synthetic = manifest.get("synthetic_fixtures", {})
        self.assertEqual("modbus-oem-synthetic/v1", synthetic.get("schema_version"))
        paths = {entry["path"] for entry in synthetic.get("files", [])}
        self.assertIn("synthetic/gotion-bess-integrator-messy.xlsx", paths)
        self.assertGreaterEqual(len(paths), 10)

    def test_parseable_messy_yields_records(self) -> None:
        parseable = SYNTHETIC / "parseable-messy"
        self.assertTrue(parseable.is_dir())
        for path in sorted(parseable.iterdir()):
            if path.suffix not in {".csv", ".tsv", ".xlsx"}:
                continue
            result = parse_source(path.read_bytes(), filename=path.name)
            with self.subTest(path=path.name):
                self.assertGreater(
                    len(result["records"]),
                    0,
                    msg=f"expected parseable records in {path.name}",
                )

    def test_gotion_integrator_xlsx_compiles_to_user_map(self) -> None:
        path = SYNTHETIC / "gotion-bess-integrator-messy.xlsx"
        parsed = parse_source(path.read_bytes(), filename=path.name)
        from modbus_skills.map_workflows import normalize_map  # noqa: E402

        canonical = normalize_map(parsed)
        blocking = [
            hold
            for hold in canonical.get("holds", ())
            if isinstance(hold, Mapping)
            and hold.get("blocking", True) is not False
            and str(hold.get("code", ""))
            not in {"point.route-id-unresolved", "point.unit-id-unresolved"}
        ]
        self.assertGreater(len(canonical["points"]), 0)
        self.assertEqual([], blocking)
        self.assertEqual(0, len(canonical.get("rejected_rows", ())))

    def test_intake_junk_is_not_naively_parseable(self) -> None:
        intake = SYNTHETIC / "intake-junk"
        self.assertTrue(intake.is_dir())
        for path in sorted(intake.glob("*.csv")):
            result = parse_source(path.read_bytes(), filename=path.name)
            with self.subTest(path=path.name):
                self.assertEqual(0, len(result["records"]))


if __name__ == "__main__":
    unittest.main()
