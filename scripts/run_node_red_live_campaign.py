#!/usr/bin/env python3
"""Run the bounded Node-RED campaign or report exactly why it could not run."""

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

RUNTIME = Path(__file__).resolve().parents[1] / "plugins" / "modbus-skills" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from modbus_skills.analysis import analyze_capture
from modbus_skills.node_red_runtime import (
    CampaignError,
    NodeRedAdminClient,
    NodeRedRuntime,
    SimulatorClient,
    SimulatorError,
    planned_requests,
)


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_HASHES = {
    "canonical_map_sha256",
    "read_plan_sha256",
    "flow_sha256",
    "manifest_sha256",
    "simulator_config_sha256",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


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


def _hashes(supplied: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in REQUIRED_HASHES:
        value = str((supplied or {}).get(name, "unavailable"))
        if value != "unavailable" and not HEX64.fullmatch(value):
            raise CampaignError(f"{name} must be a sha256 digest")
        result[name] = value
    return result


def _verify_evidence_artifacts(
    paths: Mapping[str, Path] | None,
    expected_hashes: Mapping[str, str],
    flow: list[Mapping[str, Any]],
) -> None:
    if paths is None or set(paths) != REQUIRED_HASHES:
        raise CampaignError("all five evidence artifact paths are required")
    for name in sorted(REQUIRED_HASHES):
        path = paths[name]
        if not path.is_file() or path.is_symlink():
            raise CampaignError(f"{name} evidence artifact is missing or unsafe")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_hashes[name]:
            raise CampaignError(f"{name} does not match its supplied hash")
        if name == "flow_sha256":
            try:
                recorded_flow = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CampaignError("flow evidence artifact is not valid JSON") from exc
            if recorded_flow != flow:
                raise CampaignError("flow evidence artifact does not match the reviewed flow")


def _oracle_mismatches(capture: Mapping[str, Any], simulator: Any) -> list[dict[str, Any]]:
    cache: dict[int, Mapping[str, Any]] = {}
    mismatches: list[dict[str, Any]] = []
    for sample in capture.get("samples", ()):
        if not isinstance(sample, Mapping):
            continue
        try:
            unit_id = int(sample.get("unit_id", 0))
            offset = int(sample.get("protocol_offset", -1))
        except (TypeError, ValueError):
            mismatches.append({"sample_id": sample.get("sample_id"), "reason": "identity-incomplete"})
            continue
        route = sample.get("route", sample.get("route_id"))
        area = sample.get("area")
        if route in (None, "") or area in (None, ""):
            mismatches.append({"sample_id": sample.get("sample_id"), "reason": "identity-incomplete"})
            continue
        if sample.get("success") is False:
            continue
        words = sample.get("raw_words", ())
        if unit_id < 1 or offset < 0 or not isinstance(words, list):
            mismatches.append({"sample_id": sample.get("sample_id"), "reason": "identity-incomplete"})
            continue
        if unit_id not in cache:
            cache[unit_id] = simulator.registers(unit_id)
        registers = cache[unit_id].get("registers")
        if not isinstance(registers, Mapping):
            mismatches.append({"sample_id": sample.get("sample_id"), "reason": "oracle-registers-missing"})
            continue
        expected = [registers.get(str(offset + index), registers.get(offset + index)) for index in range(len(words))]
        if expected != words:
            mismatches.append(
                {
                    "sample_id": sample.get("sample_id"),
                    "reason": "raw-words-do-not-match-oracle",
                    "expected": expected,
                    "observed": words,
                }
            )
    return mismatches


def _flow_environment(
    flow: list[Mapping[str, Any]], *, modbus_port: int, capture_path: Path
) -> dict[str, str]:
    environment = {
        "MODBUS_WATCHDOG_MS": "3000",
        "MODBUS_CAPTURE_PATH": str(capture_path.resolve()),
    }
    for node in flow:
        if node.get("type") != "modbus-client":
            continue
        for field, value in (("tcpHost", "127.0.0.1"), ("tcpPort", str(modbus_port))):
            match = ENV_PLACEHOLDER.fullmatch(str(node.get(field, "")))
            if match:
                environment[match.group(1)] = value
    return environment


def run_campaign(
    contract: Mapping[str, Any],
    *,
    profile_id: str | None = None,
    authorized: bool = False,
    runtime: NodeRedRuntime | None = None,
    flow: list[Mapping[str, Any]] | None = None,
    hashes: Mapping[str, str] | None = None,
    artifact_paths: Mapping[str, Path] | None = None,
    admin: Any | None = None,
    simulator: Any | None = None,
    capture_path: Path | None = None,
) -> dict[str, Any]:
    validate_campaign_settings(contract)
    selected = next((p for p in contract["profiles"] if p["id"] == profile_id), None) if profile_id else contract["profiles"][0]
    if selected is None:
        raise CampaignError("unknown campaign profile")
    report: dict[str, Any] = {"schema_version": "node-red-live-report/v1", "run_id": uuid.uuid4().hex, "profile_id": selected["id"], "fleet_size": selected["fleet_size"], "status": "blocked", "terminal_state": "blocked", "issue_codes": [], "request_count": 0, "error_count": 0, "queue_drained": False, "response_time_ms": [], "hashes": _hashes(hashes), "versions": {"campaign": contract["schema_version"]}, "cleanup": {"simulator_reset": False, "flow_removed": False}}
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
    if admin is None or simulator is None or capture_path is None or not flow:
        report["status"] = report["terminal_state"] = "not-run"
        report["issue_codes"] = ["native-driver-not-configured"]
        return report
    if any(value == "unavailable" for value in report["hashes"].values()):
        report["issue_codes"] = ["evidence-hashes-required"]
        return report
    try:
        _verify_evidence_artifacts(artifact_paths, report["hashes"], flow)
        ready = simulator.require_ready(int(selected["fleet_size"]))
        blocks = planned_requests(flow)
        expected_units = set(int(value) for value in selected["unit_ids"])
        planned_units = {int(block.get("unit_id", 0)) for block in blocks}
        rounds = int(contract["budget"]["rounds"])
        if planned_units != expected_units:
            raise CampaignError("generated flow does not cover the selected fleet exactly")
        if len(blocks) * rounds > int(contract["budget"]["max_compiled_block_reads"]):
            raise CampaignError("generated flow exceeds the compiled-block read budget")
        all_mismatches: list[dict[str, Any]] = []
        coverage_error = False
        queue_drained = True
        deadline = runtime.clock() + float(contract["budget"]["max_seconds"])
        for round_index in range(rounds):
            remaining = deadline - runtime.clock()
            if remaining <= 0:
                raise CampaignError("live campaign time budget exhausted")
            capture = admin.run_flow(
                flow,
                capture_path=capture_path,
                environment=_flow_environment(
                    flow, modbus_port=ready.modbus_port, capture_path=capture_path
                ),
                timeout_seconds=remaining,
            )
            analysis = analyze_capture(capture)
            campaign = analysis["campaign"]
            report["response_time_ms"].append(analysis["communications"]["response_ms"])
            runtime_metadata = capture.get("runtime_metadata", {})
            queue_drained = queue_drained and isinstance(runtime_metadata, Mapping) and runtime_metadata.get("terminal_state") == "drained"
            if (
                campaign["observed_requests"] != len(blocks)
                or campaign["missing_requests"] != 0
            ):
                coverage_error = True
            report["request_count"] += int(campaign["observed_requests"])
            report["error_count"] += int(analysis["communications"]["error_count"])
            all_mismatches.extend(_oracle_mismatches(capture, simulator))
            if round_index + 1 < rounds:
                runtime.wait(float(contract["budget"]["cadence_seconds"]))
        report["cleanup"]["flow_removed"] = True
        report["queue_drained"] = queue_drained
        if coverage_error:
            report["issue_codes"].append("planned-requests-missing")
        if not queue_drained:
            report["issue_codes"].append("node-red-queue-not-drained")
        if report["error_count"]:
            report["issue_codes"].append("node-red-read-errors")
        if all_mismatches:
            report["issue_codes"].append("simulator-oracle-mismatch")
        report["oracle_mismatches"] = all_mismatches[:10]
        report["status"] = report["terminal_state"] = (
            "passed" if not report["issue_codes"] else "failed"
        )
    except (CampaignError, SimulatorError, OSError, ValueError) as exc:
        report["status"] = report["terminal_state"] = "failed"
        report["issue_codes"] = [str(exc)]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=ROOT / "tests/node_red_live/fixtures/campaign.json")
    parser.add_argument("--profile", choices=("fleet-10", "fleet-50"))
    parser.add_argument("--authorize", action="store_true", help="confirm this named local campaign once")
    parser.add_argument("--node-red-cli")
    parser.add_argument("--node-red-url", default="http://127.0.0.1:1880")
    parser.add_argument("--simulator-url", default="http://127.0.0.1:5000")
    parser.add_argument("--flow", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--hashes", type=Path)
    parser.add_argument("--canonical-map", type=Path)
    parser.add_argument("--read-plan", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--simulator-config", type=Path)
    args = parser.parse_args(argv)
    try:
        contract = load_campaign_contract(args.contract)
        runtime = NodeRedRuntime(cli=args.node_red_cli)
        flow = json.loads(args.flow.read_text(encoding="utf-8")) if args.flow else None
        hashes = json.loads(args.hashes.read_text(encoding="utf-8")) if args.hashes else None
        artifact_paths = (
            {
                "canonical_map_sha256": args.canonical_map,
                "read_plan_sha256": args.read_plan,
                "flow_sha256": args.flow,
                "manifest_sha256": args.manifest,
                "simulator_config_sha256": args.simulator_config,
            }
            if all((args.canonical_map, args.read_plan, args.flow, args.manifest, args.simulator_config))
            else None
        )
        report = run_campaign(
            contract,
            profile_id=args.profile,
            authorized=args.authorize,
            runtime=runtime,
            flow=flow,
            hashes=hashes,
            artifact_paths=artifact_paths,
            admin=NodeRedAdminClient(args.node_red_url) if args.flow and args.capture else None,
            simulator=SimulatorClient(args.simulator_url) if args.flow and args.capture else None,
            capture_path=args.capture,
        )
    except (OSError, json.JSONDecodeError, CampaignError, SimulatorError) as exc:
        report = {"schema_version": "node-red-live-report/v1", "status": "failed", "terminal_state": "failed", "issue_codes": [str(exc)]}
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("status") in {"passed", "blocked", "not-run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
