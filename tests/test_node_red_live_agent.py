from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.run_node_red_live_campaign import load_campaign_contract, run_campaign


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "node_red_live" / "fixtures" / "campaign.json"
SCHEMA = ROOT / "tests" / "node_red_live" / "expected-report.schema.json"


def _assert_report_shape(test: unittest.TestCase, report: dict[str, object]) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for field in schema["required"]:
        test.assertIn(field, report)
    test.assertEqual(schema["properties"]["schema_version"]["const"], report["schema_version"])
    test.assertIn(report["profile_id"], schema["properties"]["profile_id"]["enum"])
    test.assertIn(report["fleet_size"], schema["properties"]["fleet_size"]["enum"])
    test.assertIn(report["status"], schema["properties"]["status"]["enum"])
    test.assertEqual(report["status"], report["terminal_state"])
    test.assertGreaterEqual(report["request_count"], 0)
    test.assertLessEqual(report["request_count"], 180)


class NodeRedLiveAgentAcceptanceTests(unittest.TestCase):
    def test_missing_authorization_is_blocked_before_native_traffic(self) -> None:
        contract = load_campaign_contract(CONTRACT)
        report = run_campaign(contract, profile_id="fleet-10", authorized=False)
        _assert_report_shape(self, report)
        self.assertEqual("blocked", report["status"])
        self.assertEqual(["authorization-required"], report["issue_codes"])
        self.assertEqual(0, report["request_count"])

    def test_native_unavailable_is_not_claimed_as_live_success(self) -> None:
        contract = load_campaign_contract(CONTRACT)
        report = run_campaign(contract, profile_id="fleet-50", authorized=True)
        _assert_report_shape(self, report)
        self.assertIn(report["status"], {"blocked", "not-run"})
        self.assertNotEqual("passed", report["status"])
        self.assertEqual(0, report["request_count"])

    def test_aggregate_comparison_ignores_run_id_and_prose(self) -> None:
        contract = load_campaign_contract(CONTRACT)
        first = run_campaign(contract, profile_id="fleet-10", authorized=False)
        second = run_campaign(contract, profile_id="fleet-10", authorized=False)
        comparable = ("profile_id", "fleet_size", "status", "terminal_state", "issue_codes", "request_count", "error_count")
        self.assertEqual(tuple(first[key] for key in comparable), tuple(second[key] for key in comparable))
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_both_profiles_are_explicitly_represented(self) -> None:
        contract = load_campaign_contract(CONTRACT)
        self.assertEqual({10, 50}, {profile["fleet_size"] for profile in contract["profiles"]})
        self.assertEqual({"fleet-10", "fleet-50"}, {profile["id"] for profile in contract["profiles"]})


if __name__ == "__main__":
    unittest.main()
