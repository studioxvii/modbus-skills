from __future__ import annotations

import sys
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_node_red_live_campaign import (  # noqa: E402
    CampaignError,
    REQUIRED_HASHES,
    load_campaign_contract,
    _verify_evidence_artifacts,
    _flow_environment,
    _oracle_mismatches,
    validate_campaign_settings,
)
from modbus_skills.node_red_runtime import (  # noqa: E402
    CampaignError as NodeRedCampaignError,
    NodeRedAdminClient,
    NodeRedRuntime,
    validate_flow,
    SimulatorClient,
    SimulatorError,
)
from modbus_skills.node_red import export_node_red  # noqa: E402
from tests.test_node_red import artifact_text, inputs  # noqa: E402


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

    def test_generated_flow_passes_preflight_and_unsafe_client_is_rejected(self) -> None:
        canonical_map, read_plan = inputs()
        flow = json.loads(artifact_text(export_node_red(canonical_map, read_plan), "flow.json"))
        validate_flow(flow)
        client = next(node for node in flow if node.get("type") == "modbus-client")
        client["tcpHost"] = "192.0.2.10"
        with self.assertRaises(NodeRedCampaignError):
            validate_flow(flow)

    def test_evidence_artifacts_must_match_supplied_hashes(self) -> None:
        flow = [{"id": "tab", "type": "tab", "disabled": True}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            hashes = {}
            for name in REQUIRED_HASHES:
                payload = json.dumps(flow).encode() if name == "flow_sha256" else b"{}"
                path = root / f"{name}.json"
                path.write_bytes(payload)
                paths[name] = path
                hashes[name] = hashlib.sha256(payload).hexdigest()
            _verify_evidence_artifacts(paths, hashes, flow)
            hashes["flow_sha256"] = "0" * 64
            with self.assertRaises(CampaignError):
                _verify_evidence_artifacts(paths, hashes, flow)

    def test_flow_environment_fills_every_generated_route(self) -> None:
        environment = _flow_environment(
            [
                {"type": "modbus-client", "tcpHost": "${MODBUS_A_HOST}", "tcpPort": "${MODBUS_A_PORT}"},
                {"type": "modbus-client", "tcpHost": "${MODBUS_B_HOST}", "tcpPort": "${MODBUS_B_PORT}"},
            ],
            modbus_port=5502,
            capture_path=Path("capture.json"),
        )
        self.assertEqual("127.0.0.1", environment["MODBUS_A_HOST"])
        self.assertEqual("5502", environment["MODBUS_B_PORT"])

    def test_oracle_reports_incomplete_capture_identity(self) -> None:
        mismatches = _oracle_mismatches(
            {
                "samples": [
                    {
                        "sample_id": "sample-1",
                        "unit_id": 1,
                        "protocol_offset": 0,
                        "raw_words": [1],
                        "success": True,
                    }
                ]
            },
            object(),
        )
        self.assertEqual("identity-incomplete", mismatches[0]["reason"])

    def test_simulator_boundary_uses_json_http_and_surfaces_http_errors(self) -> None:
        client = SimulatorClient("http://127.0.0.1:5020")
        with patch.object(client, "_request_json", return_value={"ready": True, "modbus_port": 5502, "num_generators": 10}):
            ready = client.readiness()
        self.assertEqual(5502, ready["modbus_port"])
        self.assertEqual(10, ready["num_generators"])
        with patch.object(client, "_request_json", side_effect=SimulatorError("HTTP 503")):
            with self.assertRaises(SimulatorError):
                client.readiness()

    def test_admin_driver_triggers_once_and_restores_original_flows(self) -> None:
        class Response:
            def __init__(self, body=b""):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return self.body

        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.json"
            calls = []

            def opener(request, timeout):
                calls.append((request.method, request.full_url))
                if request.method == "GET":
                    return Response(b"[]")
                if "/inject/" in request.full_url:
                    capture_path.write_text(
                        json.dumps(
                            {
                                "expected_request_ids": ["run:block"],
                                "runtime_metadata": {"terminal_state": "drained"},
                                "samples": [{"request_id": "run:block"}],
                            }
                        ),
                        encoding="utf-8",
                    )
                return Response()

            admin = NodeRedAdminClient(opener=opener, wait=lambda _: None)
            capture = admin.run_flow(
                [
                    {"id": "tab", "type": "tab", "disabled": True},
                    {"id": "inject", "type": "inject", "repeat": "", "crontab": "", "once": False},
                    {"id": "read", "type": "modbus-flex-getter", "fc": 3, "tcpHost": "127.0.0.1"},
                ],
                capture_path=capture_path,
                environment={"MODBUS_CAPTURE_PATH": str(capture_path)},
                timeout_seconds=1,
            )
        self.assertEqual(["run:block"], capture["expected_request_ids"])
        self.assertEqual(2, sum(1 for method, url in calls if method == "POST" and url.endswith("/flows")))
        self.assertEqual(1, sum(1 for method, url in calls if method == "POST" and "/inject/" in url))


if __name__ == "__main__":
    unittest.main()
