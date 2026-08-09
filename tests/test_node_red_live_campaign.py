from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_node_red_live_campaign import (  # noqa: E402
    CampaignError,
    capture_row,
    load_campaign_contract,
    validate_campaign_settings,
)
from node_red_live.node_red import (  # noqa: E402
    BoundedReadLedger,
    CampaignError as NodeRedCampaignError,
    NodeRedRuntime,
    validate_flow,
)
from node_red_live.simulator import (  # noqa: E402
    SimulatorClient,
    SimulatorError,
)


FIXTURE = ROOT / "tests" / "node_red_live" / "fixtures" / "campaign.json"


class NodeRedLiveCampaignTests(unittest.TestCase):
    def test_contract_rejects_non_loopback_and_unsafe_budget(self) -> None:
        contract = load_campaign_contract(FIXTURE)
        validate_campaign_settings(contract)
        contract["safety"]["endpoint"] = "http://192.0.2.10:502"
        with self.assertRaises(CampaignError):
            validate_campaign_settings(contract)
        contract = load_campaign_contract(FIXTURE)
        contract["budget"]["max_in_flight"] = 2
        with self.assertRaises(CampaignError):
            validate_campaign_settings(contract)

    def test_capture_error_row_has_no_stale_or_derived_values(self) -> None:
        row = capture_row(
            route="default",
            unit_id=4,
            area="holding-register",
            protocol_offset=0,
            timestamp="2026-08-09T12:00:00Z",
            raw_words=[123],
            response_time_ms=12.4,
            status="error",
            error="timeout",
            derived_values={"stale": 1},
        )
        self.assertEqual("error", row["status"])
        self.assertEqual([], row["raw_words"])
        self.assertNotIn("derived_values", row)
        self.assertEqual("timeout", row["error"])

    def test_flow_validation_rejects_write_or_scheduled_nodes(self) -> None:
        safe = [
            {"type": "tab", "disabled": True},
            {"type": "inject", "repeat": "", "crontab": "", "once": False},
            {"type": "modbus-flex-getter", "fc": 3, "tcpHost": "127.0.0.1", "tcpPort": 5020},
        ]
        validate_flow(safe)
        with self.assertRaises(CampaignError):
            validate_flow(safe + [{"type": "modbus-write"}])
        with self.assertRaises(CampaignError):
            validate_flow([{**safe[1], "repeat": "1"}, *safe[:1], safe[2]])

    def test_runtime_unavailable_is_honest_and_does_not_spawn(self) -> None:
        runtime = NodeRedRuntime(which=lambda _: None)
        availability = runtime.preflight([])
        self.assertEqual("blocked", availability["status"])
        self.assertIn("node-red-runtime-unavailable", availability["issue_codes"])

    def test_read_ledger_enforces_three_rounds_one_in_flight_and_180_reads(self) -> None:
        ledger = BoundedReadLedger(clock=lambda: 0.0)
        for _ in range(3):
            ledger.begin_round()
            for _ in range(60):
                ledger.begin()
                ledger.finish()
        self.assertEqual(180, ledger.request_count)
        with self.assertRaises(NodeRedCampaignError):
            ledger.begin_round()
        with self.assertRaises(NodeRedCampaignError):
            ledger.begin()

    def test_flow_validation_allows_placeholder_loopback_host_only(self) -> None:
        flow = [
            {"type": "tab", "disabled": True},
            {"type": "inject", "repeat": "", "crontab": "", "once": False},
            {"type": "modbus-flex-getter", "fc": 3, "tcpHost": "${MODBUS_HOST}"},
        ]
        validate_flow(flow)
        flow[-1]["tcpHost"] = "10.0.0.8"
        with self.assertRaises(NodeRedCampaignError):
            validate_flow(flow)

    def test_simulator_boundary_uses_json_http_and_surfaces_http_errors(self) -> None:
        client = SimulatorClient("http://127.0.0.1:5020")
        with patch.object(client, "_request_json", return_value={"ready": True, "modbus_port": 5502, "num_generators": 10}):
            ready = client.readiness()
        self.assertEqual(5502, ready["modbus_port"])
        self.assertEqual(10, ready["num_generators"])
        with patch.object(client, "_request_json", side_effect=SimulatorError("HTTP 503")):
            with self.assertRaises(SimulatorError):
                client.readiness()


if __name__ == "__main__":
    unittest.main()
