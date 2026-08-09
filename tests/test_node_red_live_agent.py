from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from scripts.run_node_red_live_campaign import load_campaign_contract, run_campaign
from modbus_skills.node_red_runtime import NodeRedRuntime
from modbus_skills.exporters import canonical_map_hash
from modbus_skills.node_red import export_node_red
from modbus_skills.read_plan import compile_read_plan


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

    def test_ready_native_driver_runs_flow_and_checks_simulator_oracle(self) -> None:
        contract = load_campaign_contract(CONTRACT)
        points = [
            {
                "logical_point_id": f"unit-{unit_id}",
                "name": f"Unit {unit_id}",
                "route_id": "default",
                "unit_id": unit_id,
                "area": "holding-register",
                "protocol_offset": 0,
                "source_address": {"raw": "40001", "convention": "modicon-reference"},
                "datatype": "uint16",
                "word_span": 1,
                "byte_order": None,
                "byte_order_confirmed": True,
                "normalization_status": "confirmed",
            }
            for unit_id in range(1, 11)
        ]
        canonical_map = {"schema_version": "modbus-map/v1", "points": points}
        read_plan = compile_read_plan(points).to_dict()
        read_plan["input_hashes"] = {"canonical_map": canonical_map_hash(canonical_map)}
        exported = export_node_red(canonical_map, read_plan)
        flow = json.loads(next(artifact.as_text() for artifact in exported.artifacts if artifact.path.endswith("flow.json")))
        blocks = next(node["modbusSkillsBlocks"] for node in flow if node.get("modbusSkillsBlocks"))
        capture = {
            "schema_version": "capture/v1",
            "runtime_metadata": {
                "target": "node-red",
                "terminal_state": "drained",
                "queue_depth": 0,
                "max_in_flight": 1,
            },
            "expected_request_ids": [f"run:{block['block_id']}" for block in blocks],
            "completed_request_ids": [f"run:{block['block_id']}" for block in blocks],
            "samples": [
                {
                    "sample_id": f"run:{block['block_id']}:unit-{block['unit_id']}",
                    "request_id": f"run:{block['block_id']}",
                    "block_id": block["block_id"],
                    "point_id": f"unit-{block['unit_id']}",
                    "route": "default",
                    "route_id": "default",
                    "unit_id": block["unit_id"],
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "raw_words": [123],
                    "success": True,
                    "status": "success",
                    "derived_values": {},
                    "timestamp": "2026-08-09T12:00:00Z",
                }
                for block in blocks
            ],
        }

        class Admin:
            restored = False

            @contextmanager
            def campaign_session(self, flow, *, capture_path, environment):
                try:
                    yield lambda timeout_seconds: capture
                finally:
                    self.restored = True

        class Simulator:
            def require_ready(self, expected_fleet):
                return type("Ready", (), {"modbus_port": 5020, "fleet_size": expected_fleet})()

            def registers(self, unit_id):
                return {"registers": {"0": 123}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_paths = {}
            hashes = {}
            payloads = {
                "canonical_map_sha256": json.dumps(canonical_map, sort_keys=True).encode(),
                "read_plan_sha256": json.dumps(read_plan, sort_keys=True).encode(),
                "flow_sha256": json.dumps(flow).encode(),
                "manifest_sha256": b"{}",
                "simulator_config_sha256": b"{}",
            }
            for name, payload in payloads.items():
                path = root / f"{name}.json"
                path.write_bytes(payload)
                artifact_paths[name] = path
                hashes[name] = hashlib.sha256(payload).hexdigest()
            report = run_campaign(
                contract,
                profile_id="fleet-10",
                authorized=True,
                runtime=NodeRedRuntime(which=lambda _: "/tmp/node-red", wait=lambda _: None),
                flow=flow,
                hashes=hashes,
                artifact_paths=artifact_paths,
                admin=Admin(),
                simulator=Simulator(),
                capture_path=Path(directory) / "capture.json",
            )
        _assert_report_shape(self, report)
        self.assertEqual("passed", report["status"])
        self.assertEqual(30, report["request_count"])
        self.assertEqual(0, report["error_count"])
        self.assertTrue(report["queue_drained"])
        self.assertEqual(3, len(report["response_time_ms"]))
        self.assertTrue(report["cleanup"]["flow_removed"])


if __name__ == "__main__":
    unittest.main()
