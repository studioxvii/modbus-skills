#!/usr/bin/env python3
"""Preflight and (when available) run the bounded Node-RED live campaign.

The native Node-RED driver is intentionally optional.  On a machine without the
CLI the command emits a sanitized blocked receipt instead of pretending that a
live read occurred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

if str(Path(__file__).resolve().parents[1] / "tests") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from node_red_live.node_red import CampaignError, NodeRedRuntime, ReadLedger


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_HASHES = {
    "canonical_map_sha256",
    "read_plan_sha256",
    "flow_sha256",
    "manifest_sha256",
    "simulator_config_sha256",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load_campaign_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignError("campaign contract must be a JSON object")
    return value


def validate_campaign_settings(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != "node-red-live-campaign/v1":
        raise CampaignError("unsupported campaign contract")
    profiles = contract.get("profiles")
    if not isinstance(profiles, list) or {p.get("fleet_size") for p in profiles} != {10, 50}:
        raise CampaignError("campaign must contain exactly 10- and 50-unit profiles")
    for profile in profiles:
        expected = list(range(1, int(profile["fleet_size"]) + 1))
        if profile.get("unit_ids") != expected:
            raise CampaignError("profile unit IDs must be contiguous and explicit")
    budget = contract.get("budget", {})
    if budget != {"rounds": 3, "max_compiled_block_reads": 180, "max_seconds": 60, "cadence_seconds": 1, "max_in_flight": 1}:
        raise CampaignError("campaign budget does not match the bounded acceptance contract")
    safety = contract.get("safety", {})
    endpoint = urlparse(str(safety.get("endpoint", "")))
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise CampaignError("live endpoint must be loopback HTTP")
    if safety.get("allowed_function_codes") != [1, 2, 3, 4]:
        raise CampaignError("campaign permits only FC01-FC04")
    for key in ("writes", "broadcasts", "discovery_scans", "scheduled_polling", "deploy_time_triggers", "credentials"):
        if safety.get(key) is not False:
            raise CampaignError(f"unsafe campaign setting: {key}")
    if not safety.get("flow_disabled_before_import") or not safety.get("manual_one_shot_trigger"):
        raise CampaignError("flow must be disabled and manually triggered")
    authorization = safety.get("authorization", {})
    if authorization != {"required": True, "scope": "named-local-campaign", "per_read": False}:
        raise CampaignError("authorization scope is not bounded")
    if set(contract.get("hash_bindings", {})) != REQUIRED_HASHES:
        raise CampaignError("all five evidence hash bindings are required")


def capture_row(*, route: str, unit_id: int, area: str, protocol_offset: int, timestamp: str, raw_words: list[int] | None, response_time_ms: float, status: str, error: str | None = None, derived_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if status not in {"success", "error"}:
        raise CampaignError("capture status must be success or error")
    row: dict[str, Any] = {"route": route, "unit_id": unit_id, "area": area, "protocol_offset": protocol_offset, "timestamp": timestamp, "response_time_ms": response_time_ms, "status": status}
    if status == "success":
        row["raw_words"] = list(raw_words or [])
        row["derived_values"] = dict(derived_values or {})
    else:
        row["raw_words"] = []
        if error:
            row["error"] = str(error)
    return row


def _hashes(contract: Mapping[str, Any], supplied: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in REQUIRED_HASHES:
        value = str((supplied or {}).get(name, "unavailable"))
        if value != "unavailable" and not HEX64.fullmatch(value):
            raise CampaignError(f"{name} must be a sha256 digest")
        result[name] = value
    return result


def run_campaign(contract: Mapping[str, Any], *, profile_id: str | None = None, authorized: bool = False, runtime: NodeRedRuntime | None = None, flow: list[Mapping[str, Any]] | None = None, hashes: Mapping[str, str] | None = None) -> dict[str, Any]:
    validate_campaign_settings(contract)
    selected = next((p for p in contract["profiles"] if p["id"] == profile_id), None) if profile_id else contract["profiles"][0]
    if selected is None:
        raise CampaignError("unknown campaign profile")
    report: dict[str, Any] = {"schema_version": "node-red-live-report/v1", "run_id": uuid.uuid4().hex, "profile_id": selected["id"], "fleet_size": selected["fleet_size"], "status": "blocked", "terminal_state": "blocked", "issue_codes": [], "request_count": 0, "error_count": 0, "hashes": _hashes(contract, hashes), "versions": {"campaign": contract["schema_version"]}, "cleanup": {"simulator_reset": False, "flow_removed": False}}
    if not authorized:
        report["issue_codes"] = ["authorization-required"]
        return report
    runtime = runtime or NodeRedRuntime()
    try:
        preflight = runtime.preflight(flow or [])
    except CampaignError as exc:
        report["status"] = report["terminal_state"] = "failed"
        report["issue_codes"] = ["unsafe-flow", str(exc)]
        return report
    report["versions"]["node_red"] = preflight.get("runtime", "unavailable")
    if preflight["status"] != "ready":
        report["issue_codes"] = list(preflight["issue_codes"])
        return report
    # Native execution is deliberately a separate driver; this command only
    # promises preflight unless a future driver supplies capture rows.
    report["status"] = report["terminal_state"] = "not-run"
    report["issue_codes"] = ["native-driver-not-configured"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=ROOT / "tests/node_red_live/fixtures/campaign.json")
    parser.add_argument("--profile", choices=("fleet-10", "fleet-50"))
    parser.add_argument("--authorize", action="store_true", help="confirm this named local campaign once")
    parser.add_argument("--node-red-cli")
    args = parser.parse_args(argv)
    try:
        contract = load_campaign_contract(args.contract)
        runtime = NodeRedRuntime(cli=args.node_red_cli)
        report = run_campaign(contract, profile_id=args.profile, authorized=args.authorize, runtime=runtime)
    except (OSError, json.JSONDecodeError, CampaignError) as exc:
        report = {"schema_version": "node-red-live-report/v1", "status": "failed", "terminal_state": "failed", "issue_codes": [str(exc)]}
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") in {"passed", "blocked", "not-run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
