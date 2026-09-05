from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
FIXTURES = ROOT / "tests" / "fixtures" / "outputs"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.node_red import export_node_red  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402
from modbus_skills.exporters import canonical_map_hash  # noqa: E402


def point(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "logical_point_id": "pressure",
        "name": "Discharge Pressure",
        "route_id": "default",
        "unit_id": 1,
        "area": "holding-register",
        "protocol_offset": 100,
        "source_address": {"raw": "40101", "convention": "modicon-reference"},
        "datatype": "float32",
        "word_span": 2,
        "byte_order": "ABCD",
        "byte_order_confirmed": True,
        "normalization_status": "confirmed",
        "scale": 0.1,
        "engineering_offset": 0.0,
        "engineering_unit": "bar",
    }
    value.update(updates)
    return value


def inputs(*points: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    values = list(points or (point(),))
    canonical_map = {"schema_version": "modbus-map/v1", "points": values}
    read_plan = compile_read_plan(values).to_dict()
    read_plan["input_hashes"] = {"canonical_map": canonical_map_hash(canonical_map)}
    return canonical_map, read_plan


def artifact_text(result: object, suffix: str) -> str:
    return next(
        artifact.as_text()
        for artifact in result.artifacts  # type: ignore[attr-defined]
        if artifact.path.endswith(suffix)
    )


class NodeRedExporterTests(unittest.TestCase):
    def test_flow_is_disabled_read_only_and_runs_one_sequenced_plan(self) -> None:
        first = point()
        second = point(
            logical_point_id="temperature",
            name="Temperature",
            protocol_offset=110,
            datatype="int16",
            word_span=1,
            byte_order=None,
        )
        canonical_map, read_plan = inputs(first, second)
        result = export_node_red(canonical_map, read_plan)
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        counts = Counter(node["type"] for node in flow)
        self.assertEqual(1, counts["inject"])
        self.assertEqual(1, counts["modbus-flex-getter"])
        self.assertEqual(1, counts["file"])
        self.assertFalse(counts["modbus-read"])
        self.assertTrue(next(node for node in flow if node["type"] == "tab")["disabled"])
        self.assertEqual(1, counts["catch"])
        self.assertEqual(1, counts["status"])
        self.assertEqual(1, counts["trigger"])
        getter = next(node for node in flow if node["type"] == "modbus-flex-getter")
        self.assertEqual([3], getter["modbusSkillsAllowedFunctionCodes"])
        sequencer = next(node for node in flow if node.get("modbusSkillsBlocks"))
        self.assertEqual(len(read_plan["requests"]), len(sequencer["modbusSkillsBlocks"]))
        self.assertFalse(any("write" in node["type"].lower() for node in flow))

    def test_flow_has_human_readable_sections_and_read_only_dashboard(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        flow = json.loads(artifact_text(result, "flow.json"))
        groups = {node["name"]: node for node in flow if node["type"] == "group"}
        self.assertEqual(
            {
                "PRIMARY READ PATH",
                "HEALTH & FAILURE HANDLING",
                "RETRY & WATCHDOG",
                "LIVE CAPTURE DASHBOARD",
            },
            set(groups),
        )
        self.assertTrue(all(group["nodes"] for group in groups.values()))
        comments = {node["name"] for node in flow if node["type"] == "comment"}
        self.assertIn("PRIMARY READ PATH · one request in flight", comments)
        self.assertIn("HEALTH & FAILURE HANDLING · status / timeout / errors", comments)
        self.assertIn("RETRY & WATCHDOG · bounded recovery", comments)
        self.assertIn("LIVE CAPTURE DASHBOARD · GET /modbus-dashboard", comments)
        dashboard_http = next(node for node in flow if node["type"] == "http in")
        dashboard_render = next(node for node in flow if node.get("name") == "Render live dashboard")
        dashboard_response = next(node for node in flow if node["type"] == "http response")
        self.assertEqual("/modbus-dashboard", dashboard_http["url"])
        self.assertEqual([dashboard_render["id"]], dashboard_http["wires"][0])
        self.assertEqual([dashboard_response["id"]], dashboard_render["wires"][0])
        self.assertIn("modbusSkillsCapture", dashboard_render["func"])
        self.assertIn("refresh", dashboard_render["func"])
        self.assertIn("Register data", dashboard_render["func"])
        self.assertIn("Engineering value", dashboard_render["func"])
        self.assertIn("Enable the flow to start live polling", dashboard_render["func"])
        self.assertNotIn("inject", dashboard_render["func"])
        manifest = json.loads(artifact_text(result, "manifest.json"))
        self.assertEqual("core-http", manifest["dashboard"]["type"])
        self.assertTrue(manifest["dashboard"]["read_only"])

    def test_primary_path_is_ordered_and_diagnostics_are_below_it(self) -> None:
        canonical_map, read_plan = inputs()
        flow = json.loads(artifact_text(export_node_red(canonical_map, read_plan), "flow.json"))
        groups = {node["name"]: node for node in flow if node["type"] == "group"}
        names = {
            node["name"]: node
            for node in flow
            if node["type"] in {"inject", "function", "modbus-flex-getter", "file"}
        }
        primary = [
            names["01 Live poll (5s)"],
            names["02 Sequence read blocks"],
            names["03 Read route: default"],
            names["04 Validate response"],
            names["05 Decode points"],
            names["06 Terminal gate"],
            names["07 Build capture/v1"],
            names["08 Write capture.json"],
        ]
        self.assertEqual(sorted(node["x"] for node in primary), [node["x"] for node in primary])
        self.assertEqual(1, len({node["y"] for node in primary[:5]}))
        self.assertGreater(names["06 Terminal gate"]["y"], names["05 Decode points"]["y"])
        self.assertGreater(names["07 Build capture/v1"]["y"], names["06 Terminal gate"]["y"])
        continuation = names["07a Continue plan"]
        self.assertEqual(continuation["y"], names["07 Build capture/v1"]["y"])
        self.assertIn(names["02 Sequence read blocks"]["id"], continuation["wires"][0])
        continuation_lane = {
            node["name"]
            for node in flow
            if node.get("y") == continuation["y"]
            and node.get("type") not in {"group", "comment"}
        }
        self.assertEqual(
            {"07a Continue plan", "07 Build capture/v1", "08 Write capture.json"},
            continuation_lane,
        )
        diagnostics = [node for node in flow if node.get("g") == next(g["id"] for g in flow if g.get("name") == "HEALTH & FAILURE HANDLING")]
        self.assertTrue(all(node["y"] >= 300 for node in diagnostics))
        visual_nodes = [
            node
            for node in flow
            if node.get("type") not in {"group", "comment", "tab", "modbus-client"}
            and isinstance(node.get("x"), int)
            and isinstance(node.get("y"), int)
        ]
        for left, right in combinations(visual_nodes, 2):
            with self.subTest(left=left["name"], right=right["name"]):
                self.assertTrue(
                    abs(left["x"] - right["x"]) >= 180
                    or abs(left["y"] - right["y"]) >= 40,
                    "Node-RED nodes must not overlap",
                )
        for group in groups.values():
            for node in flow:
                if node.get("g") != group["id"]:
                    continue
                self.assertGreaterEqual(node["x"], group["x"])
                self.assertLessEqual(node["x"], group["x"] + group["w"])
                self.assertGreaterEqual(node["y"], group["y"])
                self.assertLessEqual(node["y"], group["y"] + group["h"])
        self.assertLess(
            next(node for node in flow if node.get("name", "").startswith("PRIMARY READ PATH ·"))["y"],
            groups["PRIMARY READ PATH"]["y"],
        )
        self.assertLess(
            next(node for node in flow if node.get("name", "").startswith("HEALTH & FAILURE HANDLING ·"))["y"],
            groups["HEALTH & FAILURE HANDLING"]["y"],
        )
        self.assertLess(
            next(node for node in flow if node.get("name", "").startswith("LIVE CAPTURE DASHBOARD ·"))["y"],
            groups["LIVE CAPTURE DASHBOARD"]["y"],
        )

    def test_probe_dashboard_and_readme_use_manual_one_shot_language(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan, mode="probe")
        flow = json.loads(artifact_text(result, "flow.json"))
        dashboard = next(
            node for node in flow if node.get("name") == "Render live dashboard"
        )
        self.assertIn("Start the bounded plan once", dashboard["func"])
        self.assertNotIn("start live polling", dashboard["func"])
        readme = artifact_text(result, "README.md")
        self.assertIn("Probe mode is manual one-shot and does not poll", readme)
        self.assertNotIn("every five seconds while the tab stays enabled", readme)

    def test_ids_are_unique_and_environment_values_are_placeholders(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        flow = json.loads(artifact_text(result, "flow.json"))
        identifiers = [node["id"] for node in flow]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        client = next(node for node in flow if node["type"] == "modbus-client")
        self.assertEqual("${MODBUS_HOST}", client["tcpHost"])
        self.assertEqual("${MODBUS_PORT}", client["tcpPort"])
        read = next(node for node in flow if node["type"] == "modbus-flex-getter")
        inject = next(node for node in flow if node["type"] == "inject")
        sequencer = next(node for node in flow if node.get("name") == "02 Sequence read blocks" and node["type"] == "function")
        self.assertEqual(sequencer["id"], inject["wires"][0][0])
        self.assertIn(read["id"], sequencer["wires"][0])
        self.assertEqual("5", inject["repeat"])
        self.assertEqual("live-poll", inject["modbusSkillsRole"])
        self.assertEqual("", inject["crontab"])
        self.assertFalse(inject["once"])

    def test_derive_node_preserves_raw_values_and_decoding_metadata(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        flow = json.loads(artifact_text(result, "flow.json"))
        derive = next(
            node
            for node in flow
            if node["type"] == "function" and node["name"] == "05 Decode points"
        )
        self.assertIn("raw_values", derive["func"])
        sequencer = next(node for node in flow if node.get("name") == "02 Sequence read blocks" and node["type"] == "function")
        self.assertIn('"datatype":"float32"', sequencer["func"])
        self.assertIn('"byte_order":"ABCD"', sequencer["func"])
        self.assertIn('"scale":0.1', sequencer["func"])

    def test_flow_writes_capture_v1_and_advances_only_after_a_result(self) -> None:
        canonical_map, read_plan = inputs(
            point(),
            point(logical_point_id="temperature", protocol_offset=110, datatype="int16", word_span=1),
        )
        flow = json.loads(artifact_text(export_node_red(canonical_map, read_plan), "flow.json"))
        sequencer = next(node for node in flow if node.get("name") == "02 Sequence read blocks" and node["type"] == "function")
        capture = next(node for node in flow if node.get("name") == "07 Build capture/v1")
        sink = next(node for node in flow if node["type"] == "file")
        self.assertIn("flow.set('modbusSkillsQueue'", sequencer["func"])
        self.assertIn("modbusSkillsContinue", sequencer["func"])
        self.assertIn('schema_version: "capture/v1"', capture["func"])
        self.assertIn("expected_request_ids", capture["func"])
        self.assertIn("expected_unit_ids", capture["func"])
        self.assertIn("request_id", capture["func"])
        self.assertIn("MODBUS_CAPTURE_PATH", capture["func"])
        self.assertIn("runtime_metadata", capture["func"])
        self.assertIn("queue_depth: 0", capture["func"])
        self.assertIn("raw_words", capture["func"])
        self.assertIn("response_time_ms", capture["func"])
        self.assertIn("msg.modbusSkillsFinalize", capture["func"])
        self.assertIn("return [null,", capture["func"])
        self.assertIn("action === 'cancel'", sequencer["func"])
        self.assertIn("modbusSkillsRetry", sequencer["func"])
        self.assertIn("retry_limit: 1", sequencer["func"])
        self.assertIn("finalize('drained')", sequencer["func"])
        continuation = next(node for node in flow if node.get("name") == "07a Continue plan")
        self.assertIn(continuation["id"], capture["wires"][1])
        self.assertIn(sequencer["id"], continuation["wires"][0])
        self.assertEqual(sink["id"], capture["wires"][0][0])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to execute generated functions")
    def test_generated_campaign_executes_retry_drain_and_cancel_states(self) -> None:
        canonical_map, read_plan = inputs()
        flow = json.loads(artifact_text(export_node_red(canonical_map, read_plan), "flow.json"))
        sequencer = next(
            node["func"]
            for node in flow
            if node.get("type") == "function" and node.get("name") == "02 Sequence read blocks"
        )
        capture = next(
            node["func"]
            for node in flow
            if node.get("type") == "function" and node.get("name") == "07 Build capture/v1"
        )
        harness = f"""
const store = new Map();
const flow = {{get: (key) => store.get(key), set: (key, value) => store.set(key, value)}};
const env = {{get: () => null}};
const seq = (msg) => Function('msg', 'flow', 'env', {json.dumps(sequencer)})(msg, flow, env);
const sink = (msg) => Function('msg', 'flow', 'env', {json.dumps(capture)})(msg, flow, env);
let routed = seq({{payload: {{action: 'start'}}}});
let request = routed.find(Boolean).modbusSkillsRequest;
let response = sink({{modbusSkillsRequest: request, modbusSkillsReadError: {{reason: 'timeout'}}}})[1];
if (!response.modbusSkillsRetry || store.get('modbusSkillsActiveBlockId') !== null) throw new Error('retry was not released');
routed = seq(response);
request = routed.find(Boolean).modbusSkillsRequest;
if (request.attempt !== 1) throw new Error('retry attempt was not bounded');
response = sink({{modbusSkillsRequest: request, modbusSkillsReadError: {{reason: 'timeout'}}}})[1];
if (!response.modbusSkillsContinue || response.modbusSkillsRetry) throw new Error('second failure did not advance');
routed = seq(response);
if (routed.at(-1).payload.state !== 'drained') throw new Error('campaign did not drain');
seq({{payload: {{action: 'start'}}}});
routed = seq({{payload: {{action: 'cancel'}}}});
if (routed.at(-1).payload.state !== 'cancelled' || store.get('modbusSkillsRunning')) throw new Error('campaign did not cancel');
"""
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    def test_probe_flow_accepts_unresolved_decoding(self) -> None:
        raw_point = point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map, read_plan = inputs(raw_point)
        read_plan.pop("input_hashes")
        result = export_node_red(canonical_map, read_plan, mode="probe")
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        sequencer = next(node["func"] for node in flow if node.get("name") == "02 Sequence read blocks")
        capture = next(node["func"] for node in flow if node.get("name") == "07 Build capture/v1")
        self.assertIn("const retryLimit = 0;", sequencer)
        self.assertIn("retry_limit: 0", capture)
        self.assertIn('"datatype":null', sequencer)

    @unittest.skipUnless(shutil.which("node"), "Node.js required")
    def test_probe_failure_drains_without_a_second_physical_read(self) -> None:
        canonical, plan = inputs()
        flow_nodes = json.loads(artifact_text(export_node_red(canonical, plan, mode="probe"), "flow.json"))
        seq = next(node["func"] for node in flow_nodes if node.get("name") == "02 Sequence read blocks")
        cap = next(node["func"] for node in flow_nodes if node.get("name") == "07 Build capture/v1")
        harness = f"""
const store = new Map();
const flow = {{get: k => store.get(k), set: (k,v) => store.set(k,v)}};
const env = {{get: () => null}};
const seq = msg => Function('msg','flow','env', {json.dumps(seq)})(msg, flow, env);
const cap = msg => Function('msg','flow','env', {json.dumps(cap)})(msg, flow, env);
const request = seq({{payload: {{action:'start'}}}}).find(Boolean).modbusSkillsRequest;
if (request.max_attempts !== 1) throw Error('more than one attempt allowed');
const response = cap({{modbusSkillsRequest:request, modbusSkillsReadError:{{reason:'timeout'}}}})[1];
if (response.modbusSkillsRetry) throw Error('probe retry requested');
const result = seq(response);
if (result.some(x => x && x.modbusSkillsRequest)) throw Error('second physical read emitted');
if (result.at(-1).payload.state !== 'drained') throw Error('probe did not stop');
"""
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, timeout=5)

    def test_probe_one_read_feeds_all_four_32_bit_byte_order_candidates(self) -> None:
        raw_point = point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map, read_plan = inputs(raw_point)
        result = export_node_red(canonical_map, read_plan, mode="probe")
        flow = json.loads(artifact_text(result, "flow.json"))
        injects = [node for node in flow if node["type"] == "inject"]
        reads = [node for node in flow if node["type"] == "modbus-flex-getter"]
        derives = [
            node
            for node in flow
            if node["type"] == "function"
            and node["name"].startswith("05 Decode points")
        ]

        self.assertEqual(1, len(injects))
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(derives))
        self.assertEqual("", injects[0]["repeat"])
        self.assertEqual("manual-start", injects[0]["modbusSkillsRole"])
        self.assertFalse(injects[0]["once"])
        sequencer = next(node for node in flow if node.get("name") == "02 Sequence read blocks" and node["type"] == "function")
        self.assertEqual(sequencer["id"], injects[0]["wires"][0][0])
        watchdog = next(node for node in flow if node["type"] == "trigger")
        self.assertIn(reads[0]["id"], sequencer["wires"][0])
        self.assertIn(watchdog["id"], sequencer["wires"][0])
        reset = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"] == "Retry / watchdog handoff"
        )
        read_error = next(node for node in flow if node.get("name") == "03a Read error lane")
        gate = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"] == "04 Validate response"
        )
        terminal = next(
            node for node in flow
            if node.get("name") == "06 Terminal gate"
        )
        error_debug = next(
            node for node in flow if node.get("name") == "Errors → Debug"
        )
        self.assertEqual([gate["id"]], reads[0]["wires"][0])
        self.assertIn(read_error["id"], reads[0]["wires"][1])
        self.assertIn(terminal["id"], read_error["wires"][0])
        self.assertIn(derives[0]["id"], gate["wires"][0])
        self.assertEqual([[terminal["id"]]], derives[0]["wires"])
        self.assertEqual([[reset["id"]], [error_debug["id"]]], terminal["wires"])
        self.assertIn("request.block_id !== active", terminal["func"])
        self.assertIn("duplicate-raw-response", terminal["func"])
        self.assertIn("modbusSkillsActiveBlockId', null", terminal["func"])
        self.assertNotIn(reset["id"], reads[0]["wires"][0])
        self.assertNotIn(watchdog["id"], reads[0]["wires"][0])
        self.assertIn("msg.reset = true", reset["func"])
        self.assertIn(watchdog["id"], reset["wires"][0])
        self.assertIn("return [null, msg]", gate["func"])
        self.assertIn("read-wrong-length", gate["func"])
        self.assertIn("msg.error || msg.modbusError", gate["func"])
        self.assertIn(
            "values.length === expected.expected_quantity", gate["func"]
        )
        self.assertEqual({"action": "start"}, json.loads(injects[0]["payload"]))
        self.assertFalse(any(node["type"] == "modbus-read" for node in flow))
        self.assertFalse(any("write" in node["type"].lower() for node in flow))
        self.assertTrue(next(node for node in flow if node["type"] == "tab")["disabled"])
        function = derives[0]["func"]
        layouts = re.findall(
            r"^  (ABCD|BADC|CDAB|DCBA): Object\.freeze",
            function,
            flags=re.MULTILINE,
        )
        self.assertEqual(["ABCD", "BADC", "CDAB", "DCBA"], layouts)
        self.assertIn("const rawValues = Object.freeze(candidate.slice())", function)
        self.assertIn("derived.byte_order_candidates", function)
        self.assertIn("buffer.readUInt32BE(0)", function)
        self.assertIn("buffer.readInt32BE(0)", function)
        self.assertIn("buffer.readFloatBE(0)", function)
        self.assertNotIn("winner", function.lower())
        self.assertNotIn("selected_byte_order", function.lower())

        manifest = json.loads(artifact_text(result, "manifest.json"))
        self.assertEqual(
            ["ABCD", "BADC", "CDAB", "DCBA"],
            manifest["probe_byte_order_candidates"],
        )
        self.assertEqual(
            ["uint32", "int32", "float32"],
            manifest["probe_candidate_interpretations"],
        )
        self.assertEqual(
            "one-read-immutable-raw-words",
            manifest["probe_candidate_source"],
        )
        self.assertFalse(manifest["probe_polling"])
        self.assertEqual("sequenced-manual-plan", manifest["probe_trigger_type"])
        self.assertEqual(1, manifest["probe_trigger_nodes"])
        self.assertEqual("modbus-flex-getter", manifest["read_node_type"])
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", manifest["environment"])

    def test_final_flow_uses_manual_one_shot_read_nodes(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan, mode="final")
        self.assertEqual("generated", result.status)
        flow = json.loads(artifact_text(result, "flow.json"))
        reads = [node for node in flow if node["type"] == "modbus-flex-getter"]
        injects = [node for node in flow if node["type"] == "inject"]
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(injects))
        sequencer = next(node for node in flow if node.get("name") == "02 Sequence read blocks" and node["type"] == "function")
        self.assertEqual(sequencer["id"], injects[0]["wires"][0][0])
        watchdog = next(node for node in flow if node["type"] == "trigger")
        self.assertIn(reads[0]["id"], sequencer["wires"][0])
        self.assertIn(watchdog["id"], sequencer["wires"][0])
        reset = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"] == "Retry / watchdog handoff"
        )
        read_error = next(node for node in flow if node.get("name") == "03a Read error lane")
        gate = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"] == "04 Validate response"
        )
        terminal = next(
            node for node in flow
            if node.get("name") == "06 Terminal gate"
        )
        derive = next(
            node
            for node in flow
            if node["type"] == "function"
            and node["name"] == "05 Decode points"
        )
        error_debug = next(
            node for node in flow if node.get("name") == "Errors → Debug"
        )
        self.assertEqual([gate["id"]], reads[0]["wires"][0])
        self.assertIn(read_error["id"], reads[0]["wires"][1])
        self.assertIn(terminal["id"], read_error["wires"][0])
        self.assertIn(derive["id"], gate["wires"][0])
        self.assertEqual([[terminal["id"]]], derive["wires"])
        self.assertEqual([[reset["id"]], [error_debug["id"]]], terminal["wires"])
        self.assertIn("duplicate-raw-response", terminal["func"])
        self.assertNotIn(reset["id"], reads[0]["wires"][0])
        self.assertNotIn(watchdog["id"], reads[0]["wires"][0])
        self.assertIn("msg.reset = true", reset["func"])
        self.assertEqual("5", injects[0]["repeat"])
        self.assertEqual("live-poll", injects[0]["modbusSkillsRole"])
        self.assertEqual("", injects[0]["crontab"])
        self.assertFalse(injects[0]["once"])
        self.assertFalse(any(node["type"] == "modbus-read" for node in flow))
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", json.dumps(flow))
        manifest = json.loads(artifact_text(result, "manifest.json"))
        self.assertEqual("sequenced-live-poll", manifest["trigger_type"])
        self.assertEqual(1, manifest["trigger_nodes"])
        self.assertTrue(manifest["scheduled_polling"])
        self.assertNotIn("MODBUS_POLL_INTERVAL_MS", manifest["environment"])

    def test_final_flow_holds_an_unconfirmed_byte_order(self) -> None:
        unresolved = point(byte_order=None, byte_order_confirmed=False)
        canonical_map, read_plan = inputs(unresolved)
        result = export_node_red(canonical_map, read_plan, mode="final")
        self.assertEqual("held", result.status)
        self.assertFalse(result.artifacts)
        self.assertIn(
            "POINT_BYTE_ORDER_UNRESOLVED",
            {finding.code for finding in result.findings},
        )

    def test_explicit_point_trace_must_match_block_scope(self) -> None:
        canonical_map, base_plan = inputs()
        cases = (
            ({"route_id": "other-route"}, "BLOCK_POINT_ROUTE_MISMATCH"),
            ({"unit_id": 2}, "BLOCK_POINT_UNIT_MISMATCH"),
            (
                {"area": "input-register", "function_code": 4},
                "BLOCK_POINT_AREA_MISMATCH",
            ),
        )
        for updates, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                read_plan = json.loads(json.dumps(base_plan))
                read_plan["requests"][0].update(updates)
                result = export_node_red(canonical_map, read_plan)
                self.assertEqual("held", result.status)
                self.assertIn(expected_code, {finding.code for finding in result.findings})

    def test_environment_prefix_collision_holds_generation(self) -> None:
        first = point(logical_point_id="first", route_id="line-a")
        second = point(
            logical_point_id="second",
            route_id="line_a",
            protocol_offset=110,
        )
        canonical_map, read_plan = inputs(first, second)
        result = export_node_red(canonical_map, read_plan)
        self.assertEqual("held", result.status)
        self.assertIn(
            "NODE_RED_ENV_PREFIX_COLLISION",
            {finding.code for finding in result.findings},
        )

    def test_output_is_deterministic(self) -> None:
        canonical_map, read_plan = inputs()
        left = export_node_red(canonical_map, read_plan)
        right = export_node_red(canonical_map, read_plan)
        self.assertEqual(
            [(artifact.path, artifact.content) for artifact in left.artifacts],
            [(artifact.path, artifact.content) for artifact in right.artifacts],
        )

    def test_target_manifest_matches_rights_safe_golden_fixture(self) -> None:
        canonical_map, read_plan = inputs()
        result = export_node_red(canonical_map, read_plan)
        expected = (FIXTURES / "node-red-manifest.json").read_text(encoding="utf-8")
        self.assertEqual(expected, artifact_text(result, "manifest.json"))


if __name__ == "__main__":
    unittest.main()
