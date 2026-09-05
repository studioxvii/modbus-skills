from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_skill_routing_acceptance import routing_inputs  # noqa: E402
from skill_usability.contracts import ContractError, catalog_skill_ids, validate_campaign  # noqa: E402


class RoutingAcceptanceTests(unittest.TestCase):
    def test_declared_routes_cover_each_specialist_and_help_categories(self):
        declaration, campaign, scenarios = routing_inputs()
        self.assertEqual(42, len(scenarios))
        for category in ("explicit-stage-routing", "natural-language-routing"):
            self.assertEqual(catalog_skill_ids() - {"modbus-help"},
                             {row["expected_skill"] for row in declaration["cases"] if row["category"] == category})
        self.assertEqual({"positive", "negative", "incomplete", "unsafe"},
                         {row["category"] for row in declaration["cases"] if row["id"].startswith("help-")})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scenarios").mkdir()
            for scenario in scenarios:
                (root / "scenarios" / f"{scenario['scenario_id']}.json").write_text(json.dumps(scenario))
            self.assertEqual(42, len(validate_campaign(campaign, campaign_dir=root)["loaded_scenarios"]))
            invalid = copy.deepcopy(campaign)
            invalid["campaign_kind"] = "representative"
            with self.assertRaisesRegex(ContractError, "exactly 8"):
                validate_campaign(invalid, campaign_dir=root)
            invalid["campaign_kind"] = "extended"
            for names in ([], campaign["scenarios"] * 3):
                invalid["scenarios"] = names
                with self.assertRaisesRegex(ContractError, "1 through 100"):
                    validate_campaign(invalid, campaign_dir=root)


if __name__ == "__main__":
    unittest.main()
