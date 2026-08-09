from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "node_red_live" / "fixtures" / "campaign.json"
README = ROOT / "tests" / "node_red_live" / "README.md"
AGENT_TASK = ROOT / "tests" / "node_red_live" / "agent-task.md"


TERMINAL_STATUSES = {"passed", "failed", "blocked", "not-run", "inconclusive"}
REQUIRED_HASHES = {
    "canonical_map_sha256",
    "read_plan_sha256",
    "flow_sha256",
    "manifest_sha256",
    "simulator_config_sha256",
}
CAPTURE_IDENTITY = {"route", "unit_id", "area", "protocol_offset"}
CAPTURE_FIELDS = {
    "timestamp",
    "raw_words",
    "response_time_ms",
    "status",
}


def _load() -> dict[str, object]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _validate_contract(value: dict[str, object]) -> None:
    """Validate the public campaign contract without contacting a runtime."""

    assert value["schema_version"] == "node-red-live-campaign/v1"
    profiles = value["profiles"]
    assert isinstance(profiles, list)
    assert {profile["fleet_size"] for profile in profiles} == {10, 50}
    assert {profile["id"] for profile in profiles} == {"fleet-10", "fleet-50"}

    budget = value["budget"]
    assert budget == {
        "rounds": 3,
        "max_compiled_block_reads": 180,
        "max_seconds": 60,
        "cadence_seconds": 1,
        "max_in_flight": 1,
    }
    assert value["readiness"] == {
        "required": True,
        "endpoint": "/api/ready",
        "fleet_size_must_match": True,
        "reported_modbus_port_required": True,
    }

    safety = value["safety"]
    endpoint = urlparse(safety["endpoint"])
    assert endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
    assert safety["require_loopback"] is True
    assert safety["allowed_function_codes"] == [1, 2, 3, 4]
    for forbidden in (
        "writes",
        "broadcasts",
        "discovery_scans",
        "scheduled_polling",
        "deploy_time_triggers",
        "credentials",
    ):
        assert safety[forbidden] is False

    authorization = safety["authorization"]
    assert authorization == {
        "required": True,
        "scope": "named-local-campaign",
        "per_read": False,
    }

    bindings = value["hash_bindings"]
    assert set(bindings) == REQUIRED_HASHES
    for key in REQUIRED_HASHES:
        assert re.fullmatch(r"[a-z0-9_]+", bindings[key])

    capture = value["capture"]
    assert capture["schema_version"] == "capture/v1"
    assert set(capture["required_identity"]) == CAPTURE_IDENTITY
    assert set(capture["required_fields"]) == CAPTURE_FIELDS
    assert capture["success_fields"] == ["derived_values"]
    assert capture["error_forbidden_fields"] == ["derived_values"]
    assert set(capture["row_statuses"]) == {"success", "error"}

    assert set(value["terminal_statuses"]) == TERMINAL_STATUSES
    output = value["output"]
    assert set(output["ignored_roots"]) == {"artifacts", "private"}
    assert output["sanitized"] is True
    assert output["forbid_absolute_paths"] is True
    for output_value in output.values():
        if isinstance(output_value, str):
            assert not output_value.startswith("/")
            assert not re.match(r"^[A-Za-z]:[\\/]", output_value)


class NodeRedLiveContractTests(unittest.TestCase):
    def test_happy_path_has_two_profiles_finite_budget_and_terminal_statuses(self) -> None:
        contract = _load()
        _validate_contract(contract)
        profiles = {profile["id"]: profile for profile in contract["profiles"]}
        self.assertEqual(list(range(1, 11)), profiles["fleet-10"]["unit_ids"])
        self.assertEqual(list(range(1, 51)), profiles["fleet-50"]["unit_ids"])

    def test_safety_mutations_are_rejected(self) -> None:
        contract = _load()
        for name, mutation in (
            ("non-loopback", lambda c: c["safety"].update(endpoint="http://192.0.2.10:502")),
            ("missing-hash", lambda c: c["hash_bindings"].pop("flow_sha256")),
            ("scheduled", lambda c: c["safety"].update(scheduled_polling=True)),
            ("write-node", lambda c: c["safety"].update(writes=True)),
            ("credential", lambda c: c["safety"].update(credentials=True)),
        ):
            with self.subTest(name=name):
                candidate = copy.deepcopy(contract)
                mutation(candidate)
                with self.assertRaises((AssertionError, KeyError)):
                    _validate_contract(candidate)

    def test_sanitized_output_rejects_absolute_paths(self) -> None:
        contract = _load()
        candidate = copy.deepcopy(contract)
        candidate["output"]["example_path"] = str(Path("/", "Users", "example", "private", "run"))
        with self.assertRaises(AssertionError):
            _validate_contract(candidate)

    def test_capture_rows_require_complete_identity_and_evidence(self) -> None:
        capture = _load()["capture"]
        valid = set(capture["required_identity"]) | set(capture["required_fields"])
        self.assertEqual(CAPTURE_IDENTITY | CAPTURE_FIELDS, valid)
        for missing in sorted(valid):
            with self.subTest(missing=missing):
                row = valid - {missing}
                self.assertNotEqual(valid, row)

    def test_runbook_and_agent_task_are_human_level_and_sanitized(self) -> None:
        readme = README.read_text(encoding="utf-8")
        task = AGENT_TASK.read_text(encoding="utf-8")
        combined = f"{readme}\n{task}"
        for phrase in ("10 units", "50 units", "three rounds", "one scoped human authorization", "capture/v1"):
            self.assertIn(phrase, combined)
        for forbidden in ("modpoll", "modscan", "password", "secret", "/" + "Users/"):
            self.assertNotIn(forbidden.lower(), combined.lower())


if __name__ == "__main__":
    unittest.main()
